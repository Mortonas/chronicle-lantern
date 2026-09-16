from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.club_generation as club_generation
from core.club_cache import ClubCacheService
from core.club_generation import (
    build_npc_quick_panel,
    ClubGenerationError,
    ClubGenerationService,
    build_attendee_context,
    build_dashboard_skeleton,
    build_npc_panel_context,
    build_npc_panel_skeleton,
    build_source_map,
    clean_display_text,
    cluster_social_map,
    dashboard_from_skeleton,
    dashboard_prompt_context,
    dedupe_display_strings,
    dedupe_grounded_facts,
    grounded_fact_key,
    is_dashboard_pressure,
    npc_panel_from_skeleton,
    rank_connections,
    relationship_digest_for,
    safe_debug_json,
    validate_dashboard,
    validate_npc_panel,
)
from core.club_identity import NpcIdentityRegistry
from core.club_index import ClubIndexStore, build_club_index
from core.club_models import NpcIdentity


class CanonicalGenerationService(ClubGenerationService):
    """Exercise the retained canonical pipeline independently of whole-sheet AI.

    Public orchestration, partial results and exact rumor parity are tested in
    test_club_prep.py; these existing tests retain their original fact oracles.
    """
    def build_event_result(self, *args, **kwargs):
        return self._build_canonical_event_result(*args, **kwargs)


class FakeProvider:
    def __init__(self, responses: list[dict | str]) -> None:
        self.responses = [json.dumps(item) if isinstance(item, dict) else item for item in responses]
        self.prompts: list[str] = []
        self.request_options: list[dict | None] = []

    def generate_from_messages(self, messages, *, strip_response=True, request_options=None):
        self.prompts.append(messages[-1]["content"])
        self.request_options.append(request_options)
        return self.responses.pop(0)


class RaisingProvider:
    def __init__(self, message: str = "provider failed") -> None:
        self.message = message
        self.prompts: list[str] = []
        self.request_options: list[dict | None] = []

    def generate_from_messages(self, messages, *, strip_response=True, request_options=None):
        self.prompts.append(messages[-1]["content"])
        self.request_options.append(request_options)
        raise RuntimeError(self.message)


class TimeoutProvider:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.request_options: list[dict | None] = []

    def generate_from_messages(self, messages, *, strip_response=True, request_options=None):
        self.prompts.append(messages[-1]["content"])
        self.request_options.append(request_options)
        raise TimeoutError("provider timed out")


class SkeletonEchoProvider:
    def __init__(self, *, invalid_first: dict | None = None, panel_summaries: list[str] | None = None) -> None:
        self.invalid_first = invalid_first
        self.panel_summaries = list(panel_summaries or [])
        self.prompts: list[str] = []
        self.request_options: list[dict | None] = []
        self._used_invalid = False

    def generate_from_messages(self, messages, *, strip_response=True, request_options=None):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        self.request_options.append(request_options)
        if self.invalid_first is not None and not self._used_invalid:
            self._used_invalid = True
            return json.dumps(self.invalid_first)
        skeleton = _skeleton_from_prompt(prompt)
        if skeleton.get("kind") == "dashboard":
            return json.dumps(dashboard_from_skeleton(skeleton, first_impression="The bass shudders through old brick."))
        response = _panel_presentation_response(skeleton)
        if self.panel_summaries and response["presentation"]["people_here"]:
            response["presentation"]["people_here"][0]["text"] = self.panel_summaries.pop(0)
        return json.dumps(response)


class ScriptedSkeletonProvider:
    def __init__(self, builders) -> None:
        self.builders = list(builders)
        self.prompts: list[str] = []
        self.request_options: list[dict | None] = []

    def generate_from_messages(self, messages, *, strip_response=True, request_options=None):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        self.request_options.append(request_options)
        skeleton = _skeleton_from_prompt(prompt)
        builder = self.builders.pop(0)
        response = builder(skeleton) if callable(builder) else builder
        return json.dumps(response)


def _assert_club_json_request_options(options: dict | None) -> None:
    assert options == {
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }


def _skeleton_from_prompt(prompt: str) -> dict:
    marker = "Skeleton JSON:\n"
    assert marker in prompt
    skeleton, _end = json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])
    return skeleton


def _clone_json(value):
    return json.loads(json.dumps(value))


def _panel_presentation_response(skeleton: dict) -> dict:
    conversation = skeleton.get("conversation") or {}
    tonight = skeleton.get("tonight") or {}
    portrayal_basis = skeleton.get("portrayal_basis")
    current_read_basis = skeleton.get("current_read_basis")
    demeanor = current_read_basis.get("demeanor") if isinstance(current_read_basis, dict) else None
    has_demeanor_basis = bool(
        (
            isinstance(portrayal_basis, dict)
            and str(portrayal_basis.get("demeanor") or "").strip()
        )
        or (
            isinstance(demeanor, dict)
            and str(demeanor.get("value") or "").strip()
            and any(
                isinstance(source, dict) and str(source.get("source_id") or "").strip()
                for source in demeanor.get("sources") or []
            )
        )
    )
    play_cue = (
        "Keep the delivery clipped and watchful."
        if has_demeanor_basis or skeleton.get("personality")
        else ""
    )

    def entries(items, prefix):
        return [
            {"item_id": item["item_id"], "text": f"{prefix} {index + 1}."}
            for index, item in enumerate(items or [])
        ]

    focus = tonight.get("current_desire")
    hook = skeleton.get("interesting_detail")
    return {
        "npc_id": skeleton["npc_id"],
        "presentation": {
            "play_cue": play_cue,
            "agenda": {"item_id": focus["item_id"], "text": "Advance the grounded agenda tonight."} if focus else None,
            "people_here": entries(skeleton.get("people_here"), "Relationship cue"),
            "if_approached": entries(conversation.get("likely_subjects"), "Reaction cue"),
            "keep_guarded": entries(conversation.get("sensitive"), "Guarded cue"),
            "hook": {"item_id": hook["item_id"], "text": "Put the grounded hook in motion."} if hook else None,
        },
    }


def _assert_no_ai_unavailable_marker(value) -> None:
    assert "[AI unavailable" not in json.dumps(value)


def _valid_dashboard(source_id: str, a_id: str, b_id: str) -> dict:
    grounded = {
        "summary": "Damien distrusts Annabelle's political maneuvering.",
        "characters": [a_id, b_id],
        "sources": [{"source_id": source_id}],
    }
    return {
        "event": {"venue": "The Lantern Room", "event_type": "social gathering", "mood": "tense"},
        "first_impression": "The bass shudders through old brick.",
        "social_map": [{"location": "Bar", "npc_ids": [a_id, b_id], "summary": "A tense conversation."}],
        "notable_details": ["Rain beads on black glass."],
        "rumors": [],
        "possible_drama": [grounded],
        "interesting_connections": [grounded],
        "unresolved_business": [],
        "opportunities": [],
        "top_connections": [grounded],
        "background_details": ["No timeline is planned."],
    }


def test_attendee_context_filters_relationships_to_attendees(tmp_path: Path) -> None:
    cache = ClubCacheService(tmp_path / ".club-cache")
    registry = NpcIdentityRegistry(cache, vault_root=tmp_path)
    store = ClubIndexStore(cache)
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    absent = tmp_path / "Absent.md"
    damien.write_text("### Relationships\n- **[[Annabelle]] (Distrust):** Political maneuvering.\n- [[Absent]] (Enemy): Gone.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    absent.write_text("Affiliation: Executives\n", encoding="utf-8")

    identities = [registry.get_or_create(damien), registry.get_or_create(annabelle)]
    indexes = [store.get_or_build(identities[0], damien), store.get_or_build(identities[1], annabelle)]
    context = build_attendee_context(identities, indexes)

    assert len(context["attendee_relationships"]) == 1
    assert set(context["attendee_relationships"][0]["characters"]) == {identities[0].npc_id, identities[1].npc_id}


def test_source_map_backfills_resolved_relationship_target_ids(tmp_path: Path) -> None:
    cache = ClubCacheService(tmp_path / ".club-cache")
    registry = NpcIdentityRegistry(cache, vault_root=tmp_path)
    store = ClubIndexStore(cache)
    son = tmp_path / "Son.md"
    alexa = tmp_path / "Alexa Santos.md"
    son.write_text(
        "### Relationships\n"
        "- [[Alexa Santos]] (Toy): Young Oracle he manipulates and may commit grave misconduct against.\n",
        encoding="utf-8",
    )
    alexa.write_text("Affiliation: Oracle\n", encoding="utf-8")
    identities = [registry.get_or_create(son), registry.get_or_create(alexa)]
    indexes = [store.get_or_build(identities[0], son), store.get_or_build(identities[1], alexa)]

    source_map = build_source_map(indexes, identities=identities)
    relationship_source_id = indexes[0].relationships[0].sources[0].source_id

    assert indexes[0].relationships[0].target_npc_id == ""
    assert source_map[relationship_source_id]["target_name"] == "Alexa Santos"
    assert source_map[relationship_source_id]["target_npc_id"] == identities[1].npc_id


def test_unresolved_relationship_targets_stay_out_of_dashboard_sources(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Distrust): Political maneuvering.\n"
        "- [[Absent Contact]] (Blackmail): This absent leverage should not enter the room.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)
    skeleton = build_dashboard_skeleton(result)
    debug_payload = result.to_debug_dict()
    skeleton_text = json.dumps(skeleton)

    assert "Political maneuvering" in skeleton_text
    assert "absent leverage" not in skeleton_text
    assert all("Absent Contact" != entry.get("target_name") for entry in skeleton["source_map"].values())
    assert all("Absent Contact" != entry.get("target_name") for entry in debug_payload["source_map"].values())


def test_grounded_facts_dedupe_by_composite_identity_key() -> None:
    first = {"type": "distrust", "characters": ["a", "b"], "summary": "One", "sources": [{"source_id": "s1"}]}
    duplicate = {"type": "distrust", "characters": ["a", "b"], "summary": "Different wording", "sources": [{"source_id": "s1"}]}
    different_source = {"type": "distrust", "characters": ["a", "b"], "summary": "One", "sources": [{"source_id": "s2"}]}

    assert grounded_fact_key(first) == ("a", "b", "distrust", "s1")
    assert dedupe_grounded_facts([first, duplicate, different_source]) == [first, different_source]


def test_directed_relationships_do_not_merge_opposing_viewpoints(tmp_path: Path) -> None:
    alexa = tmp_path / "Alexa Santos.md"
    son = tmp_path / "Son.md"
    alexa.write_text(
        "### Relationships\n"
        "- **[[Son]] (Mentor 2, Revulsion):** Alexa finds it disturbing that Son frequently reaches out.\n",
        encoding="utf-8",
    )
    son.write_text(
        "### Relationships\n"
        "- **[[Alexa Santos]] (Toy):** Young Oracle he manipulates and may commit grave misconduct against.\n",
        encoding="utf-8",
    )
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(alexa), str(son)], seed=1, use_ai=False)
    alexa_id = result.event.attendee_ids[0]

    skeleton = build_npc_panel_skeleton(result, alexa_id)
    text = json.dumps(skeleton)

    assert "Alexa finds it disturbing" in text
    assert "Toy" not in json.dumps(skeleton["people_here"])
    assert "commit grave misconduct against" in json.dumps([skeleton["conversation"]["sensitive"], skeleton["interesting_detail"]])
    assert "commit grave misconduct against" not in json.dumps(skeleton["people_here"])
    assert all(item["source_npc_id"] == alexa_id for item in skeleton["people_here"])


def test_non_relationship_attendee_mentions_are_topics_not_people_here(tmp_path: Path) -> None:
    alexa = tmp_path / "Alexa Santos.md"
    kevin = tmp_path / "Kevin Jackson.md"
    alexa.write_text(
        "### Plots and Schemes\n"
        "- **Corrosive Network:** As a Hound for [[Kevin Jackson]], Alexa coordinates court attacks.\n",
        encoding="utf-8",
    )
    kevin.write_text("Affiliation: Executives\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(alexa), str(kevin)], seed=1, use_ai=False)

    skeleton = build_npc_panel_skeleton(result, result.event.attendee_ids[0])
    conversation = skeleton["conversation"]["likely_subjects"]

    assert skeleton["people_here"] == []
    assert conversation == []
    focus = skeleton["tonight"]["current_desire"]
    assert "Corrosive Network" in focus["summary"]
    assert focus["fact_scope"] == "mention"
    assert result.event.attendee_ids[1] in focus["mentioned_npc_ids"]


def test_display_string_dedupe_uses_normalized_exact_equality() -> None:
    assert dedupe_display_strings(["  [[Annabelle]]  ", "Annabelle", "Annabelle's debt"]) == ["Annabelle", "Annabelle's debt"]


def test_wiki_links_and_simple_emphasis_clean_to_plain_text() -> None:
    assert clean_display_text("**[[Annabelle|Anne]]** distrusts `Damien` and _Critias_.") == "Anne distrusts Damien and Critias."


def test_club_index_extracts_duncan_style_sheet_sections(tmp_path: Path) -> None:
    sheet = tmp_path / "Duncan MacTavish.md"
    sheet.write_text(
        "![[portrait.png]]\n"
        "# Sgt. Duncan MacTavish\n"
        "**Epitaph:** _SAS Soldier and Independent Enforcer_\n"
        "> **Affiliation:** [[Travelers]]\n"
        "> **Faction:** [[Independent Movement]]\n"
        "> **Group:** [[Gary Independents]]\n"
        "> **Tags:** #Travelers #Independent #Enforcer #NPC #Revolutionary\n"
        "### At a Glance – Motives\n"
        "- **Primary Drive:** Uphold the Independent cause through any means necessary\n"
        "- **Secondary Aim:** Prevent the Council from consolidating control in Riverton\n"
        "- **Pressure Point:** Haunted by the execution of Christine's family\n"
        "### Plots and Schemes\n"
        "- **High-Profile Targets:** Plans to sabotage Strategists-Council negotiations.\n"
        "### Dependents and Tools\n"
        "- **Christine Summers (Retainers 1):** Teen he trains in survival and combat.\n"
        "### Relationships\n"
        "- **[[Bobby Weatherbottom]] (Ally):** Trusted informant, treated with rare warmth.\n"
        "- **[[Maldavis]] (Enemy):** Suspects her of betrayal.\n"
        "### Whispers\n"
        "- **Rumor:** Duncan is the last true soldier of the Movement.\n"
        "### Mask and Mien\n"
        "- **Appearance:** Gruff and disheveled, always in tactical gear.\n"
        "### Background Details\n"
        "- **Ambition:** Burn down the Council's grip on Riverton\n"
        "### Advantages & Flaws\n"
        "- **Flaw – Enemy (Maldavis):** Distrusts and resents this political rival.\n",
        encoding="utf-8",
    )
    identity = NpcIdentity("npc_duncan", str(sheet), "Duncan MacTavish", (), "fingerprint")

    index = build_club_index(identity, sheet, sheet_revision_hash="revision")

    assert index.affiliation == "Travelers"
    assert index.faction == "Independent Movement"
    assert "Gary Independents" in index.roles
    assert "SAS Soldier" in index.roles
    assert "Independent Enforcer" in index.roles
    assert any("Uphold the Independent cause" in fact.summary for fact in index.current_goals)
    assert any("sabotage Strategists" in fact.summary for fact in index.current_goals)
    assert any("Burn down the Council" in fact.summary for fact in index.current_goals)
    assert any("Christine's family" in fact.summary for fact in index.grievances)
    assert any(fact.target_name == "Bobby Weatherbottom" and fact.fact_type == "ally" for fact in index.relationships)
    assert any(fact.target_name == "Maldavis" and fact.fact_type == "enemy" for fact in index.relationships)
    assert any(fact.target_name == "Christine Summers" and fact.fact_type == "retainer" for fact in index.relationships)
    assert any("last true soldier" in fact.summary for fact in index.rumor_notes)
    assert index.appearance is not None
    assert index.appearance.value == "Gruff and disheveled, always in tactical gear."
    assert index.personality_tags == []


def test_story_hooks_are_indexed_separately_from_unresolved_business(tmp_path: Path) -> None:
    sheet = tmp_path / "Duncan.md"
    sheet.write_text(
        "### Story Hooks\n"
        "- **Marked Man:** Someone wants him dead.\n"
        "### Unresolved Business\n"
        "- **Old Debt:** He owes a favor.\n",
        encoding="utf-8",
    )
    identity = NpcIdentity("npc_duncan", str(sheet), "Duncan", (), "fingerprint")

    index = build_club_index(identity, sheet, sheet_revision_hash="revision")

    assert any("Marked Man" in fact.summary for fact in index.story_hooks)
    assert not any("Marked Man" in fact.summary for fact in index.unresolved_business)
    assert any("Old Debt" in fact.summary for fact in index.unresolved_business)


def test_index_drops_decorative_non_semantic_lines(tmp_path: Path) -> None:
    sheet = tmp_path / "Decorated.md"
    sheet.write_text(
        "### Rumors\n"
        "![[portrait.png]]\n"
        "---\n"
        "-\n"
        "- ...\n"
        "- **Rumor:** A real rumor survives.\n",
        encoding="utf-8",
    )
    identity = NpcIdentity("npc_decorated", str(sheet), "Decorated", (), "fingerprint")

    index = build_club_index(identity, sheet, sheet_revision_hash="revision")

    assert [fact.summary for fact in index.rumor_notes] == ["Rumor: A real rumor survives."]


def test_dashboard_skeleton_excludes_non_attendee_relationships(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    absent = tmp_path / "Absent.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Distrust): Political maneuvering.\n"
        "- [[Absent]] (Enemy): Not in the room.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    absent.write_text("Affiliation: Executives\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    skeleton = build_dashboard_skeleton(result)
    text = json.dumps(skeleton)

    assert "Political maneuvering" in text
    assert "Not in the room" not in text


def test_dashboard_skeleton_only_elevates_attendee_facing_facts(tmp_path: Path) -> None:
    abraham = tmp_path / "Abraham DuSable.md"
    alexa = tmp_path / "Alexa Santos.md"
    aluc = tmp_path / "Aluc Romas de Leon.md"
    abraham.write_text(
        "### Whispers\n"
        "- **Eyes of the Pyramid:** Some believe DuSable returned to monitor the Scholars.\n"
        "- **Court Watch:** DuSable is asking [[Alexa Santos]] who still speaks for Jackson.\n"
        "### Plots and Schemes\n"
        "- **Unification:** DuSable wants to unify Affiliation Scholars in Riverton.\n"
        "- **Pressure Alexa:** DuSable wants [[Alexa Santos]] to carry a warning.\n",
        encoding="utf-8",
    )
    alexa.write_text(
        "### Relationships\n"
        "- [[Aluc Romas de Leon]] (Paramour): Blood-bound partner.\n",
        encoding="utf-8",
    )
    aluc.write_text("Affiliation: Oracle\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(abraham), str(alexa), str(aluc)], seed=1, use_ai=False)

    skeleton = build_dashboard_skeleton(result)
    text = json.dumps(skeleton)

    assert "Eyes of the Pyramid" in json.dumps(skeleton["rumors"])
    assert "Unification" not in text
    assert "Court Watch" in text
    assert "Pressure Alexa" in text
    assert "Blood-bound partner" not in json.dumps(skeleton["opportunities"])
    rumor = next(item for item in skeleton["rumors"] if "Eyes of the Pyramid" in item["summary"])
    assert rumor["source_npc_id"] == result.event.attendee_ids[0]
    assert rumor["target_npc_id"] is None
    assert rumor["mentioned_npc_ids"] == []


def test_self_scoped_non_rumor_does_not_enter_rumor_section(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Plots and Schemes\n"
        "- **Private Ambition:** Damien wants private leverage over the Delegate.\n"
        "### Unresolved Business\n"
        "- **Old Accounting:** Damien still owes a private obligation.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))

    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)
    skeleton = build_dashboard_skeleton(result)

    assert skeleton["rumors"] == []
    assert result.event.dashboard["rumors_in_circulation"] == []


def test_rumor_links_populate_mentions_without_targets_or_fuzzy_matches(tmp_path: Path) -> None:
    mara = tmp_path / "Mara Voss.md"
    alexa = tmp_path / "Alexa Santos.md"
    darius = tmp_path / "Darius Bell.md"
    erin = tmp_path / "Erin.md"
    ellis = tmp_path / "Ellis.md"
    mara.write_text(
        "### Whispers\n"
        "- **Court Watch:** [[SPARK]] and [[Baron]] were seen leaving [[Lore/Locations/Clubs/Blood Disco]].\n"
        "- **Ambiguous Twin:** [[Twin]] is promising favors after midnight.\n"
        "- **No Substring:** The staff at [[Lore/Locations/Clubs/Blood Disco]] are almost revolting.\n"
        "- **Repeat Spark:** [[SPARK]] warned [[SPARK]] near [[   ]].\n",
        encoding="utf-8",
    )
    alexa.write_text("Aliases: Spark\n", encoding="utf-8")
    darius.write_text("Aliases: Baron\n", encoding="utf-8")
    erin.write_text("Aliases: Twin\n", encoding="utf-8")
    ellis.write_text("Aliases: Twin\n", encoding="utf-8")
    identities = [
        NpcIdentity("mara", str(mara), "Mara Voss", (), "mara-rev"),
        NpcIdentity("alexa", str(alexa), "Alexa Santos", ("Spark",), "alexa-rev"),
        NpcIdentity("darius", str(darius), "Darius Bell", ("Baron",), "darius-rev"),
        NpcIdentity("erin", str(erin), "Erin", ("Twin",), "erin-rev"),
        NpcIdentity("ellis", str(ellis), "Ellis", ("Twin",), "ellis-rev"),
    ]
    indexes = [build_club_index(identity, Path(identity.current_path)) for identity in identities]
    context = build_attendee_context(identities, indexes)
    id_by_name = {identity.display_name: identity.npc_id for identity in identities}
    rumors = [item for item in context["attendee_facts"] if item.get("type") == "rumor"]

    assert all(item.get("target_npc_id") is None for item in rumors)
    linked = next(item for item in rumors if "Court Watch" in item["summary"])
    assert linked["mentioned_npc_ids"] == [id_by_name["Alexa Santos"], id_by_name["Darius Bell"]]
    assert linked["link_targets"] == ["SPARK", "Baron", "Lore/Locations/Clubs/Blood Disco"]
    ambiguous = next(item for item in rumors if "Ambiguous Twin" in item["summary"])
    assert ambiguous["mentioned_npc_ids"] == []
    lore_only = next(item for item in rumors if "No Substring" in item["summary"])
    assert lore_only["mentioned_npc_ids"] == []
    repeat = next(item for item in rumors if "Repeat Spark" in item["summary"])
    assert repeat["mentioned_npc_ids"] == [id_by_name["Alexa Santos"]]
    assert repeat["link_targets"] == ["SPARK"]
    source_map = build_source_map(indexes, identities=identities)
    assert all(entry["target_npc_id"] is None for entry in source_map.values() if entry["fact_type"] == "rumor")


def test_rumor_attendee_lookup_ambiguity_requires_distinct_npc_ids(tmp_path: Path) -> None:
    mara = tmp_path / "Mara Voss.md"
    display_claim = tmp_path / "Display Claim.md"
    alias_claim = tmp_path / "Alias Claim.md"
    stem_claim = tmp_path / "Signal.md"
    mara.write_text(
        "### Whispers\n"
        "- **Collision:** [[Signal]] is selling secrets near the west stairs.\n",
        encoding="utf-8",
    )
    display_claim.write_text("Affiliation: Artists\n", encoding="utf-8")
    alias_claim.write_text("Aliases: Signal\n", encoding="utf-8")
    stem_claim.write_text("Affiliation: Executives\n", encoding="utf-8")
    identities = [
        NpcIdentity("mara", str(mara), "Mara Voss", (), "mara-rev"),
        NpcIdentity("display", str(display_claim), "Signal", (), "display-rev"),
        NpcIdentity("alias", str(alias_claim), "Alias Claim", ("Signal",), "alias-rev"),
        NpcIdentity("stem", str(stem_claim), "Stem Claim", (), "stem-rev"),
    ]
    indexes = [build_club_index(identity, Path(identity.current_path)) for identity in identities]

    context = build_attendee_context(identities, indexes)
    rumor = next(item for item in context["attendee_facts"] if "Collision" in item["summary"])

    assert rumor["target_npc_id"] is None
    assert rumor["mentioned_npc_ids"] == []
    assert rumor in context["attendee_facts"]


def test_rumor_same_npc_duplicate_identity_forms_are_not_ambiguous(tmp_path: Path) -> None:
    mara = tmp_path / "Mara Voss.md"
    damien = tmp_path / "Damien.md"
    mara.write_text(
        "### Whispers\n"
        "- **Harmless Duplicate:** [[  damien  ]] keeps three names on the same ledger.\n",
        encoding="utf-8",
    )
    damien.write_text("Aliases: DAMIEN, Damien\n", encoding="utf-8")
    identities = [
        NpcIdentity("mara", str(mara), "Mara Voss", (), "mara-rev"),
        NpcIdentity("damien", str(damien), "Damien", ("DAMIEN", " Damien "), "damien-rev"),
    ]
    indexes = [build_club_index(identity, Path(identity.current_path)) for identity in identities]

    context = build_attendee_context(identities, indexes)
    rumor = next(item for item in context["attendee_facts"] if "Harmless Duplicate" in item["summary"])

    assert rumor["target_npc_id"] is None
    assert rumor["mentioned_npc_ids"] == ["damien"]


def test_rumor_attendee_lookup_normalizes_only_case_and_whitespace(tmp_path: Path) -> None:
    mara = tmp_path / "Mara Voss.md"
    signal = tmp_path / "The Signal.md"
    dash_signal = tmp_path / "Dash Signal.md"
    mara.write_text(
        "### Whispers\n"
        "- **Whitespace Match:** [[  the   signal  ]] has the courier's keys.\n"
        "- **Punctuation Miss:** [[the-signal]] has the courier's keys.\n",
        encoding="utf-8",
    )
    signal.write_text("Affiliation: Artists\n", encoding="utf-8")
    dash_signal.write_text("Affiliation: Executives\n", encoding="utf-8")
    identities = [
        NpcIdentity("mara", str(mara), "Mara Voss", (), "mara-rev"),
        NpcIdentity("signal", str(signal), "The Signal", (), "signal-rev"),
        NpcIdentity("dash", str(dash_signal), "Dash Signal", (), "dash-rev"),
    ]
    indexes = [build_club_index(identity, Path(identity.current_path)) for identity in identities]

    context = build_attendee_context(identities, indexes)
    rumors = [item for item in context["attendee_facts"] if item["type"] == "rumor"]
    whitespace_match = next(item for item in rumors if "Whitespace Match" in item["summary"])
    punctuation_miss = next(item for item in rumors if "Punctuation Miss" in item["summary"])

    assert whitespace_match["mentioned_npc_ids"] == ["signal"]
    assert punctuation_miss["mentioned_npc_ids"] == []


def test_rumor_attendee_lookup_resolves_unique_stem_and_fails_closed_on_stem_alias_ambiguity(tmp_path: Path) -> None:
    mara = tmp_path / "Mara Voss.md"
    unique_stem = tmp_path / "Silent Courier.md"
    alias_claim = tmp_path / "Alias Claim.md"
    stem_claim = tmp_path / "Night Market.md"
    mara.write_text(
        "### Whispers\n"
        "- **Unique Stem:** [[Silent Courier]] has the package.\n"
        "- **Stem Alias Collision:** [[Night Market]] has the package.\n",
        encoding="utf-8",
    )
    unique_stem.write_text("Affiliation: Outsider\n", encoding="utf-8")
    alias_claim.write_text("Aliases: Night Market\n", encoding="utf-8")
    stem_claim.write_text("Affiliation: Executives\n", encoding="utf-8")
    identities = [
        NpcIdentity("mara", str(mara), "Mara Voss", (), "mara-rev"),
        NpcIdentity("courier", str(unique_stem), "Different Display", (), "courier-rev"),
        NpcIdentity("alias", str(alias_claim), "Alias Claim", ("Night Market",), "alias-rev"),
        NpcIdentity("stem", str(stem_claim), "Stem Claim", (), "stem-rev"),
    ]
    indexes = [build_club_index(identity, Path(identity.current_path)) for identity in identities]

    context = build_attendee_context(identities, indexes)
    rumors = [item for item in context["attendee_facts"] if item["type"] == "rumor"]
    unique_stem_rumor = next(item for item in rumors if "Unique Stem" in item["summary"])
    ambiguous_rumor = next(item for item in rumors if "Stem Alias Collision" in item["summary"])

    assert unique_stem_rumor["mentioned_npc_ids"] == ["courier"]
    assert ambiguous_rumor["mentioned_npc_ids"] == []


def test_cached_index_rehydrates_legacy_rumor_targets_to_none_without_losing_relationship_targets(tmp_path: Path) -> None:
    cache = ClubCacheService(tmp_path / ".club-cache")
    registry = NpcIdentityRegistry(cache, vault_root=tmp_path)
    store = ClubIndexStore(cache)
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    alexa = tmp_path / "Alexa Santos.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Keeps blackmail material about Annabelle's secret private.\n"
        "### Whispers\n"
        "- **Bogus Target:** [[Alexa Santos]] heard Damien keeps a second ledger.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    alexa.write_text("Affiliation: Oracle\n", encoding="utf-8")
    identities = [registry.get_or_create(path) for path in (damien, annabelle, alexa)]
    paths_by_npc_id = {identity.npc_id: path for identity, path in zip(identities, (damien, annabelle, alexa))}
    indexes = [store.get_or_build(identity, paths_by_npc_id[identity.npc_id]) for identity in identities]
    damien_identity = identities[0]
    cached = cache.read_json("indexes", f"{damien_identity.npc_id}.json", default={})
    cached["index"]["rumor_notes"][0]["target_npc_id"] = "bogus-rumor-target"
    cache.write_json("indexes", f"{damien_identity.npc_id}.json", data=cached)
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))

    result = service.build_event_result([str(damien), str(annabelle), str(alexa)], seed=1, use_ai=False)
    rumor_index_fact = result.indexes_by_id[damien_identity.npc_id].rumor_notes[0]
    rumor_context_item = next(item for item in result.attendee_facts if item["type"] == "rumor")
    rumor_source_id = rumor_index_fact.sources[0].source_id
    relationship_source_id = indexes[0].relationships[0].sources[0].source_id
    skeleton = build_dashboard_skeleton(result)

    assert rumor_index_fact.target_npc_id is None
    assert rumor_context_item["target_npc_id"] is None
    assert result.source_map[rumor_source_id]["target_npc_id"] is None
    assert skeleton["source_map"][rumor_source_id]["target_npc_id"] is None
    assert result.source_map[relationship_source_id]["target_npc_id"] == identities[1].npc_id


def test_dashboard_rumor_dedupe_uses_canonical_source_identity() -> None:
    same_source_first = {
        "type": "rumor",
        "fact_scope": "self",
        "source_npc_id": "npc_a",
        "summary": "Rumor: Damien keeps a hidden ledger.",
        "sources": [{"source_id": "src_1", "path": "Damien.md", "section": "Rumors"}],
    }
    same_source_second = {
        **same_source_first,
        "summary": "Whisper: Damien keeps the hidden ledger close.",
    }
    different_source = {
        **same_source_first,
        "summary": "Rumor: Damien keeps a hidden ledger.",
        "sources": [{"source_id": "src_2", "path": "Damien.md", "section": "Rumors"}],
    }

    deduped = club_generation._dedupe_dashboard_rumors([same_source_first, same_source_second, different_source])

    assert len(deduped) == 2
    assert [item["sources"][0]["source_id"] for item in deduped] == ["src_1", "src_2"]


def test_canonical_fact_identity_uses_first_nonempty_source_id_and_fallback_location() -> None:
    sourced = {
        "source_npc_id": " npc_a ",
        "type": "rumor",
        "summary": "Rumor: Damien keeps a hidden ledger.",
        "sources": [{"source_id": " "}, {"source_id": " src_1 "}],
    }
    fallback_a = {
        "source_npc_id": "npc_a",
        "type": "rumor",
        "summary": "Rumor: Damien keeps a hidden ledger.",
        "sources": [{"path": "Damien.md", "section": "Rumors", "excerpt": "line one"}],
    }
    fallback_b = {
        **fallback_a,
        "sources": [{"path": "Damien.md", "section": "Rumors", "excerpt": "line two"}],
    }
    delimiter_a = {
        "source_npc_id": "npc_a",
        "type": "rumor",
        "summary": "Rumor: Damien keeps a hidden ledger.",
        "sources": [{"character_id": "a|b", "path": "c|d", "section": "e|f", "excerpt": "g"}],
    }
    delimiter_b = {
        "source_npc_id": "npc_a",
        "type": "rumor",
        "summary": "Rumor: Damien keeps a hidden ledger.",
        "sources": [{"character_id": "a", "path": "b|c", "section": "d|e", "excerpt": "f|g"}],
    }

    assert club_generation._canonical_fact_identity(sourced) == ("source_id", "npc_a", "src_1")
    assert club_generation._canonical_fact_identity(fallback_a)[0] == "fallback"
    assert club_generation._canonical_fact_identity(fallback_a) != club_generation._canonical_fact_identity(fallback_b)
    assert club_generation._canonical_fact_identity(delimiter_a) != club_generation._canonical_fact_identity(delimiter_b)


def _rumor_candidate(
    source_npc_id: str,
    source_id: str,
    summary: str,
    *,
    mentioned_npc_ids: tuple[str, ...] = (),
    topic_key: str = "",
    source_path: str = "",
    source_section: str = "Whispers",
    link_targets: tuple[str, ...] = (),
) -> dict:
    source = {"source_id": source_id, "section": source_section}
    if source_path:
        source["path"] = source_path
        source["character_id"] = source_npc_id
    item = {
        "type": "rumor",
        "fact_scope": "mention" if mentioned_npc_ids else "self",
        "source_npc_id": source_npc_id,
        "target_npc_id": None,
        "mentioned_npc_ids": list(mentioned_npc_ids),
        "characters": [source_npc_id],
        "summary": summary,
        "sources": [source],
    }
    if topic_key:
        item["topic_key"] = topic_key
    if link_targets:
        item["link_targets"] = list(link_targets)
    return item


def _rumor_source_id(item: dict) -> str:
    return str(item["sources"][0]["source_id"])


def _rumor_selection_projection(selected: list[dict], audit: list[dict]) -> dict:
    return {
        "selected_identities": [club_generation._canonical_fact_identity(item) for item in selected],
        "selected_sources": [_rumor_source_id(item) for item in selected],
        "audit": [
            {
                "identity": club_generation._canonical_fact_identity(entry["fact"]),
                "source_id": _rumor_source_id(entry["fact"]),
                "selection_state": entry["selection_state"],
                "prior_exclusion_reasons": list(entry["prior_exclusion_reasons"]),
            }
            for entry in audit
        ],
    }


def _candidate_pool_projection(items: list[dict]) -> list[dict]:
    return [
        {
            "identity": club_generation._canonical_fact_identity(item),
            "source_npc_id": item.get("source_npc_id"),
            "target_npc_id": item.get("target_npc_id"),
            "mentioned_npc_ids": list(item.get("mentioned_npc_ids") or []),
            "characters": list(item.get("characters") or []),
            "summary": item.get("summary"),
            "topic_key": item.get("topic_key"),
            "link_targets": list(item.get("link_targets") or []),
            "sources": _clone_json(item.get("sources") or []),
        }
        for item in items
    ]


def test_rumor_limit_defaults_and_validation() -> None:
    assert club_generation.default_rumor_limit(1) == 3
    assert club_generation.default_rumor_limit(5) == 5
    assert club_generation.default_rumor_limit(11) == 7
    assert club_generation.normalize_rumor_limit("7", attendee_count=2) == 7
    with pytest.raises(ClubGenerationError, match="Rumors shown"):
        club_generation.normalize_rumor_limit(4, attendee_count=8)


def test_dashboard_rumor_shortlist_applies_caps_then_cumulative_relaxation() -> None:
    items = [
        _rumor_candidate("npc_a", "src_1", "Mara Voss was seen leaving before dawn.", mentioned_npc_ids=("mara",), topic_key="mara"),
        _rumor_candidate("npc_a", "src_2", "Mara Voss plans to confront the courier.", mentioned_npc_ids=("mara",), topic_key="mara"),
        _rumor_candidate("npc_a", "src_3", "Mara Voss is asking about the basement.", mentioned_npc_ids=("mara",), topic_key="mara"),
    ]

    selected, audit = club_generation._select_dashboard_rumors(items, limit=3, seed=1)

    assert [item["sources"][0]["source_id"] for item in selected] == ["src_1", "src_2", "src_3"]
    audit_by_source = {entry["fact"]["sources"][0]["source_id"]: entry for entry in audit}
    assert audit_by_source["src_1"]["selection_state"] == "selected_primary"
    assert audit_by_source["src_2"]["selection_state"] == "selected_after_topic_relaxation"
    assert "excluded_topic_cap" in audit_by_source["src_2"]["prior_exclusion_reasons"]
    assert audit_by_source["src_3"]["selection_state"] == "selected_after_source_relaxation"
    assert "excluded_attendee_cap" in audit_by_source["src_3"]["prior_exclusion_reasons"]
    assert "excluded_source_cap" in audit_by_source["src_3"]["prior_exclusion_reasons"]


def test_dashboard_rumor_shortlist_skips_topic_cap_without_structured_key() -> None:
    items = [
        _rumor_candidate("npc_a", "src_1", "Trouble in Paradise: Mara was seen leaving before dawn."),
        _rumor_candidate("npc_b", "src_2", "Trouble in Paradise: Alexa is asking about the basement."),
        _rumor_candidate("npc_c", "src_3", "Trouble in Paradise: Darius plans to sell a ledger."),
    ]

    _selected, audit = club_generation._select_dashboard_rumors(items, limit=3, seed=1)

    assert {entry["selection_state"] for entry in audit} == {"selected_primary"}
    assert all("excluded_topic_cap" not in entry["prior_exclusion_reasons"] for entry in audit)


def test_dashboard_rumor_multi_mentions_consume_each_attendee_cap() -> None:
    items = [
        _rumor_candidate("npc_1", "src_1", "Mara and Alexa were seen leaving before dawn.", mentioned_npc_ids=("mara", "alexa")),
        _rumor_candidate("npc_2", "src_2", "Mara plans to ask about the missing courier.", mentioned_npc_ids=("mara",)),
        _rumor_candidate("npc_3", "src_3", "Alexa plans to ask about the basement.", mentioned_npc_ids=("alexa",)),
        _rumor_candidate("npc_4", "src_4", "Mara and Alexa are asking about a second courier.", mentioned_npc_ids=("mara", "alexa")),
    ]

    selected, audit = club_generation._select_dashboard_rumors(items, limit=5, seed=1)

    assert [item["sources"][0]["source_id"] for item in selected][-1] == "src_4"
    audit_by_source = {entry["fact"]["sources"][0]["source_id"]: entry for entry in audit}
    assert audit_by_source["src_4"]["selection_state"] == "selected_after_attendee_relaxation"
    assert "excluded_attendee_cap" in audit_by_source["src_4"]["prior_exclusion_reasons"]


def test_dashboard_rumor_sort_uses_canonical_identity_after_seed_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(club_generation, "_seeded_rumor_tie_break", lambda _seed, _identity: "same")
    later = _rumor_candidate("npc_b", "src_b", "Someone is asking about a courier.")
    earlier = _rumor_candidate("npc_a", "src_a", "Someone is asking about a courier.")

    selected, _audit = club_generation._select_dashboard_rumors([later, earlier], limit=3, seed=99)

    assert [item["sources"][0]["source_id"] for item in selected] == ["src_a", "src_b"]


def test_dashboard_rumor_duplicate_representative_is_input_order_stable() -> None:
    first = _rumor_candidate("npc_a", "src_a", "Someone is asking about a courier.")
    second = {**first, "prep_text": "Alternate table phrasing."}

    selected_forward, audit_forward = club_generation._select_dashboard_rumors([first, second], limit=3, seed=99)
    selected_reversed, audit_reversed = club_generation._select_dashboard_rumors([second, first], limit=3, seed=99)

    assert selected_forward == selected_reversed
    assert audit_forward == audit_reversed


def test_dashboard_rumor_selector_dedupes_same_claim_but_keeps_distinct_meanings() -> None:
    duplicate_a = _rumor_candidate(
        "npc_damien",
        "src_claim_a",
        "Ledger Claim: Damien keeps blackmail material over Mara.",
        mentioned_npc_ids=("npc_mara",),
        topic_key="ledger",
        source_path="Damien.md",
    )
    duplicate_b = _rumor_candidate(
        "npc_damien",
        "src_claim_b",
        "Ledger Claim: Damien keeps blackmail material over Mara.",
        mentioned_npc_ids=("npc_mara",),
        topic_key="ledger",
        source_path="Damien.md",
    )
    private_exposure = _rumor_candidate(
        "npc_damien",
        "src_private",
        "Ledger Claim: Damien can expose Mara's private at court.",
        mentioned_npc_ids=("npc_mara",),
        topic_key="ledger",
        source_path="Damien.md",
    )
    debt_to_alexa = _rumor_candidate(
        "npc_damien",
        "src_debt",
        "Ledger Claim: Damien owes Alexa a major obligation tonight.",
        mentioned_npc_ids=("npc_alexa",),
        topic_key="ledger",
        source_path="Damien.md",
    )

    selected, audit = club_generation._select_dashboard_rumors(
        [duplicate_b, debt_to_alexa, duplicate_a, private_exposure],
        limit=7,
        seed=17,
    )
    selected_sources = {_rumor_source_id(item) for item in selected}
    audit_with_duplicate_reason = [
        entry for entry in audit if "excluded_provenance_duplicate" in entry["prior_exclusion_reasons"]
    ]

    assert len({"src_claim_a", "src_claim_b"} & selected_sources) == 1
    assert audit_with_duplicate_reason
    assert {"src_private", "src_debt"} <= selected_sources


def test_dashboard_rumor_selector_is_deterministic_idempotent_and_non_mutating_for_mixed_pool() -> None:
    items = [
        _rumor_candidate(
            "npc_damien",
            "src_dup_a",
            "Ledger Claim: Damien keeps blackmail material over Mara.",
            mentioned_npc_ids=("npc_mara",),
            topic_key="ledger",
            source_path="Damien.md",
        ),
        _rumor_candidate(
            "npc_damien",
            "src_dup_b",
            "Ledger Claim: Damien keeps blackmail material over Mara.",
            mentioned_npc_ids=("npc_mara",),
            topic_key="ledger",
            source_path="Damien.md",
        ),
        _rumor_candidate(
            "npc_damien",
            "src_topic_2",
            "Ledger Claim: Damien can expose Mara's private at court tonight.",
            mentioned_npc_ids=("npc_mara",),
            topic_key="ledger",
            source_path="Damien.md",
            link_targets=("Mara",),
        ),
        _rumor_candidate(
            "npc_damien",
            "src_topic_3",
            "Ledger Claim: Damien owes Alexa a major obligation tonight.",
            mentioned_npc_ids=("npc_alexa",),
            topic_key="ledger",
            source_path="Damien.md",
            link_targets=("Alexa",),
        ),
        _rumor_candidate(
            "npc_damien",
            "src_source_3",
            "Courier Claim: Damien is asking Darius about a missing courier tonight.",
            mentioned_npc_ids=("npc_darius",),
            topic_key="courier",
            source_path="Damien.md",
            link_targets=("Darius",),
        ),
        _rumor_candidate(
            "npc_mara",
            "src_attendee_3",
            "Mara Claim: Mara and Alexa are hunting for the second ledger at court.",
            mentioned_npc_ids=("npc_mara", "npc_alexa"),
            topic_key="mara",
            source_path="Mara.md",
            link_targets=("Mara", "Alexa"),
        ),
        _rumor_candidate(
            "npc_alexa",
            "src_equal_b",
            "Equal Claim: Someone is asking about a courier at court.",
            mentioned_npc_ids=("npc_darius",),
            topic_key="equal-b",
            source_path="Alexa.md",
        ),
        _rumor_candidate(
            "npc_darius",
            "src_equal_a",
            "Equal Claim: Someone is asking about a courier at court.",
            mentioned_npc_ids=("npc_alexa",),
            topic_key="equal-a",
            source_path="Darius.md",
        ),
        _rumor_candidate(
            "npc_erika",
            "src_no_topic",
            "No Topic Claim: Erika is asking about the bar's locked office.",
            source_path="Erika.md",
        ),
    ]
    orders = [
        items,
        list(reversed(items)),
        [items[index] for index in (3, 0, 7, 1, 5, 2, 8, 4, 6)],
        [items[index] for index in (8, 4, 2, 6, 1, 7, 0, 3, 5)],
    ]
    baseline_projection = None
    baseline_pool = _candidate_pool_projection(items)

    for ordered in orders:
        before = _candidate_pool_projection(ordered)
        selected, audit = club_generation._select_dashboard_rumors(ordered, limit=7, seed=23)
        projection = _rumor_selection_projection(selected, audit)
        selected_identities = projection["selected_identities"]
        audit_by_source = {entry["source_id"]: entry for entry in projection["audit"]}

        assert _candidate_pool_projection(ordered) == before
        assert len(selected) <= 7
        assert len(selected_identities) == len(set(selected_identities))
        assert len(projection["selected_sources"]) == len(set(projection["selected_sources"]))
        all_prior_reasons = [
            reason
            for entry in projection["audit"]
            for reason in entry["prior_exclusion_reasons"]
        ]
        assert "excluded_topic_cap" in all_prior_reasons
        assert "excluded_source_cap" in audit_by_source["src_source_3"]["prior_exclusion_reasons"]
        assert "excluded_attendee_cap" in all_prior_reasons
        assert "excluded_topic_cap" not in audit_by_source["src_no_topic"]["prior_exclusion_reasons"]
        if baseline_projection is None:
            baseline_projection = projection
        assert projection == baseline_projection

    assert _candidate_pool_projection(items) == baseline_pool


def test_dashboard_visible_curation_does_not_mutate_inputs() -> None:
    relationship = {
        "type": "blackmail",
        "fact_scope": "relationship",
        "source_npc_id": "npc_damien",
        "target_npc_id": "npc_mara",
        "mentioned_npc_ids": [],
        "characters": ["npc_damien", "npc_mara"],
        "summary": "Damien keeps blackmail material about Mara's private.",
        "sources": [{"source_id": "src_relationship"}],
    }
    dashboard_facts = [
        _rumor_candidate(
            "npc_damien",
            "src_rumor",
            "Rumor: Damien keeps blackmail material about Mara's private.",
            mentioned_npc_ids=("npc_mara",),
            topic_key="ledger",
        ),
        {
            "type": "unresolved_business",
            "fact_scope": "self",
            "source_npc_id": "npc_mara",
            "target_npc_id": "",
            "mentioned_npc_ids": [],
            "characters": ["npc_mara"],
            "summary": "Mara still owes a public debt.",
            "sources": [{"source_id": "src_unresolved"}],
        },
    ]
    ranked_connections = [relationship]
    before = _clone_json(
        {
            "relationships": [relationship],
            "dashboard_facts": dashboard_facts,
            "ranked_connections": ranked_connections,
        }
    )

    club_generation._curate_dashboard_visible_sections(
        relationships=[relationship],
        dashboard_facts=dashboard_facts,
        ranked_connections=ranked_connections,
        rumor_limit=3,
        seed=1,
    )

    assert {
        "relationships": [relationship],
        "dashboard_facts": dashboard_facts,
        "ranked_connections": ranked_connections,
    } == before


def test_dashboard_skeleton_keeps_full_rumor_pool_but_materializes_selected_limit(tmp_path: Path) -> None:
    files = []
    for idx in range(6):
        path = tmp_path / f"Source {idx}.md"
        target = (idx + 1) % 6
        path.write_text(
            "### Whispers\n"
            f"- **Lead {idx}:** Source {idx} was seen asking [[Source {target}]] about the missing courier.\n",
            encoding="utf-8",
        )
        files.append(path)
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))

    result = service.build_event_result([str(path) for path in files], seed=1, rumor_limit=3, use_ai=False)
    skeleton = build_dashboard_skeleton(result)

    assert len(skeleton["rumor_pool"]) == 6
    assert len(skeleton["rumors"]) == 3
    assert skeleton["rumor_selection"] == {"limit": 3, "selected_count": 3, "grounded_count": 6}
    assert len(result.event.dashboard["rumors_in_circulation"]) == 3


def test_deterministic_dashboard_includes_self_scoped_sheet_rumor(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Whispers\n"
        "- **Eyes of the Pyramid:** The Court says Damien keeps watch for a hidden patron.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    identities = [
        NpcIdentity("damien", str(damien), "Damien", (), "damien-rev"),
        NpcIdentity("annabelle", str(annabelle), "Annabelle", (), "annabelle-rev"),
    ]
    indexes = [build_club_index(identity, Path(identity.current_path)) for identity in identities]
    context = build_attendee_context(identities, indexes)

    dashboard = club_generation.deterministic_dashboard(
        context,
        venue="The Lantern Room",
        event_type="social gathering",
        late_arrival_id="annabelle",
        rumor_limit=3,
        seed=1,
    )

    assert dashboard["rumor_selection"] == {"limit": 3, "selected_count": 1, "grounded_count": 1}
    assert "Eyes of the Pyramid" in json.dumps(dashboard["rumors"])
    assert "Eyes of the Pyramid" in json.dumps(dashboard["rumors_in_circulation"])
    assert dashboard["rumors"][0]["target_npc_id"] is None


def test_rumor_limit_participates_in_dashboard_cache_identity(tmp_path: Path) -> None:
    files = []
    for idx in range(8):
        path = tmp_path / f"Guest {idx}.md"
        path.write_text(
            "### Whispers\n"
            f"- **Lead {idx}:** Guest {idx} was seen asking about the missing courier.\n",
            encoding="utf-8",
        )
        files.append(path)
    provider = FakeProvider([])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    cap_3 = service.build_event_result([str(path) for path in files], seed=1, rumor_limit=3, use_ai=False)
    cap_7 = service.build_event_result([str(path) for path in files], seed=1, rumor_limit=7, use_ai=False)

    assert cap_3.event.cache_key != cap_7.event.cache_key
    assert cap_3.debug_context["dashboard_skeleton"]["rumor_materialization_version"] == club_generation.CLUB_RUMOR_MATERIALIZATION_VERSION
    assert cap_3.event.dashboard["rumor_selection"]["limit"] == 3
    assert cap_7.event.dashboard["rumor_selection"]["limit"] == 7


def test_rumor_materialization_version_participates_in_dashboard_cache_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Whispers\n"
        "- **Ledger:** Damien is asking about a missing courier.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))

    first = service.build_event_result([str(damien), str(annabelle)], seed=1, rumor_limit=3, use_ai=False)
    monkeypatch.setattr(club_generation, "CLUB_RUMOR_MATERIALIZATION_VERSION", "club_rumor_materialization_test")
    second = service.build_event_result([str(damien), str(annabelle)], seed=1, rumor_limit=3, use_ai=False)

    assert first.event.cache_key != second.event.cache_key
    assert second.debug_context["dashboard_skeleton"]["rumor_materialization_version"] == "club_rumor_materialization_test"


def test_old_rumor_materialization_event_cache_entry_is_not_returned_under_current_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Whispers\n"
        "- **Ledger:** Damien is asking about a missing courier at court.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    cache_root = tmp_path / ".club-cache"
    service_for_identity = CanonicalGenerationService({}, cache_root=cache_root, vault_root=tmp_path, provider=FakeProvider([]))
    identities, indexes = service_for_identity._load_attendees([str(damien), str(annabelle)])
    context = build_attendee_context(identities, indexes)
    source_map = build_source_map(indexes, identities=identities)
    event_seed = 1
    late_arrival_id = club_generation.select_late_arrival(identities, host_path=None, seed=event_seed)
    current_skeleton = club_generation._build_dashboard_skeleton_from_context(
        context,
        source_map,
        venue="The Lantern Room",
        event_type="social gathering",
        late_arrival_id=late_arrival_id,
        rumor_limit=3,
        seed=event_seed,
    )
    old_skeleton = _clone_json(current_skeleton)
    old_skeleton["rumor_materialization_version"] = f"{club_generation.CLUB_RUMOR_MATERIALIZATION_VERSION}_old"
    old_cache_key = club_generation.stable_hash(
        {
            "attendee_ids": sorted(identity.npc_id for identity in identities),
            "index_revisions": {index.npc_id: index.sheet_revision_hash for index in indexes},
            "venue": "The Lantern Room",
            "event_type": "social gathering",
            "guest_paths": sorted(str(path) for path in [damien, annabelle]),
            "skeleton": old_skeleton,
            "schema_version": club_generation.CLUB_DASHBOARD_SCHEMA_VERSION,
            "prompt_version": club_generation.CLUB_DASHBOARD_PROMPT_VERSION,
            "seed": event_seed,
            "ai_request_version": club_generation.CLUB_DASHBOARD_AI_REQUEST_VERSION,
        }
    )
    stale_dashboard = dashboard_from_skeleton(old_skeleton)
    stale_dashboard["room_situation"] = "STALE_RUMOR_MATERIALIZATION_SENTINEL"
    ClubCacheService(cache_root).write_json(
        "events",
        f"{old_cache_key}.json",
        data={
            "event_id": "stale-rumor-materialization",
            "attendee_ids": [identity.npc_id for identity in identities],
            "late_arrival_id": late_arrival_id,
            "dashboard": stale_dashboard,
            "cache_key": old_cache_key,
            "seed": event_seed,
            "metadata": {"generation_mode": "ai", "from_cache": False},
        },
    )
    tracked_caches = []

    class TrackingCache(ClubCacheService):
        def __init__(self, root) -> None:
            super().__init__(root)
            self.reads = []
            self.writes = []

        def read_json(self, *parts: str, default=None):
            self.reads.append(tuple(parts))
            return super().read_json(*parts, default=default)

        def write_json(self, *parts: str, data):
            self.writes.append(tuple(parts))
            return super().write_json(*parts, data=data)

    def tracking_cache_factory(root):
        cache = TrackingCache(root)
        tracked_caches.append(cache)
        return cache

    other_versions = (
        club_generation.CLUB_DASHBOARD_AI_REQUEST_VERSION,
        club_generation.CLUB_PANEL_AI_REQUEST_VERSION,
        club_generation.CLUB_PANEL_SCHEMA_VERSION,
    )
    monkeypatch.setattr(club_generation, "CLUB_RUMOR_MATERIALIZATION_VERSION", "temporary_guard_version")
    assert (
        club_generation.CLUB_DASHBOARD_AI_REQUEST_VERSION,
        club_generation.CLUB_PANEL_AI_REQUEST_VERSION,
        club_generation.CLUB_PANEL_SCHEMA_VERSION,
    ) == other_versions
    monkeypatch.setattr(club_generation, "CLUB_RUMOR_MATERIALIZATION_VERSION", current_skeleton["rumor_materialization_version"])
    monkeypatch.setattr(club_generation, "ClubCacheService", tracking_cache_factory)
    provider = SkeletonEchoProvider()
    service = CanonicalGenerationService({}, cache_root=cache_root, vault_root=tmp_path, provider=provider)

    result = service.build_event_result([str(damien), str(annabelle)], seed=event_seed, rumor_limit=3)

    current_event_parts = ("events", f"{result.event.cache_key}.json")
    assert old_cache_key != result.event.cache_key
    assert "STALE_RUMOR_MATERIALIZATION_SENTINEL" not in json.dumps(result.event.dashboard)
    assert len(provider.prompts) == 1
    assert any(current_event_parts in cache.reads for cache in tracked_caches)
    assert any(current_event_parts in cache.writes for cache in tracked_caches)
    assert (cache_root / "events" / f"{result.event.cache_key}.json").exists()


def test_dashboard_pressure_promotion_runs_after_relevance_gate(tmp_path: Path) -> None:
    alexa = tmp_path / "Alexa Santos.md"
    aluc = tmp_path / "Aluc Romas de Leon.md"
    abraham = tmp_path / "Abraham DuSable.md"
    alexa.write_text(
        "### Relationships\n"
        "- [[Aluc Romas de Leon]] (Paramour): Blood-bound lover and partner in degeneracy.\n"
        "### Whispers\n"
        "- **Test Subject:** Some claim [[Aluc Romas de Leon]] is studying Alexa to see how far she can fall.\n",
        encoding="utf-8",
    )
    aluc.write_text("Affiliation: Oracle\n", encoding="utf-8")
    abraham.write_text(
        "### Whispers\n"
        "- **Blackmail Ledger:** Abraham keeps scandalous material for blackmail.\n",
        encoding="utf-8",
    )
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(alexa), str(aluc), str(abraham)], seed=1, use_ai=False)

    skeleton = build_dashboard_skeleton(result)
    drama_text = json.dumps(skeleton["possible_drama"])

    assert "Blood-bound lover" in drama_text
    assert "Test Subject" in drama_text
    assert "Blackmail Ledger" not in drama_text
    assert all(is_dashboard_pressure(item) for item in skeleton["possible_drama"])


def test_dashboard_top_connections_are_limited_and_removed_from_additional(tmp_path: Path) -> None:
    names = ["Alan Sovereign", "Alexa Santos", "Aluc Romas de Leon", "Amelia Locke", "Anthius Dread", "Abraham DuSable"]
    files = {name: tmp_path / f"{name}.md" for name in names}
    files["Alan Sovereign"].write_text(
        "### Relationships\n"
        "- [[Alexa Santos]] (Enemy): Bitter court hostility.\n"
        "- [[Aluc Romas de Leon]] (Business): Valuable business associate.\n"
        "- [[Amelia Locke]] (Ally): Quiet support.\n"
        "- [[Anthius Dread]] (Patron): Old protection.\n",
        encoding="utf-8",
    )
    files["Alexa Santos"].write_text(
        "### Relationships\n"
        "- [[Aluc Romas de Leon]] (Paramour): Blood-bound lover and partner in degeneracy.\n",
        encoding="utf-8",
    )
    for name in ["Aluc Romas de Leon", "Amelia Locke", "Anthius Dread", "Abraham DuSable"]:
        files[name].write_text("Affiliation: Executives\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(files[name]) for name in names], seed=1, use_ai=False)

    skeleton = build_dashboard_skeleton(result)
    top_keys = {grounded_fact_key(item) for item in skeleton["top_connections"]}
    additional_keys = {grounded_fact_key(item) for item in skeleton["interesting_connections"]}
    drama_keys = {grounded_fact_key(item) for item in skeleton["possible_drama"]}
    top_signatures = {club_generation._visible_item_signature(item) for item in skeleton["top_connections"]}
    drama_signatures = {club_generation._visible_item_signature(item) for item in skeleton["possible_drama"]}

    assert 1 <= len(skeleton["top_connections"]) <= 3
    assert not top_keys & additional_keys
    assert not top_keys & drama_keys
    assert not top_signatures & drama_signatures


def test_dashboard_ranked_connections_preserve_source_viewpoint(tmp_path: Path) -> None:
    alexa = tmp_path / "Alexa Santos.md"
    son = tmp_path / "Son.md"
    alexa.write_text("Affiliation: Oracle\n", encoding="utf-8")
    son.write_text(
        "### Relationships\n"
        "- [[Alexa Santos]] (Toy): Young Oracle he manipulates and may commit grave misconduct against.\n",
        encoding="utf-8",
    )
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(alexa), str(son)], seed=1, use_ai=False)

    skeleton = build_dashboard_skeleton(result)
    toy = next(item for item in skeleton["possible_drama"] if "Toy" in item["summary"])
    source_id, target_id = toy["characters"]

    assert result.indexes_by_id[source_id].name == "Son"
    assert result.indexes_by_id[target_id].name == "Alexa Santos"


def test_dashboard_adds_table_ready_prep_text_without_removing_sources(tmp_path: Path) -> None:
    alexa = tmp_path / "Alexa Santos.md"
    son = tmp_path / "Son.md"
    alexa.write_text("Affiliation: Oracle\n", encoding="utf-8")
    son.write_text(
        "### Relationships\n"
        "- [[Alexa Santos]] (Toy): Alexa Santos (Toy): Young Oracle he manipulates and may commit grave misconduct against.\n",
        encoding="utf-8",
    )
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(alexa), str(son)], seed=1, use_ai=False)

    item = result.event.dashboard["possible_drama"][0]

    assert item["summary"].startswith("Alexa Santos (Toy):")
    assert item["prep_text"] == (
        "Possible pressure — Alexa Santos (Toy): Alexa Santos (Toy): "
        "Young Oracle he manipulates and may commit grave misconduct against."
    )
    assert item["sources"]


def test_relationship_opening_avoids_bad_tie_phrasing() -> None:
    rendered = [
        club_generation._relationship_opening("Damien", "Annabelle", label)
        for label in ["Offended", "Useful", "Ally", "Business", "Distrust"]
    ]
    text = "\n".join(rendered).lower()

    assert "has a offended tie" not in text
    assert "has a useful tie" not in text
    assert "has a " not in text


def test_fallback_guest_brief_uses_specific_grounded_relationship(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Useful): Political contact at court.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    guest_text = "\n".join(result.event.dashboard["guest_brief"])

    assert "tied into the room's visible social map" not in guest_text
    assert "has a useful tie" not in guest_text
    assert "Damien (late arrival): watch Annabelle" in guest_text
    assert "Political contact at court" in guest_text


def test_ai_dashboard_rehydrates_compact_grounded_items_from_canonical_skeleton(tmp_path: Path) -> None:
    son = tmp_path / "Son.md"
    alexa = tmp_path / "Alexa Santos.md"
    son.write_text(
        "### Relationships\n"
        "- [[Alexa Santos]] (Toy): Young Oracle he manipulates and may commit grave misconduct against.\n",
        encoding="utf-8",
    )
    alexa.write_text("Affiliation: Oracle\n", encoding="utf-8")

    def compact_ai_response(skeleton):
        dashboard = dashboard_from_skeleton(skeleton, first_impression="The bass shudders through old brick.")
        dashboard["possible_drama"][0]["prep_text"] = "Son turns Alexa into the pressure point for tonight."
        return dashboard

    provider = ScriptedSkeletonProvider([compact_ai_response])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)
    result = service.build_event_result([str(son), str(alexa)], seed=1)
    item = result.event.dashboard["possible_drama"][0]
    source = item["sources"][0]

    assert result.event.metadata["generation_mode"] == "ai"
    assert result.event.metadata["ai_attempted"] is True
    assert result.event.metadata["fallback_used"] is False
    assert result.event.metadata["ai_error_type"] is None
    assert result.event.metadata["ai_error_stage"] is None
    assert result.event.metadata["validation_error"] is None
    assert item["prep_text"] == (
        "Possible pressure — Alexa Santos (Toy): Young Oracle he manipulates and may commit grave misconduct against."
    )
    assert item["summary"] == "Alexa Santos (Toy): Young Oracle he manipulates and may commit grave misconduct against."
    assert item["source_npc_id"] == result.event.attendee_ids[0]
    assert item["target_npc_id"] == result.event.attendee_ids[1]
    assert item["characters"] == [result.event.attendee_ids[0], result.event.attendee_ids[1]]
    assert source["path"] == str(son)
    assert source["character_id"] == result.event.attendee_ids[0]
    assert source["section"] == "Relationships"


def test_ai_dashboard_accepts_exact_expanded_skeleton_source_objects(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)
    skeleton = build_dashboard_skeleton(result)
    dashboard = dashboard_from_skeleton(skeleton)

    club_generation.validate_ai_dashboard(
        dashboard,
        source_ids=set(skeleton["source_ids"]),
        attendee_ids=set(skeleton["attendee_ids"]),
        skeleton=skeleton,
    )


def test_ai_dashboard_blank_prep_text_is_filled_from_canonical_item(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")

    def blank_prep_response(skeleton):
        dashboard = dashboard_from_skeleton(skeleton)
        dashboard["possible_drama"][0]["prep_text"] = " "
        return dashboard

    provider = ScriptedSkeletonProvider([blank_prep_response])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)
    result = service.build_event_result([str(damien), str(annabelle)], seed=1)
    item = result.event.dashboard["possible_drama"][0]

    assert result.event.metadata["generation_mode"] == "ai"
    assert item["prep_text"]
    assert "source_npc_id" not in item["prep_text"]
    assert "target_npc_id" not in item["prep_text"]
    assert item["summary"] == "Annabelle (Blackmail): Keeps blackmail material about Annabelle's secret private."


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_item_id",
        "duplicate_item_id",
        "wrong_section",
        "unknown_source_id",
        "mutated_nested_source",
        "mixed_source_object",
        "mutated_endpoint",
    ],
)
def test_ai_dashboard_rejects_bad_grounded_candidates_before_rehydration(tmp_path: Path, mutation: str) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")

    def bad_response(skeleton):
        dashboard = dashboard_from_skeleton(skeleton)
        item = _clone_json(dashboard["possible_drama"][0])
        item["prep_text"] = "Bad AI candidate should not display."
        if mutation == "unknown_item_id":
            item["item_id"] = "fabricated"
        elif mutation == "duplicate_item_id":
            dashboard["possible_drama"] = [item, _clone_json(item)]
            return dashboard
        elif mutation == "wrong_section":
            item["section"] = "top_connections"
        elif mutation == "unknown_source_id":
            item["sources"] = [{"source_id": "fabricated-source"}]
        elif mutation == "mutated_nested_source":
            source_id = item["sources"][0]["source_id"]
            item["sources"] = [
                {
                    "character_id": "fabricated-character",
                    "path": "fabricated.md",
                    "section": "Relationships",
                    "source_id": source_id,
                    "excerpt": "Invented provenance.",
                }
            ]
        elif mutation == "mixed_source_object":
            item["sources"] = [{"source_id": item["sources"][0]["source_id"], "path": "fabricated.md"}]
        elif mutation == "mutated_endpoint":
            item["target_npc_id"] = "fabricated-target"
        dashboard["possible_drama"] = [item]
        return dashboard

    provider = ScriptedSkeletonProvider([bad_response, bad_response])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)
    result = service.build_event_result([str(damien), str(annabelle)], seed=1)

    assert result.event.metadata["generation_mode"] == "deterministic_fallback"
    assert result.event.metadata["ai_error_type"] == "ai_output_validation_failed"
    assert result.event.metadata["ai_error_stage"] == "ai_output_validation"
    assert len(provider.prompts) == 2
    assert "Bad AI candidate should not display." not in json.dumps(result.event.dashboard)
    _assert_no_ai_unavailable_marker(result.event.dashboard)


def test_dashboard_pressure_does_not_promote_generic_threat_mentions(tmp_path: Path) -> None:
    critias = tmp_path / "Critias.md"
    kevin = tmp_path / "Kevin Jackson.md"
    critias.write_text(
        "### Relationships\n"
        "- [[Kevin Jackson]] (Loyalty): Reports privacy threats to the Director as duty-bound Council loyalist.\n",
        encoding="utf-8",
    )
    kevin.write_text("Affiliation: Executives\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(critias), str(kevin)], seed=1, use_ai=False)

    skeleton = build_dashboard_skeleton(result)

    assert "Reports privacy threats" not in json.dumps(skeleton["possible_drama"])


def test_dashboard_possible_pressure_requires_concrete_break_reason(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Rival): Useful political contact at court.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    skeleton = build_dashboard_skeleton(result)

    assert skeleton["top_connections"]
    assert "Useful political contact" not in json.dumps(skeleton["possible_drama"])


def test_dashboard_possible_pressure_names_specific_break_consequence(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    skeleton = build_dashboard_skeleton(result)
    pressure = skeleton["possible_drama"][0]
    text = pressure["prep_text"].lower()

    assert pressure["item_id"]
    assert pressure["sources"]
    assert "secret private" in text
    assert "blackmail" in text
    assert any(word in text for word in ("exposed", "leveraged", "confrontation"))


def test_dashboard_duplicate_connection_allowed_only_with_fact_specific_pressure_reason(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    critias = tmp_path / "Critias.md"
    kevin = tmp_path / "Kevin Jackson.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Keeps blackmail material about Annabelle's secret private.\n"
        "- [[Critias]] (Rival): Useful political contact at court.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    critias.write_text("Affiliation: Dockworkers\n", encoding="utf-8")
    kevin.write_text("Affiliation: Executives\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle), str(critias), str(kevin)], seed=1, use_ai=False)

    skeleton = build_dashboard_skeleton(result)
    top_by_key = {grounded_fact_key(item): item for item in skeleton["top_connections"]}
    pressure_by_key = {grounded_fact_key(item): item for item in skeleton["possible_drama"]}
    duplicated_keys = set(top_by_key) & set(pressure_by_key)
    top_signatures = {club_generation._visible_item_signature(item) for item in skeleton["top_connections"]}
    pressure_signatures = {club_generation._visible_item_signature(item) for item in skeleton["possible_drama"]}

    assert not duplicated_keys
    assert not top_signatures & pressure_signatures
    assert any("secret private" in item["prep_text"].lower() for item in pressure_by_key.values())
    assert "Useful political contact" not in json.dumps(skeleton["possible_drama"])


def test_dashboard_visible_signature_keeps_asymmetric_debts_separate(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Obligation): Damien owes Annabelle a obligation.\n",
        encoding="utf-8",
    )
    annabelle.write_text(
        "### Relationships\n"
        "- [[Damien]] (Obligation): Annabelle owes Damien a obligation.\n",
        encoding="utf-8",
    )
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    pressures = build_dashboard_skeleton(result)["possible_drama"]
    pressure_text = json.dumps(pressures)

    assert len(pressures) == 2
    assert "Damien owes Annabelle" in pressure_text
    assert "Annabelle owes Damien" in pressure_text
    assert {item["source_npc_id"] for item in pressures} == set(result.event.attendee_ids)
    assert all(item["characters"] == [item["source_npc_id"], item["target_npc_id"]] for item in pressures)


def test_dashboard_visible_signature_merges_same_direction_pressure_reformulation(tmp_path: Path) -> None:
    annabelle = tmp_path / "Annabelle.md"
    damien = tmp_path / "Damien.md"
    annabelle.write_text(
        "### Relationships\n"
        "- [[Damien]] (Blackmail): Annabelle has blackmail material on Damien.\n"
        "- [[Damien]] (Leverage): Annabelle keeps blackmail leverage over Damien.\n",
        encoding="utf-8",
    )
    damien.write_text("Affiliation: Dockworkers\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(annabelle), str(damien)], seed=1, use_ai=False)

    pressures = build_dashboard_skeleton(result)["possible_drama"]

    assert len(pressures) == 1
    assert pressures[0]["source_npc_id"] == result.event.attendee_ids[0]
    assert pressures[0]["target_npc_id"] == result.event.attendee_ids[1]


def test_dashboard_visible_signature_does_not_merge_distinct_table_meaning(tmp_path: Path) -> None:
    annabelle = tmp_path / "Annabelle.md"
    damien = tmp_path / "Damien.md"
    annabelle.write_text(
        "### Relationships\n"
        "- [[Damien]] (Blackmail): Annabelle blackmails Damien over his private.\n"
        "- [[Damien]] (Paramour): Damien is Annabelle's paramour.\n",
        encoding="utf-8",
    )
    damien.write_text("Affiliation: Dockworkers\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(annabelle), str(damien)], seed=1, use_ai=False)

    skeleton = build_dashboard_skeleton(result)
    visible_text = json.dumps([*skeleton["possible_drama"], *skeleton["top_connections"], *skeleton["interesting_connections"]])

    assert "blackmails Damien" in visible_text
    assert "paramour" in visible_text


def test_visible_dedupe_uses_stable_survivor_tie_breaks() -> None:
    weaker = {
        "type": "blackmail",
        "source_npc_id": "annabelle",
        "target_npc_id": "damien",
        "characters": ["annabelle", "damien"],
        "summary": "Annabelle has blackmail material on Damien.",
        "sources": [{"source_id": "src_b", "character_id": "annabelle", "path": "Annabelle.md", "section": "Relationships"}],
    }
    stronger = {
        **weaker,
        "sources": [
            {"source_id": "src_c", "character_id": "annabelle", "path": "Annabelle.md", "section": "Relationships"},
            {"source_id": "src_d", "character_id": "annabelle", "path": "Annabelle.md", "section": "Relationships"},
        ],
    }
    earlier_source = {**weaker, "sources": [{"source_id": "src_a", "character_id": "annabelle", "path": "Annabelle.md", "section": "Relationships"}]}

    assert club_generation._dedupe_visible_items([weaker, stronger]) == [stronger]
    assert club_generation._dedupe_visible_items([weaker, earlier_source]) == [earlier_source]


def test_dashboard_opportunities_require_room_action_not_private_agendas(tmp_path: Path) -> None:
    dusable = tmp_path / "Abraham DuSable.md"
    alexa = tmp_path / "Alexa Santos.md"
    kevin = tmp_path / "Kevin Jackson.md"
    dusable.write_text(
        "### Plots and Schemes\n"
        "- **Private Unity:** DuSable wants to unify Affiliation Scholars in Riverton.\n"
        "- **Useful Contact:** DuSable wants [[Kevin Jackson]] as a useful contact.\n"
        "- **Warning Courier:** DuSable wants [[Alexa Santos]] to carry a warning to court.\n",
        encoding="utf-8",
    )
    alexa.write_text("Affiliation: Oracle\n", encoding="utf-8")
    kevin.write_text("Affiliation: Executives\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(dusable), str(alexa), str(kevin)], seed=1, use_ai=False)

    opportunities = json.dumps(build_dashboard_skeleton(result)["opportunities"])

    assert "Warning Courier" in opportunities
    assert "Private Unity" not in opportunities
    assert "Useful Contact" not in opportunities


def test_npc_panel_skeleton_only_includes_clicked_npc_facts(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    critias = tmp_path / "Critias.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Distrust): Political maneuvering.\n",
        encoding="utf-8",
    )
    annabelle.write_text("### Relationships\n- [[Critias]] (Ally): Shared philosophy.\n", encoding="utf-8")
    critias.write_text("Affiliation: Dockworkers\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle), str(critias)], seed=1, use_ai=False)

    skeleton = build_npc_panel_skeleton(result, result.event.attendee_ids[0])
    text = json.dumps(skeleton)

    assert "Political maneuvering" in text
    assert "Shared philosophy" not in text


def test_npc_panel_people_here_includes_clicked_npc_outgoing_relationships(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Obligation): Damien owes Annabelle a obligation.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    skeleton = build_npc_panel_skeleton(result, result.event.attendee_ids[0])

    assert skeleton["people_here"]
    assert skeleton["people_here"][0]["source_npc_id"] == result.event.attendee_ids[0]
    assert skeleton["people_here"][0]["target_npc_id"] == result.event.attendee_ids[1]
    assert "Damien owes Annabelle a obligation" in skeleton["people_here"][0]["summary"]


def test_npc_panel_people_here_uses_only_clicked_npc_outgoing_relationships(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("Affiliation: Dockworkers\n", encoding="utf-8")
    annabelle.write_text(
        "### Relationships\n"
        "- [[Damien]] (Blackmail): Annabelle keeps blackmail material on Damien.\n",
        encoding="utf-8",
    )
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    skeleton = build_npc_panel_skeleton(result, result.event.attendee_ids[0])

    assert skeleton["people_here"] == []


def test_npc_panel_incoming_pressure_can_surface_as_sensitive_or_hook(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("Affiliation: Dockworkers\n", encoding="utf-8")
    annabelle.write_text(
        "### Relationships\n"
        "- [[Damien]] (Blackmail): Annabelle holds blackmail evidence about Damien's secret private.\n",
        encoding="utf-8",
    )
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    skeleton = build_npc_panel_skeleton(result, result.event.attendee_ids[0])
    selected_text = json.dumps([skeleton["conversation"]["sensitive"], skeleton["interesting_detail"]])

    assert skeleton["people_here"] == []
    assert "blackmail evidence about Damien" in selected_text


def test_npc_panel_incoming_generic_tie_is_not_sensitive(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("Affiliation: Dockworkers\n", encoding="utf-8")
    annabelle.write_text(
        "### Relationships\n"
        "- [[Damien]] (Ally): Annabelle sees Damien as a useful political contact.\n",
        encoding="utf-8",
    )
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    skeleton = build_npc_panel_skeleton(result, result.event.attendee_ids[0])

    assert "useful political contact" not in json.dumps(skeleton["conversation"]["sensitive"])


def test_npc_panel_unrelated_attendee_fact_is_excluded(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    son = tmp_path / "Son.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("Affiliation: Dockworkers\n", encoding="utf-8")
    son.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Son holds blackmail evidence over Annabelle.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(son), str(annabelle)], seed=1, use_ai=False)

    skeleton = build_npc_panel_skeleton(result, result.event.attendee_ids[0])

    assert "Son holds blackmail evidence" not in json.dumps(skeleton)


def test_npc_panel_mentioned_id_relevance_uses_canonical_ids(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("Affiliation: Dockworkers\n", encoding="utf-8")
    annabelle.write_text(
        "### Plots and Schemes\n"
        "- **Public Terms:** Annabelle wants [[Damien]] to hear her terms tonight.\n",
        encoding="utf-8",
    )
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)
    damien_id = result.event.attendee_ids[0]

    skeleton = build_npc_panel_skeleton(result, damien_id)
    conversation = skeleton["conversation"]["likely_subjects"]

    assert any(damien_id in item["mentioned_npc_ids"] for item in conversation)
    assert "Public Terms" in json.dumps(conversation)


def test_npc_panel_sensitive_subjects_exclude_generic_useful_connections(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Rival): Useful political contact at court.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    skeleton = build_npc_panel_skeleton(result, result.event.attendee_ids[0])

    assert skeleton["people_here"]
    assert "Useful political contact" not in json.dumps(skeleton["conversation"]["sensitive"])


def test_npc_panel_routes_goal_guarded_fact_and_unresolved_hook_exclusively(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Whispers\n"
        "- **Hidden Ledger:** Damien keeps blackmail leverage that could expose a secret debt.\n"
        "### Plots and Schemes\n"
        "- **Warning Courier:** Damien wants [[Annabelle]] to carry a warning tonight.\n"
        "### Unresolved Business\n"
        "- **Old Accounting:** Damien owes an old favor.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    skeleton = build_npc_panel_skeleton(result, result.event.attendee_ids[0])

    assert "Hidden Ledger" in json.dumps(skeleton["conversation"]["sensitive"])
    assert "Warning Courier" in json.dumps(skeleton["tonight"]["current_desire"])
    assert "Old Accounting" in json.dumps(skeleton["interesting_detail"])


def test_npc_panel_does_not_promote_secret_or_goal_into_hook(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Whispers\n"
        "- **Hidden Ledger:** Damien keeps blackmail leverage that could expose a secret debt.\n"
        "### Plots and Schemes\n"
        "- **Warning Courier:** Damien wants [[Annabelle]] to carry a warning tonight.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    skeleton = build_npc_panel_skeleton(result, result.event.attendee_ids[0])

    assert "Hidden Ledger" in json.dumps(skeleton["conversation"]["sensitive"])
    assert "Warning Courier" in json.dumps(skeleton["tonight"]["current_desire"])
    assert skeleton["interesting_detail"] is None
    assert skeleton["presentation_suppressed"] == []
    panel = npc_panel_from_skeleton(skeleton)
    assert panel["presentation_suppressed"] == []


def test_npc_panel_hook_is_empty_when_all_hook_candidates_duplicate_sensitive(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Whispers\n"
        "- **Hidden Ledger:** Damien keeps blackmail leverage that could expose a secret debt.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    skeleton = build_npc_panel_skeleton(result, result.event.attendee_ids[0])
    panel = npc_panel_from_skeleton(skeleton)

    assert skeleton["interesting_detail"] is None
    assert panel["useful_hook"] == ""
    assert skeleton["presentation_suppressed"] == []


def test_panel_hook_dedupe_is_provenance_based_not_fuzzy_text() -> None:
    sensitive = [
        {
            "item_id": "sensitive-1",
            "section": "sensitive",
            "type": "rumor",
            "fact_scope": "self",
            "source_npc_id": "npc_a",
            "target_npc_id": None,
            "mentioned_npc_ids": [],
            "characters": ["npc_a"],
            "display_label": "Rumor",
            "summary": "Damien keeps a hidden ledger.",
            "sources": [{"source_id": "src_1"}],
        }
    ]
    candidate = {
        **sensitive[0],
        "item_id": "candidate-1",
        "source_npc_id": "npc_a",
        "summary": "Damien keeps the hidden ledger close.",
        "sources": [{"source_id": "src_2"}],
    }

    selected, suppressed = club_generation._select_panel_hook_items(
        [candidate],
        sensitive,
        identity_by_id={"npc_a": {"display_name": "Damien"}},
    )

    assert selected
    assert selected[0]["sources"][0]["source_id"] == "src_2"
    assert suppressed == []


def test_npc_panel_skeleton_does_not_repeat_facts_across_sections(tmp_path: Path) -> None:
    alexa = tmp_path / "Alexa Santos.md"
    son = tmp_path / "Son.md"
    kevin = tmp_path / "Kevin Jackson.md"
    alexa.write_text(
        "### Plots and Schemes\n"
        "- **Dead-Eyed Hound:** Alexa protects Council interests.\n"
        "- **Corrosive Network:** As a Hound for [[Kevin Jackson]], Alexa coordinates court attacks.\n"
        "### Relationships\n"
        "- **[[Son]] (Revulsion):** Alexa fears being compared to Son.\n",
        encoding="utf-8",
    )
    son.write_text("Affiliation: Oracle\n", encoding="utf-8")
    kevin.write_text("Affiliation: Executives\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(alexa), str(son), str(kevin)], seed=1, use_ai=False)

    skeleton = build_npc_panel_skeleton(result, result.event.attendee_ids[0])
    panel = npc_panel_from_skeleton(skeleton)
    source_ids: list[str] = []
    for item in panel["people_here"]:
        source_ids.append(item["sources"][0]["source_id"])
    for item in panel["conversation"]["likely_subjects"]:
        source_ids.append(item["sources"][0]["source_id"])
    for item in panel["conversation"]["sensitive"]:
        source_ids.append(item["sources"][0]["source_id"])

    assert len(source_ids) == len(set(source_ids))
    assert not isinstance(panel.get("interesting_detail"), dict) or panel["interesting_detail"]["sources"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("item_id", "item_id"),
        ("section", "section"),
        ("display_label", "display_label"),
    ],
)
def test_ai_npc_panel_rejects_core_owned_grounded_field_mutations(mutation: str, message: str) -> None:
    item = {
        "item_id": "panel-item-1",
        "section": "people_here",
        "type": "blackmail",
        "fact_scope": "relationship",
        "source_npc_id": "damien",
        "target_npc_id": "annabelle",
        "mentioned_npc_ids": [],
        "characters": ["damien", "annabelle"],
        "display_label": "Blackmail",
        "summary": "Damien has blackmail material about Annabelle.",
        "prep_text": "Damien can pressure Annabelle with blackmail tonight.",
        "sources": [{"source_id": "src_1"}],
    }
    skeleton = {
        "kind": "npc_panel",
        "npc_id": "damien",
        "identity": {},
        "personality": [],
        "current_read": "Damien has limited attendee-specific prep in the current indexes.",
        "people_here": [item],
        "conversation": {"likely_subjects": [], "sensitive": []},
        "interesting_detail": None,
        "source_ids": ["src_1"],
    }
    panel = {
        "npc_id": "damien",
        "name": "Damien",
        "identity": {},
        "personality": [],
        "current_read": "Damien has limited attendee-specific prep in the current indexes.",
        "tonight": {},
        "people_here": [_clone_json(item)],
        "likely_conversation": [],
        "conversation": {"likely_subjects": [], "sensitive": []},
        "sensitive_subjects": [],
        "interesting_detail": "",
        "useful_hook": "",
    }
    panel["people_here"][0][mutation] = f"changed-{mutation}"

    with pytest.raises(ClubGenerationError, match=message):
        club_generation.validate_ai_npc_panel(panel, source_ids={"src_1"}, npc_id="damien", skeleton=skeleton)


def test_ai_npc_panel_rejects_provider_prep_text_mutation() -> None:
    item = {
        "item_id": "panel-item-1",
        "section": "people_here",
        "type": "blackmail",
        "fact_scope": "relationship",
        "source_npc_id": "damien",
        "target_npc_id": "annabelle",
        "mentioned_npc_ids": [],
        "characters": ["damien", "annabelle"],
        "display_label": "Blackmail",
        "summary": "Damien has blackmail material about Annabelle.",
        "prep_text": "Original prep.",
        "sources": [{"source_id": "src_1"}],
    }
    skeleton = {
        "kind": "npc_panel",
        "npc_id": "damien",
        "identity": {},
        "personality": [],
        "current_read": "Damien has limited attendee-specific prep in the current indexes.",
        "people_here": [item],
        "conversation": {"likely_subjects": [], "sensitive": []},
        "interesting_detail": None,
        "source_ids": ["src_1"],
    }
    changed = _clone_json(item)
    changed["prep_text"] = "Damien can pressure Annabelle with blackmail tonight."

    with pytest.raises(ClubGenerationError, match="prep_text must match"):
        club_generation.validate_ai_npc_panel(
            {
                "npc_id": "damien",
                "name": "Damien",
                "identity": {},
                "personality": [],
                "current_read": "Damien has limited attendee-specific prep in the current indexes.",
                "tonight": {},
                "people_here": [changed],
                "likely_conversation": [],
                "conversation": {"likely_subjects": [], "sensitive": []},
                "sensitive_subjects": [],
                "interesting_detail": "",
                "useful_hook": "",
            },
            source_ids={"src_1"},
            npc_id="damien",
            skeleton=skeleton,
        )


def test_skeleton_validation_rejects_item_id_source_and_section_changes(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)
    skeleton = build_dashboard_skeleton(result)
    dashboard = dashboard_from_skeleton(skeleton)
    bad = dict(dashboard)
    moved = dict(bad["possible_drama"][0])
    moved["item_id"] = "changed"
    bad["possible_drama"] = [moved]

    with pytest.raises(ClubGenerationError, match="item_id"):
        validate_dashboard(bad, source_ids=set(skeleton["source_ids"]), attendee_ids=set(skeleton["attendee_ids"]), skeleton=skeleton)

    bad_source = dict(dashboard)
    source_changed = dict(bad_source["possible_drama"][0])
    source_changed["sources"] = [{"source_id": "not-real"}]
    bad_source["possible_drama"] = [source_changed]

    with pytest.raises(ClubGenerationError, match="unknown source_id|sources must match"):
        validate_dashboard(bad_source, source_ids=set(skeleton["source_ids"]), attendee_ids=set(skeleton["attendee_ids"]), skeleton=skeleton)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("item_id", "item_id"),
        ("section", "section"),
        ("type", "type"),
        ("fact_scope", "fact_scope"),
        ("source_npc_id", "source_npc_id"),
        ("target_npc_id", "target_npc_id"),
        ("mentioned_npc_ids", "mentioned_npc_ids"),
        ("characters", "characters"),
        ("display_label", "display_label"),
        ("summary", "summary"),
        ("sources", "sources"),
    ],
)
def test_skeleton_validation_rejects_immutable_grounded_field_mutations(mutation: str, message: str) -> None:
    item = {
        "item_id": "item-1",
        "section": "possible_drama",
        "type": "blackmail",
        "fact_scope": "relationship",
        "source_npc_id": "damien",
        "target_npc_id": "annabelle",
        "mentioned_npc_ids": ["gossip", "sheriff"],
        "characters": ["damien", "annabelle"],
        "display_label": "Blackmail",
        "summary": "Damien has blackmail material about Annabelle.",
        "prep_text": "Damien can use Annabelle's blackmail problem tonight.",
        "sources": [{"source_id": "src_1"}, {"source_id": "src_2"}],
    }
    skeleton = {"kind": "dashboard", "possible_drama": [item], "source_ids": ["src_1", "src_2"], "attendee_ids": ["damien", "annabelle"]}
    dashboard = {
        "event": {},
        "first_impression": "",
        "social_map": [],
        "possible_drama": [_clone_json(item)],
    }
    mutated = dashboard["possible_drama"][0]
    if mutation in {"characters", "mentioned_npc_ids", "sources"}:
        mutated[mutation] = list(reversed(mutated[mutation]))
    else:
        mutated[mutation] = f"changed-{mutation}"

    with pytest.raises(ClubGenerationError, match=message):
        validate_dashboard(dashboard, source_ids={"src_1", "src_2"}, attendee_ids={"damien", "annabelle"}, skeleton=skeleton)


def test_skeleton_validation_allows_only_prep_text_mutation() -> None:
    item = {
        "item_id": "item-1",
        "section": "possible_drama",
        "type": "blackmail",
        "fact_scope": "relationship",
        "source_npc_id": "damien",
        "target_npc_id": "annabelle",
        "mentioned_npc_ids": [],
        "characters": ["damien", "annabelle"],
        "display_label": "Blackmail",
        "summary": "Damien has blackmail material about Annabelle.",
        "prep_text": "Original prep.",
        "sources": [{"source_id": "src_1"}],
    }
    skeleton = {"kind": "dashboard", "possible_drama": [item], "source_ids": ["src_1"], "attendee_ids": ["damien", "annabelle"]}
    changed = _clone_json(item)
    changed["prep_text"] = "The PCs can catch Damien turning the blackmail into pressure tonight."

    validate_dashboard(
        {"event": {}, "first_impression": "", "social_map": [], "possible_drama": [changed]},
        source_ids={"src_1"},
        attendee_ids={"damien", "annabelle"},
        skeleton=skeleton,
    )


def test_ai_dashboard_rehydrates_canonical_grounded_fields_before_validation(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")

    def paraphrased_dashboard(skeleton):
        dashboard = dashboard_from_skeleton(skeleton)
        item = dict(dashboard["possible_drama"][0])
        item["summary"] = "AI paraphrased this forbidden canonical field."
        item["prep_text"] = "Damien can turn Annabelle's secret private into pressure tonight."
        dashboard["possible_drama"] = [item]
        dashboard["possible_pressure"] = [item]
        return dashboard

    provider = ScriptedSkeletonProvider([paraphrased_dashboard])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    result = service.build_event_result([str(damien), str(annabelle)], seed=1, force=True)

    assert result.event.metadata["generation_mode"] == "ai"
    pressure = result.event.dashboard["possible_drama"][0]
    assert pressure["summary"] == "Annabelle (Blackmail): Keeps blackmail material about Annabelle's secret private."
    assert pressure["display_label"] == "Blackmail"
    assert pressure["prep_text"] == (
        "Possible pressure — Annabelle (Blackmail): Keeps blackmail material about Annabelle's secret private."
    )
    assert result.event.dashboard["possible_pressure"] == [pressure["prep_text"]]


def test_ai_npc_panel_keeps_presentation_separate_from_canonical_grounded_fields(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "Affiliation: Dockworkers\n"
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Keeps blackmail material about Annabelle's secret private.\n"
        "### Plots and Schemes\n"
        "- Pressure Play: Use Annabelle's private secret to control Atrium votes.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    base_service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=SkeletonEchoProvider())
    result = base_service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)
    npc_id = result.identities[0].npc_id

    def presented_panel(skeleton):
        response = _panel_presentation_response(skeleton)
        response["presentation"]["people_here"][0]["text"] = "Treat Annabelle as a live source of leverage."
        response["presentation"]["agenda"]["text"] = "Turn the private secret into voting leverage."
        return response

    provider = ScriptedSkeletonProvider([presented_panel])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    panel = service.build_npc_panel_from_result(result, npc_id, use_ai=True, force=True)

    assert panel["metadata"]["generation_mode"] == "ai"
    assert panel["people_here"][0]["summary"] == "Annabelle (Blackmail): Keeps blackmail material about Annabelle's secret private."
    assert panel["people_here"][0]["prep_text"] == (
        "Damien has leverage over Annabelle: Keeps blackmail material about Annabelle's secret private."
    )
    assert panel["presentation"]["people_here"][0]["text"] == "Treat Annabelle as a live source of leverage."
    assert panel["presentation"]["agenda"]["text"] == "Turn the private secret into voting leverage."
    assert panel["tonight"]["current_desire"]["summary"] == "Pressure Play: Use Annabelle's private secret to control Atrium votes."
    assert panel["tonight"]["current_desire"]["prep_text"] == (
        "May be focused on — Pressure Play: Use Annabelle's private secret to control Atrium votes."
    )


def test_ai_npc_panel_sends_compact_sourced_demeanor_basis_to_provider(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    damien.write_text("### Demeanor\nWatchful\n", encoding="utf-8")
    bootstrap = CanonicalGenerationService(
        {},
        cache_root=tmp_path / ".club-cache",
        vault_root=tmp_path,
        provider=FakeProvider([]),
    )
    result = bootstrap.build_event_result([str(damien)], seed=1, use_ai=False)
    npc_id = result.event.attendee_ids[0]
    provider = SkeletonEchoProvider()
    service = CanonicalGenerationService(
        {},
        cache_root=tmp_path / ".club-cache",
        vault_root=tmp_path,
        provider=provider,
    )

    panel = service.build_npc_panel_from_result(result, npc_id, use_ai=True, force=True)

    prompt_skeleton = _skeleton_from_prompt(provider.prompts[0])
    assert prompt_skeleton["personality"] == []
    assert prompt_skeleton["portrayal_basis"] == {"demeanor": "Watchful"}
    assert "current_read_basis" not in prompt_skeleton
    assert panel["metadata"]["generation_mode"] == "ai"
    assert panel["presentation"]["play_cue"] == "Keep the delivery clipped and watchful."
    assert len(provider.prompts) == 1


def test_unsupported_interesting_detail_string_is_rejected() -> None:
    with pytest.raises(ClubGenerationError, match="interesting_detail"):
        validate_npc_panel(
            {
                "npc_id": "gengis",
                "people_here": [],
                "conversation": {"likely_subjects": [], "sensitive": []},
                "interesting_detail": "Critias and Damien have marked Gengis for death.",
            },
            source_ids={"src_1"},
            npc_id="gengis",
        )


def test_room_facing_social_map_labels_are_not_generic(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("Affiliation: Dockworkers\n### Relationships\n- [[Annabelle]] (Distrust): Political maneuvering.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Dockworkers\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    skeleton = build_dashboard_skeleton(result)

    assert skeleton["room_groups"][0]["location"] == "Distrust pressure point"
    assert "Connected Group" not in skeleton["room_groups"][0]["location"]
    assert "Damien" in skeleton["room_groups"][0]["summary"]
    assert "Annabelle" in skeleton["room_groups"][0]["summary"]
    assert "sit at the center of distrust pressure" in skeleton["room_groups"][0]["summary"]
    assert "grounded relationship edges" not in skeleton["room_groups"][0]["summary"]


def test_connected_components_social_map_groups_attendees_stably() -> None:
    relationships = [
        {"characters": ["b", "a"], "summary": "linked", "sources": [{"source_id": "s1"}]},
        {"characters": ["d", "c"], "summary": "linked", "sources": [{"source_id": "s2"}]},
        {"characters": ["a", "b"], "summary": "duplicate direction", "sources": [{"source_id": "s3"}]},
    ]

    first = cluster_social_map(["a", "b", "c", "d", "e"], relationships)
    second = cluster_social_map(["a", "b", "c", "d", "e"], relationships)

    assert first == second
    assert first[0]["npc_ids"] == ["a", "b"]
    assert first[1]["npc_ids"] == ["c", "d"]
    assert first[2]["location"] == "Circulating / Unanchored"
    assert first[2]["npc_ids"] == ["e"]


def test_top_connection_ranking_uses_score_then_display_name_then_id() -> None:
    identities = {
        "a": {"display_name": "Zed"},
        "b": {"display_name": "Beta"},
        "c": {"display_name": "Ada"},
        "d": {"display_name": "Delta"},
        "e": {"display_name": "Gamma"},
        "f": {"display_name": "Lambda"},
    }
    items = [
        {"type": "distrust", "characters": ["e", "f"], "summary": "Tie later name", "sources": [{"source_id": "s1"}]},
        {"type": "enemy", "characters": ["c", "d"], "summary": "Higher", "sources": [{"source_id": "s2"}]},
        {"type": "distrust", "characters": ["a", "b"], "summary": "Tie earlier name", "sources": [{"source_id": "s3"}]},
    ]

    ranked = rank_connections(items, identity_by_id=identities)

    assert [item["characters"] for item in ranked] == [["c", "d"], ["b", "a"], ["e", "f"]]


def test_dashboard_ranking_prefers_leverage_and_betrayal_over_generic_useful_ties() -> None:
    identities = {
        "a": {"display_name": "A"},
        "b": {"display_name": "B"},
        "c": {"display_name": "C"},
        "d": {"display_name": "D"},
        "e": {"display_name": "E"},
        "f": {"display_name": "F"},
    }
    items = [
        {"type": "business", "characters": ["a", "b"], "summary": "Useful political contact.", "sources": [{"source_id": "s1"}]},
        {"type": "ally", "characters": ["c", "d"], "summary": "Reliable court ally.", "sources": [{"source_id": "s2"}]},
        {"type": "rivalry", "characters": ["e", "f"], "summary": "Betrayal leverage could expose a secret debt.", "sources": [{"source_id": "s3"}]},
    ]

    ranked = rank_connections(items, identity_by_id=identities)

    assert ranked[0]["characters"] == ["e", "f"]
    assert ranked[-1]["summary"] == "Useful political contact."


@pytest.mark.parametrize(
    ("fact_type", "summary"),
    [
        ("danger", "A personal danger could surface if the secret is exposed."),
        ("blackmail", "Blackmail material could become leverage."),
        ("exposure", "A secret exposure could become leverage."),
        ("coercion", "Damien coerces Annabelle into obedience."),
        ("debt", "Damien owes Annabelle a obligation."),
        ("coercive bond", "A coercive bond ties them together."),
        ("manipulation", "Damien manipulates Annabelle as a tool."),
        ("grave misconduct", "Damien may commit grave misconduct against Annabelle."),
        ("betrayal", "A betrayal could be exposed."),
        ("dependency", "Annabelle depends on Damien for protection."),
        ("paramour", "Damien is Annabelle's paramour."),
        ("enemy", "Bitter feud and sabotage at court."),
    ],
)
@pytest.mark.parametrize("generic_type", ["political opposition", "useful", "business", "contact"])
def test_visible_rank_tiers_put_supported_stakes_above_generic_political_usefulness(generic_type: str, fact_type: str, summary: str) -> None:
    generic = {
        "type": generic_type,
        "characters": ["a", "b"],
        "summary": "Useful political contact at court.",
        "sources": [{"source_id": "src_generic"}],
    }
    supported = {
        "type": fact_type,
        "characters": ["c", "d"],
        "summary": summary,
        "sources": [{"source_id": "src_supported"}],
    }

    assert club_generation._visible_rank_tier(supported) > club_generation._visible_rank_tier(generic)


def test_dashboard_event_cache_key_changes_when_canonical_skeleton_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    first = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)
    original = club_generation._prep_text_for_item

    def revised_prep_text(item, section, *, identity_by_id):
        text = original(item, section, identity_by_id=identity_by_id)
        if section == "possible_drama":
            return f"{text} Curation marker."
        return text

    monkeypatch.setattr(club_generation, "_prep_text_for_item", revised_prep_text)
    second = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    assert first.event.cache_key != second.event.cache_key


def test_prompt_versions_participate_in_dashboard_and_panel_cache_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    provider = SkeletonEchoProvider()
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    first = service.build_event_result([str(damien), str(annabelle)], seed=1)
    panel_npc_id = first.event.attendee_ids[0]
    service.build_npc_panel_from_result(first, panel_npc_id)
    monkeypatch.setattr(club_generation, "CLUB_DASHBOARD_PROMPT_VERSION", "club_dashboard_prompt_changed")
    monkeypatch.setattr(club_generation, "CLUB_PANEL_PROMPT_VERSION", "club_panel_prompt_changed")
    second = service.build_event_result([str(damien), str(annabelle)], seed=1)
    service.build_npc_panel_from_result(second, panel_npc_id)

    assert first.event.cache_key != second.event.cache_key
    assert len(provider.prompts) == 4


def test_dashboard_validation_rejects_ungrounded_items() -> None:
    with pytest.raises(ClubGenerationError, match="sources"):
        validate_dashboard(
            {
                "event": {},
                "first_impression": "",
                "social_map": [],
                "rumors": [{"summary": "Unsupported rumor"}],
            },
            source_ids={"src_1"},
            attendee_ids={"npc_1"},
        )


def test_dashboard_validation_rejects_timeline_and_rumor_truth_rating() -> None:
    with pytest.raises(ClubGenerationError, match="timeline|truth ratings"):
        validate_dashboard(
            {
                "event": {"venue": "Club"},
                "first_impression": "",
                "social_map": [],
                "timeline": ["10pm Damien arrives"],
                "rumors": [{"summary": "Supported rumor", "truth_rating": "true", "sources": [{"source_id": "src_1"}]}],
            },
            source_ids={"src_1"},
            attendee_ids={"npc_1"},
        )


def test_npc_panel_validation_rejects_unsupported_pc_attitude() -> None:
    with pytest.raises(ClubGenerationError, match="attitude_toward_pcs"):
        validate_npc_panel(
            {
                "npc_id": "npc_1",
                "people_here": [],
                "tonight": {"attitude_toward_pcs": "Damien dislikes the PC he has never met."},
            },
            source_ids={"src_1"},
            npc_id="npc_1",
        )


def test_retry_prompt_includes_validation_error(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")

    provider = SkeletonEchoProvider(invalid_first={"event": {}, "first_impression": "", "social_map": [], "possible_drama": [{"summary": "No source"}]})
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    service.build_event([str(damien), str(annabelle)], seed=1)

    assert len(provider.prompts) == 2
    assert "previous JSON failed validation" in provider.prompts[1]
    assert "sources" in provider.prompts[1]


def test_ai_dashboard_ignores_provider_room_prose_without_retry(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")

    def bad_response(skeleton):
        dashboard = dashboard_from_skeleton(skeleton)
        dashboard["room_situation"] = "Source ID: src_1"
        return dashboard

    provider = ScriptedSkeletonProvider([bad_response])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    event = service.build_event([str(damien), str(annabelle)], seed=1)

    assert event.metadata["generation_mode"] == "ai"
    assert len(provider.prompts) == 1
    assert "Source ID" not in event.dashboard["room_situation"]
    assert event.dashboard["room_situation"].startswith("2 attendees are present.")


def test_ai_npc_panel_rejects_provider_factual_fields_and_keeps_deterministic_current_read(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "Affiliation: Dockworkers\n"
        "### Plots and Schemes\n"
        "- **Warning Courier:** Damien wants to warn [[Annabelle]] before court moves against her.\n"
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    bootstrap = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = bootstrap.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)
    expected_read = build_npc_panel_skeleton(result, result.event.attendee_ids[0])["current_read"]

    def bad_response(skeleton):
        response = _panel_presentation_response(skeleton)
        response["current_read"] = "Damien is hiding a private murder nobody in the selected panel skeleton supports."
        return response

    def good_response(skeleton):
        return _panel_presentation_response(skeleton)

    provider = ScriptedSkeletonProvider([bad_response, good_response])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    panel = service.build_npc_panel_from_result(result, result.event.attendee_ids[0])

    assert panel["metadata"]["generation_mode"] == "ai"
    assert len(provider.prompts) == 2
    assert panel["current_read"] == expected_read


def test_ai_dashboard_visible_fields_required_only_for_grounded_sections(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)
    skeleton = build_dashboard_skeleton(result)
    dashboard = dashboard_from_skeleton(skeleton)
    dashboard["possible_pressure"] = []

    with pytest.raises(ClubGenerationError, match="possible_pressure"):
        club_generation.validate_ai_dashboard(dashboard, source_ids=set(skeleton["source_ids"]), attendee_ids=set(skeleton["attendee_ids"]), skeleton=skeleton)

    empty_pressure_skeleton = _clone_json(skeleton)
    empty_pressure_skeleton["possible_drama"] = []
    allowed = dashboard_from_skeleton(empty_pressure_skeleton)
    allowed["possible_pressure"] = []

    club_generation.validate_ai_dashboard(
        allowed,
        source_ids=set(empty_pressure_skeleton["source_ids"]),
        attendee_ids=set(empty_pressure_skeleton["attendee_ids"]),
        skeleton=empty_pressure_skeleton,
    )


def test_ai_npc_panel_cues_required_only_for_grounded_sections(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "Affiliation: Dockworkers\n"
        "### Plots and Schemes\n"
        "- **Warning Courier:** Damien wants to warn [[Annabelle]] before court moves against her.\n"
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)
    npc_id = result.event.attendee_ids[0]
    skeleton = build_npc_panel_skeleton(result, npc_id)
    response = _panel_presentation_response(skeleton)
    response["presentation"]["people_here"][0]["text"] = ""

    with pytest.raises(ClubGenerationError, match="people_here"):
        club_generation.postprocess_npc_presentation(response, skeleton)

    empty_people_skeleton = _clone_json(skeleton)
    empty_people_skeleton["people_here"] = []
    allowed = _panel_presentation_response(empty_people_skeleton)

    club_generation.postprocess_npc_presentation(allowed, empty_people_skeleton)


def test_build_event_can_use_fast_deterministic_dashboard_without_model_call(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    provider = FakeProvider([])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    event = service.build_event([str(damien), str(annabelle)], seed=1, use_ai=False)

    assert event.dashboard["event"]["venue"] == "The Lantern Room"
    assert event.dashboard["possible_drama"]
    assert provider.prompts == []


def test_canonical_build_event_uses_ai_and_records_metadata(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    provider = SkeletonEchoProvider()
    service = CanonicalGenerationService({"model": {"name": "test-model"}}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    event = service.build_event([str(damien), str(annabelle)], seed=1)

    assert event.metadata["generation_mode"] == "ai"
    assert event.metadata["model_name"] == "test-model"
    assert len(provider.prompts) == 1
    assert "Skeleton JSON" in provider.prompts[0]


def test_dashboard_prompt_context_is_attendee_scoped_and_compact(tmp_path: Path) -> None:
    cache = ClubCacheService(tmp_path / ".club-cache")
    registry = NpcIdentityRegistry(cache, vault_root=tmp_path)
    store = ClubIndexStore(cache)
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    absent = tmp_path / "Absent.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Distrust): Political maneuvering.\n"
        "- [[Absent]] (Enemy): This should stay out of prompt context.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    absent.write_text("Affiliation: Executives\n", encoding="utf-8")
    identities = [registry.get_or_create(damien), registry.get_or_create(annabelle)]
    indexes = [store.get_or_build(identities[0], damien), store.get_or_build(identities[1], annabelle)]

    context = build_attendee_context(identities, indexes)
    prompt_context = dashboard_prompt_context(context, build_source_map(indexes))
    text = json.dumps(prompt_context)

    assert len(prompt_context["source_map"]) == 1
    assert "Political maneuvering" in text
    assert "This should stay out of prompt context" not in text
    assert all("relationships" not in index for index in prompt_context["indexes"])
    assert all("path" not in identity for identity in prompt_context["identities"])


def test_dashboard_prompt_context_caps_fact_volume_deterministically() -> None:
    identities = [
        {"npc_id": "a", "display_name": "A"},
        {"npc_id": "b", "display_name": "B"},
    ]
    relationships = [
        {"type": "enemy", "characters": ["a", "b"], "summary": "Critical connection.", "sources": [{"source_id": "keep"}]},
    ]
    facts = [
        {"type": "recent_event", "characters": ["a"], "summary": f"Low value fact {idx:03d}.", "sources": [{"source_id": f"low{idx:03d}"}]}
        for idx in range(95)
    ]
    source_map = {
        source_id: {"source": {"source_id": source_id, "section": "Test"}, "npc_id": "a", "summary": source_id}
        for source_id in ["keep", *[f"low{idx:03d}" for idx in range(95)]]
    }

    context = dashboard_prompt_context(
        {
            "attendee_ids": ["a", "b"],
            "identities": identities,
            "indexes": [],
            "attendee_relationships": relationships,
            "attendee_facts": [*relationships, *facts],
            "source_ids": sorted(source_map),
        },
        source_map,
    )
    second = dashboard_prompt_context(
        {
            "attendee_ids": ["a", "b"],
            "identities": list(reversed(identities)),
            "indexes": [],
            "attendee_relationships": list(reversed(relationships)),
            "attendee_facts": list(reversed([*relationships, *facts])),
            "source_ids": sorted(source_map),
        },
        source_map,
    )

    assert context["attendee_facts"] == second["attendee_facts"]
    assert len(context["attendee_facts"]) == 80
    assert "keep" in context["source_map"]
    assert len(context["source_map"]) == 80


def test_cached_ai_dashboard_skips_provider(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("### Relationships\n- **[[Annabelle]] (Distrust):** Political maneuvering.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    first_provider = SkeletonEchoProvider()
    first_service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=first_provider)
    first_service.build_event([str(damien), str(annabelle)], seed=1)
    second_provider = FakeProvider([])
    second_service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=second_provider)

    cached = second_service.build_event([str(damien), str(annabelle)], seed=1)

    assert cached.metadata["from_cache"] is True
    assert cached.metadata["generation_mode"] == "ai"
    assert cached.metadata["ai_attempted"] is False
    assert cached.metadata["fallback_used"] is False
    assert cached.metadata["ai_error_type"] is None
    assert cached.metadata["ai_error_stage"] is None
    assert second_provider.prompts == []


def test_ai_dashboard_failure_uses_deterministic_fallback_without_caching_it(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    provider = RaisingProvider("missing key")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    result = service.build_event_result([str(damien), str(annabelle)], seed=1)

    assert result.event.metadata["generation_mode"] == "deterministic_fallback"
    assert result.event.metadata["ai_attempted"] is True
    assert result.event.metadata["fallback_used"] is True
    assert result.event.metadata["ai_error_type"] == "unknown_ai_error"
    assert result.event.metadata["ai_error_stage"] == "ai_generation"
    assert "missing key" in result.event.metadata["validation_error"]
    assert result.event.dashboard["possible_drama"]
    _assert_no_ai_unavailable_marker(result.event.dashboard)

    retry_provider = RaisingProvider("still missing")
    retry_service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=retry_provider)
    retry_service.build_event_result([str(damien), str(annabelle)], seed=1)
    assert retry_provider.prompts


def test_dashboard_retry_uses_fresh_club_json_request_options(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")

    def bad_response(skeleton):
        dashboard = dashboard_from_skeleton(skeleton)
        dashboard["possible_drama"][0]["item_id"] = "fabricated-item"
        return dashboard

    provider = ScriptedSkeletonProvider([bad_response, lambda skeleton: dashboard_from_skeleton(skeleton)])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    result = service.build_event_result([str(damien), str(annabelle)], seed=1)

    assert result.event.metadata["generation_mode"] == "ai"
    assert len(provider.request_options) == 2
    _assert_club_json_request_options(provider.request_options[0])
    _assert_club_json_request_options(provider.request_options[1])
    assert provider.request_options[0] is not provider.request_options[1]
    assert provider.request_options[0]["response_format"] is not provider.request_options[1]["response_format"]
    assert provider.request_options[0]["thinking"] is not provider.request_options[1]["thinking"]


def test_npc_panel_retry_uses_fresh_club_json_request_options(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    bootstrap = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=SkeletonEchoProvider())
    result = bootstrap.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)
    npc_id = result.event.attendee_ids[0]

    def bad_panel(skeleton):
        response = _panel_presentation_response(skeleton)
        response["presentation"]["people_here"][0]["item_id"] = "fabricated-item-id"
        return response

    provider = ScriptedSkeletonProvider([bad_panel, _panel_presentation_response])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    panel = service.build_npc_panel_from_result(result, npc_id, use_ai=True, force=True)

    assert panel["metadata"]["generation_mode"] == "ai"
    assert len(provider.request_options) == 2
    _assert_club_json_request_options(provider.request_options[0])
    _assert_club_json_request_options(provider.request_options[1])
    assert provider.request_options[0] is not provider.request_options[1]
    assert provider.request_options[0]["response_format"] is not provider.request_options[1]["response_format"]
    assert provider.request_options[0]["thinking"] is not provider.request_options[1]["thinking"]


@pytest.mark.parametrize(
    "message",
    [
        "DeepSeek response did not include non-empty choices[0].message.content. Diagnostics: content_present=False; content_length=0.",
        "DeepSeek response did not include non-empty choices[0].message.content. Diagnostics: content_present=True; content_length=0.",
    ],
)
def test_deepseek_empty_content_failure_sets_typed_metadata_and_clean_fallback(tmp_path: Path, message: str) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    forbidden = "PROMPT_MARKER_DO_NOT_LEAK sk-test-key-material-do-not-leak RAW_PROVIDER_BODY_DO_NOT_LEAK"
    provider = RaisingProvider(f"{message} {forbidden}")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    result = service.build_event_result([str(damien), str(annabelle)], seed=1)

    assert result.event.metadata["generation_mode"] == "deterministic_fallback"
    assert result.event.metadata["ai_attempted"] is True
    assert result.event.metadata["fallback_used"] is True
    assert result.event.metadata["ai_error_type"] == "provider_malformed_response"
    assert result.event.metadata["ai_error_stage"] == "provider_response"
    assert result.event.metadata["validation_error"].startswith(
        "DeepSeek response did not include non-empty choices[0].message.content."
    )
    for marker in forbidden.split():
        assert marker not in result.event.metadata["validation_error"]
        assert marker not in safe_debug_json(result.to_debug_dict())
    _assert_no_ai_unavailable_marker(result.event.dashboard)
    assert not list((tmp_path / ".club-cache" / "events").glob("*.json"))


def test_pre_change_ai_dashboard_cache_entry_is_not_returned_under_current_request_contract(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    cache_root = tmp_path / ".club-cache"
    stale_service = CanonicalGenerationService({}, cache_root=cache_root, vault_root=tmp_path, provider=SkeletonEchoProvider())
    identities, indexes = stale_service._load_attendees([str(damien), str(annabelle)])
    context = build_attendee_context(identities, indexes)
    source_map = build_source_map(indexes, identities=identities)
    event_seed = 1
    late_arrival_id = club_generation.select_late_arrival(identities, host_path=None, seed=event_seed)
    skeleton = club_generation._build_dashboard_skeleton_from_context(
        context,
        source_map,
        venue="The Lantern Room",
        event_type="social gathering",
        late_arrival_id=late_arrival_id,
    )
    old_cache_key = club_generation.stable_hash(
        {
            "attendee_ids": sorted(identity.npc_id for identity in identities),
            "index_revisions": {index.npc_id: index.sheet_revision_hash for index in indexes},
            "venue": "The Lantern Room",
            "event_type": "social gathering",
            "guest_paths": sorted(str(path) for path in [damien, annabelle]),
            "skeleton": skeleton,
            "schema_version": club_generation.CLUB_DASHBOARD_SCHEMA_VERSION,
            "prompt_version": club_generation.CLUB_DASHBOARD_PROMPT_VERSION,
            "seed": event_seed,
        }
    )
    stale_dashboard = dashboard_from_skeleton(skeleton)
    stale_dashboard["room_situation"] = "STALE AI CACHE DO NOT RETURN"
    ClubCacheService(cache_root).write_json(
        "events",
        f"{old_cache_key}.json",
        data={
            "event_id": "stale-event",
            "attendee_ids": [identity.npc_id for identity in identities],
            "late_arrival_id": late_arrival_id,
            "dashboard": stale_dashboard,
            "cache_key": old_cache_key,
            "seed": event_seed,
            "metadata": {"generation_mode": "ai", "from_cache": False},
        },
    )
    provider = SkeletonEchoProvider()
    service = CanonicalGenerationService({}, cache_root=cache_root, vault_root=tmp_path, provider=provider)

    result = service.build_event_result([str(damien), str(annabelle)], seed=event_seed)

    assert provider.prompts
    assert result.event.metadata["generation_mode"] == "ai"
    assert result.event.metadata["from_cache"] is False
    assert result.event.dashboard["room_situation"] != "STALE AI CACHE DO NOT RETURN"


def test_missing_credentials_failure_sets_config_metadata(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("### Relationships\n- **[[Annabelle]] (Distrust):** Political maneuvering.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    provider = RaisingProvider("DeepSeek API key not provided (config.model.api_key or DEEPSEEK_API_KEY).")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    result = service.build_event_result([str(damien), str(annabelle)], seed=1)

    assert result.event.metadata["generation_mode"] == "deterministic_fallback"
    assert result.event.metadata["ai_attempted"] is False
    assert result.event.metadata["fallback_used"] is True
    assert result.event.metadata["ai_error_type"] == "provider_missing_credentials"
    assert result.event.metadata["ai_error_stage"] == "provider_config"
    _assert_no_ai_unavailable_marker(result.event.dashboard)


def test_timeout_failure_sets_request_stage_metadata(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("### Relationships\n- **[[Annabelle]] (Distrust):** Political maneuvering.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    provider = TimeoutProvider()
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)

    result = service.build_event_result([str(damien), str(annabelle)], seed=1)

    assert result.event.metadata["generation_mode"] == "deterministic_fallback"
    assert result.event.metadata["ai_attempted"] is True
    assert result.event.metadata["fallback_used"] is True
    assert result.event.metadata["ai_error_type"] == "provider_timeout"
    assert result.event.metadata["ai_error_stage"] == "provider_request"
    _assert_no_ai_unavailable_marker(result.event.dashboard)


def test_validation_exhaustion_returns_marked_fallback_without_poisoning_ai_cache(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- **[[Annabelle]] (Blackmail):** Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    cache_root = tmp_path / ".club-cache"

    def bad_response(skeleton):
        dashboard = dashboard_from_skeleton(skeleton)
        dashboard["possible_drama"][0]["item_id"] = "fabricated-item"
        return dashboard

    failing_provider = ScriptedSkeletonProvider([bad_response, bad_response])
    failing_service = CanonicalGenerationService({}, cache_root=cache_root, vault_root=tmp_path, provider=failing_provider)

    fallback_result = failing_service.build_event_result([str(damien), str(annabelle)], seed=1)

    assert fallback_result.event.metadata["generation_mode"] == "deterministic_fallback"
    assert fallback_result.event.metadata["fallback_used"] is True
    assert fallback_result.event.metadata["ai_error_type"] == "ai_output_validation_failed"
    assert fallback_result.event.metadata["ai_error_stage"] == "ai_output_validation"
    _assert_no_ai_unavailable_marker(fallback_result.event.dashboard)
    assert not list((cache_root / "events").glob("*.json"))
    assert len(failing_provider.prompts) == 2

    recovery_provider = SkeletonEchoProvider()
    recovery_service = CanonicalGenerationService({}, cache_root=cache_root, vault_root=tmp_path, provider=recovery_provider)
    recovered = recovery_service.build_event_result([str(damien), str(annabelle)], seed=1)

    assert recovered.event.metadata["generation_mode"] == "ai"
    assert len(recovery_provider.prompts) == 1
    _assert_no_ai_unavailable_marker(recovered.event.dashboard)


def test_oversized_dashboard_prompt_skips_provider_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("### Relationships\n- **[[Annabelle]] (Distrust):** Political maneuvering.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    provider = FakeProvider([])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)
    monkeypatch.setattr("core.club_generation.CLUB_DASHBOARD_PROMPT_TOKEN_BUDGET", 1)

    result = service.build_event_result([str(damien), str(annabelle)], seed=1)

    assert result.event.metadata["generation_mode"] == "deterministic_fallback"
    assert result.event.metadata["prompt_too_large"] is True
    assert result.event.metadata["fallback_used"] is True
    assert result.event.metadata["ai_error_type"] == "prompt_too_large"
    assert result.event.metadata["ai_error_stage"] == "prompt_budget"
    assert result.event.metadata["prompt_budget"] == 1
    assert result.event.metadata["estimated_input_tokens"] > 1
    assert provider.prompts == []


def test_failed_ai_dashboard_regeneration_prefers_cached_ai(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("### Relationships\n- **[[Annabelle]] (Distrust):** Political maneuvering.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    first_service = CanonicalGenerationService(
        {},
        cache_root=tmp_path / ".club-cache",
        vault_root=tmp_path,
        provider=SkeletonEchoProvider(),
    )
    cached_event = first_service.build_event([str(damien), str(annabelle)], seed=1)
    failing_provider = RaisingProvider("regeneration failed")
    failing_service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=failing_provider)

    result = failing_service.build_event_result([str(damien), str(annabelle)], seed=1, force=True)

    assert result.event.dashboard == cached_event.dashboard
    assert result.event.metadata["from_cache"] is True
    assert result.event.metadata["ai_attempted"] is True
    assert result.event.metadata["fallback_used"] is False
    assert result.event.metadata["ai_error_type"] == "unknown_ai_error"
    assert "reused cached AI dashboard" in result.event.metadata["fallback_reason"]


def test_build_npc_panel_can_use_fast_deterministic_panel_without_model_call(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("Affiliation: Dockworkers\n### Relationships\n- **[[Annabelle]] (Distrust):** Political maneuvering.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    provider = FakeProvider([])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)
    event = service.build_event([str(damien), str(annabelle)], seed=1, use_ai=False)
    npc_id = event.attendee_ids[0]

    panel = service.build_npc_panel(event, [str(damien), str(annabelle)], npc_id, use_ai=False)

    assert panel["npc_id"] == npc_id
    assert panel["people_here"]
    assert provider.prompts == []


def test_npc_quick_panel_uses_build_result_without_model_call(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("Affiliation: Dockworkers\n### Personality\nDirect\n### Relationships\n- **[[Annabelle]] (Distrust):** Political maneuvering.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    provider = FakeProvider([])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)

    panel = build_npc_quick_panel(result, result.event.attendee_ids[0])

    assert panel["identity"]["affiliation"] == "Dockworkers"
    assert panel["people_here"]
    assert panel["empty_state"] == ""
    assert provider.prompts == []


def test_npc_panel_prompt_context_only_contains_clicked_scope(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    critias = tmp_path / "Critias.md"
    damien.write_text(
        "Affiliation: Dockworkers\n"
        "### Personality\n"
        "Direct, suspicious\n"
        "### Relationships\n"
        "- [[Annabelle]] (Distrust): Political maneuvering.\n",
        encoding="utf-8",
    )
    annabelle.write_text(
        "Affiliation: Artists\n"
        "### Relationships\n"
        "- [[Critias]] (Ally): Shared philosophy.\n",
        encoding="utf-8",
    )
    critias.write_text("Affiliation: Dockworkers\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle), str(critias)], seed=1, use_ai=False)

    context = build_npc_panel_context(result, result.event.attendee_ids[0])
    text = json.dumps(context)

    assert context["index"]["personality_tags"] == ["direct", "suspicious"]
    assert context["identities"] == [context["identity"]]
    assert len(context["indexes"]) == 1
    assert "relationships" not in context["index"]
    assert "Political maneuvering" in text
    assert "Shared philosophy" not in text
    assert set(context["source_map"]) == set(context["source_ids"])


def test_empty_npc_quick_panel_has_clear_empty_state(tmp_path: Path) -> None:
    loner = tmp_path / "Loner.md"
    loner.write_text("Just a name.\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(loner)], seed=1, use_ai=False)

    panel = build_npc_quick_panel(result, result.event.attendee_ids[0])

    assert panel["people_here"] == []
    assert panel["empty_state"] == "No attendee-specific indexed facts were found for this NPC."


def test_npc_panel_cache_reuses_second_click(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("### Relationships\n- **[[Annabelle]] (Distrust):** Political maneuvering.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")

    bootstrap = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    identities, _indexes = bootstrap._load_attendees([str(damien), str(annabelle)])
    provider = SkeletonEchoProvider()
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)
    event = service.build_event([str(damien), str(annabelle)], seed=1)

    first = service.build_npc_panel(event, [str(damien), str(annabelle)], identities[0].npc_id)
    second = service.build_npc_panel(event, [str(damien), str(annabelle)], identities[0].npc_id)

    assert first["people_here"] == second["people_here"]
    assert first["metadata"]["from_cache"] is False
    assert second["metadata"]["from_cache"] is True
    assert len(provider.prompts) == 2


def test_npc_panel_from_result_force_bypasses_cache(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("### Relationships\n- **[[Annabelle]] (Distrust):** Political maneuvering.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    bootstrap = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    identities, _indexes = bootstrap._load_attendees([str(damien), str(annabelle)])
    provider = SkeletonEchoProvider(panel_summaries=["First.", "Second."])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)
    result = service.build_event_result([str(damien), str(annabelle)], seed=1)

    first = service.build_npc_panel_from_result(result, identities[0].npc_id)
    forced = service.build_npc_panel_from_result(result, identities[0].npc_id, force=True)

    assert first["people_here"][0]["prep_text"] == "Damien distrusts Annabelle: Political maneuvering."
    assert forced["people_here"][0]["prep_text"] == first["people_here"][0]["prep_text"]
    assert len(provider.prompts) == 3


def test_npc_panel_ai_failure_uses_deterministic_fallback(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("Affiliation: Dockworkers\n### Relationships\n- **[[Annabelle]] (Distrust):** Political maneuvering.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    result = service.build_event_result([str(damien), str(annabelle)], seed=1, use_ai=False)
    failing = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=RaisingProvider("panel failed"))

    panel = failing.build_npc_panel_from_result(result, result.event.attendee_ids[0])

    assert panel["metadata"]["generation_mode"] == "deterministic_fallback"
    assert panel["metadata"]["ai_attempted"] is True
    assert panel["metadata"]["fallback_used"] is True
    assert panel["metadata"]["ai_error_type"] == "unknown_ai_error"
    assert "panel failed" in panel["metadata"]["validation_error"]
    _assert_no_ai_unavailable_marker(panel)
    assert panel["people_here"]
    assert not list((tmp_path / ".club-cache" / "npc_panels").glob("*.json"))


def test_failed_npc_panel_regeneration_prefers_cached_ai(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("### Relationships\n- **[[Annabelle]] (Distrust):** Political maneuvering.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    bootstrap = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=FakeProvider([]))
    identities, _indexes = bootstrap._load_attendees([str(damien), str(annabelle)])
    provider = SkeletonEchoProvider(panel_summaries=["Cached panel."])
    service = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=provider)
    result = service.build_event_result([str(damien), str(annabelle)], seed=1)
    cached = service.build_npc_panel_from_result(result, identities[0].npc_id)
    failing = CanonicalGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path, provider=RaisingProvider("regeneration failed"))

    reused = failing.build_npc_panel_from_result(result, identities[0].npc_id, force=True)

    assert reused["people_here"] == cached["people_here"]
    assert reused["metadata"]["from_cache"] is True
    assert reused["metadata"]["ai_attempted"] is True
    assert reused["metadata"]["fallback_used"] is False
    assert reused["metadata"]["ai_error_type"] == "unknown_ai_error"
    assert "reused cached AI panel" in reused["metadata"]["fallback_reason"]


def test_safe_debug_json_redacts_secret_like_keys() -> None:
    text = safe_debug_json(
        {
            "cache_key": "allowed-cache-key",
            "source_id": "allowed-source",
            "api_key": "do-not-copy",
            "nested": {"token": "do-not-copy-token"},
        }
    )

    assert "allowed-cache-key" in text
    assert "allowed-source" in text
    assert "do-not-copy" not in text
    assert "<redacted>" in text


def test_validation_error_sanitizer_removes_sensitive_debug_material() -> None:
    raw = (
        "Traceback (most recent call last): File \"C:\\Users\\ExampleUser\\vault\\Npc.md\", line 1 "
        "Authorization: Bearer sk-secret api_key=sk-test "
        "payload={'messages': [{'role': 'user', 'content': 'full prompt context'}]} "
        "prompt='full prompt text' C:\\Projects\\Example App\\scenesmith\\config\\app.local.yaml "
        + ("detail " * 200)
    )

    clean = club_generation._sanitize_validation_error(raw, limit=180)

    assert "sk-secret" not in clean
    assert "sk-test" not in clean
    assert "full prompt context" not in clean
    assert "full prompt text" not in clean
    assert "C:\\Users" not in clean
    assert "C:\\Projects" not in clean
    assert "line 1" not in clean
    assert "Authorization: Bearer" not in clean
    assert "Traceback <redacted>" in clean
    assert len(clean) <= 180


def test_relationship_digest_changes_when_relationship_changes(tmp_path: Path) -> None:
    cache = ClubCacheService(tmp_path / ".club-cache")
    registry = NpcIdentityRegistry(cache, vault_root=tmp_path)
    store = ClubIndexStore(cache)
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text("### Relationships\n- [[Annabelle]] (Distrust): First.\n", encoding="utf-8")
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")
    identities = [registry.get_or_create(damien), registry.get_or_create(annabelle)]
    context1 = build_attendee_context(identities, [store.get_or_build(identities[0], damien), store.get_or_build(identities[1], annabelle)])

    damien.write_text("### Relationships\n- [[Annabelle]] (Ally): Changed.\n", encoding="utf-8")
    context2 = build_attendee_context(identities, [store.get_or_build(identities[0], damien), store.get_or_build(identities[1], annabelle)])

    assert relationship_digest_for(identities[0].npc_id, context1["attendee_relationships"]) != relationship_digest_for(
        identities[0].npc_id,
        context2["attendee_relationships"],
    )
