from __future__ import annotations

import copy

import pytest

import core.club_generation as club_generation
import tools.debug_club_generation as debug_club_generation
from core.club_generation import ClubBuildResult, build_npc_panel_skeleton, npc_panel_from_skeleton
from core.club_models import ClubEvent, ClubIndex, NpcIdentity


def _identity(npc_id: str, name: str) -> NpcIdentity:
    return NpcIdentity(
        npc_id=npc_id,
        current_path=f"C:/vault/{name}.md",
        display_name=name,
        aliases=(),
        content_fingerprint=f"fingerprint-{npc_id}",
    )


def _index(identity: NpcIdentity) -> ClubIndex:
    return ClubIndex(
        npc_id=identity.npc_id,
        name=identity.display_name,
        path=identity.current_path,
        sheet_revision_hash="revision",
        schema_version="test",
        prompt_version="test",
        affiliation="Dockworkers" if identity.npc_id == "npc_a" else "Artists",
    )


def _fact(
    source_id: str,
    summary: str,
    *,
    fact_type: str,
    source: str = "npc_a",
    target: str | None = None,
    mentioned: tuple[str, ...] = (),
    fact_scope: str | None = None,
) -> dict:
    if fact_scope is None:
        fact_scope = "relationship" if target else ("mention" if mentioned else "self")
    characters = [source]
    if target:
        characters.append(target)
    return {
        "type": fact_type,
        "fact_scope": fact_scope,
        "source_npc_id": source,
        "target_npc_id": target,
        "mentioned_npc_ids": list(mentioned),
        "link_targets": [],
        "characters": characters,
        "summary": summary,
        "sources": [
            {
                "source_id": source_id,
                "character_id": source,
                "path": f"C:/vault/{source}.md",
                "section": "Test Facts",
                "excerpt": summary,
            }
        ],
    }


def _result(*, facts: tuple[dict, ...] = (), relationships: tuple[dict, ...] = ()) -> ClubBuildResult:
    identities = (_identity("npc_a", "Damien"), _identity("npc_b", "Annabelle"))
    source_map = {}
    for item in (*facts, *relationships):
        for source in item.get("sources") or []:
            source_map[source["source_id"]] = {"source": copy.deepcopy(source)}
    return ClubBuildResult(
        event=ClubEvent(
            event_id="event",
            attendee_ids=("npc_a", "npc_b"),
            late_arrival_id="npc_b",
            dashboard={},
            cache_key="cache",
            seed=1,
        ),
        identities=identities,
        indexes_by_id={identity.npc_id: _index(identity) for identity in identities},
        attendee_relationships=relationships,
        attendee_facts=facts,
        source_map=source_map,
        debug_context={},
    )


def _slot_fact_ids(skeleton: dict) -> list[tuple[str, ...]]:
    conversation = skeleton["conversation"]
    items = [
        *skeleton["people_here"],
        *conversation["sensitive"],
        *conversation["likely_subjects"],
    ]
    if isinstance(skeleton.get("interesting_detail"), dict):
        items.append(skeleton["interesting_detail"])
    return [club_generation._canonical_fact_identity(item) for item in items]


def test_panel_reserves_canonical_fact_globally_before_hook_selection() -> None:
    relationship = _fact(
        "source-shared",
        "Damien holds blackmail material over Annabelle.",
        fact_type="blackmail",
        target="npc_b",
    )

    skeleton = build_npc_panel_skeleton(_result(relationships=(relationship,)), "npc_a")
    panel = npc_panel_from_skeleton(skeleton)

    identities = _slot_fact_ids(skeleton)
    assert len(identities) == len(set(identities))
    assert skeleton["people_here"]
    assert skeleton["interesting_detail"] is None
    assert panel["useful_hook"] == ""


def test_incoming_rumors_are_reserved_for_sensitive_only_and_overflow_is_omitted() -> None:
    rumors = tuple(
        _fact(
            f"rumor-{index}",
            f"A quiet claim about Damien number {index}",
            fact_type="rumor",
            source="npc_b",
            mentioned=("npc_a",),
        )
        for index in range(4)
    )

    skeleton = build_npc_panel_skeleton(_result(facts=rumors), "npc_a")

    sensitive = skeleton["conversation"]["sensitive"]
    assert len(sensitive) == 2
    assert all(item["type"] == "rumor" for item in sensitive)
    assert skeleton["conversation"]["likely_subjects"] == []
    assert skeleton["interesting_detail"] is None
    assert "number 2" not in str(skeleton)
    assert "number 3" not in str(skeleton)


def test_only_explicit_self_focus_becomes_agenda_and_preserves_current_read() -> None:
    candidate = _fact("goal", "Damien intends to secure the ledger tonight", fact_type="goal")
    baseline = build_npc_panel_skeleton(_result(), "npc_a")["current_read"]
    input_candidate = copy.deepcopy(candidate)
    result = _result(facts=(candidate,))

    first = build_npc_panel_skeleton(result, "npc_a")
    second = build_npc_panel_skeleton(result, "npc_a")
    support = first["tonight"]["current_desire"]

    assert support["prep_text"] == "May be focused on — Damien intends to secure the ledger tonight."
    assert support["type"] == candidate["type"]
    assert support["section"] == "current_desire"
    assert support["sources"] == candidate["sources"]
    assert first["current_read"] == baseline
    assert second["tonight"]["current_desire"] == support
    assert club_generation._canonical_fact_identity(support) == club_generation._canonical_fact_identity(candidate)
    assert candidate == input_candidate


def test_relationship_pressure_and_general_concern_do_not_become_agenda() -> None:
    relationship = _fact("relationship", "Damien relies on Annabelle at court", fact_type="ally", target="npc_b")
    pressure = _fact("pressure", "Annabelle holds blackmail evidence about Damien", fact_type="blackmail", source="npc_b", target="npc_a")
    concern = _fact("concern", "Damien still owes a favor from the last gathering", fact_type="unresolved_business")

    skeleton = build_npc_panel_skeleton(_result(facts=(pressure, concern), relationships=(relationship,)), "npc_a")

    assert skeleton["tonight"]["current_desire"] is None
    assert skeleton["people_here"]


def test_explicit_self_goal_wins_focus_without_changing_neutral_current_read() -> None:
    goal = _fact("goal", "Damien intends to secure the ledger tonight", fact_type="goal")
    pressure = _fact(
        "pressure",
        "Annabelle holds blackmail evidence about Damien",
        fact_type="blackmail",
        source="npc_b",
        target="npc_a",
    )
    result = _result(facts=(pressure, goal))

    skeleton = build_npc_panel_skeleton(result, "npc_a")
    support = skeleton["tonight"]["current_desire"]

    assert support["sources"][0]["source_id"] == "goal"
    assert support["prep_text"] == "May be focused on — Damien intends to secure the ledger tonight."
    assert skeleton["current_read"] == "Damien has limited attendee-specific prep in the current indexes."


def test_self_owned_goal_with_other_attendee_uses_goal_focus_form() -> None:
    attendee_goal = _fact(
        "goal-with-attendee",
        "Damien wants Annabelle to carry a warning tonight",
        fact_type="goal",
        mentioned=("npc_b",),
    )
    other_goal = _fact(
        "legacy-goal",
        "Damien wants the ledger secured tonight",
        fact_type="goal",
    )
    baseline = build_npc_panel_skeleton(_result(facts=(other_goal,)), "npc_a")
    skeleton = build_npc_panel_skeleton(_result(facts=(attendee_goal, other_goal)), "npc_a")
    panel = npc_panel_from_skeleton(skeleton)
    support = skeleton["tonight"]["current_desire"]

    assert club_generation._canonical_fact_identity(support) == club_generation._canonical_fact_identity(attendee_goal)
    assert support["type"] == "goal"
    assert support["source_npc_id"] == "npc_a"
    assert support["target_npc_id"] is None
    assert support["mentioned_npc_ids"] == ["npc_b"]
    assert support["sources"] == attendee_goal["sources"]
    assert support["prep_text"] == "May be focused on — Damien wants Annabelle to carry a warning tonight."
    assert baseline["current_read"] == "Damien has limited attendee-specific prep in the current indexes."
    assert skeleton["current_read"] == baseline["current_read"]

    provider_panel = copy.deepcopy(panel)
    provider_panel["tonight"]["current_demeanor"] = "Quietly attentive."
    processed = club_generation.postprocess_npc_panel(provider_panel, skeleton)

    assert club_generation._canonical_fact_identity(processed["tonight"]["current_desire"]) == club_generation._canonical_fact_identity(attendee_goal)
    assert processed["tonight"]["current_desire"]["mentioned_npc_ids"] == ["npc_b"]
    assert processed["tonight"]["current_desire"]["prep_text"] == support["prep_text"]
    assert processed["current_read"] == skeleton["current_read"]

    fallback_skeleton = copy.deepcopy(skeleton)
    fallback_skeleton["current_read"] = ""
    assert club_generation._fallback_current_read(fallback_skeleton) == baseline["current_read"]


def test_participant_free_intent_is_focus_only_not_current_read() -> None:
    intent = _fact(
        "self-intent",
        "Damien intends to watch the doors",
        fact_type="intent",
    )
    baseline = build_npc_panel_skeleton(_result(), "npc_a")
    skeleton = build_npc_panel_skeleton(_result(facts=(intent,)), "npc_a")
    panel = npc_panel_from_skeleton(skeleton)
    support = skeleton["tonight"]["current_desire"]

    assert club_generation._canonical_fact_identity(support) == club_generation._canonical_fact_identity(intent)
    assert support["type"] == "intent"
    assert support["source_npc_id"] == "npc_a"
    assert support["target_npc_id"] is None
    assert support["mentioned_npc_ids"] == []
    assert support["sources"] == intent["sources"]
    assert support["prep_text"] == "May be focused on — Damien intends to watch the doors."
    assert skeleton["current_read"] == baseline["current_read"]

    provider_panel = copy.deepcopy(panel)
    provider_panel["tonight"]["current_demeanor"] = "Quietly attentive."
    processed = club_generation.postprocess_npc_panel(provider_panel, skeleton)

    assert club_generation._canonical_fact_identity(processed["tonight"]["current_desire"]) == club_generation._canonical_fact_identity(intent)
    assert processed["tonight"]["current_desire"]["prep_text"] == support["prep_text"]
    assert processed["current_read"] == skeleton["current_read"]

    fallback_skeleton = copy.deepcopy(skeleton)
    fallback_skeleton["current_read"] = ""
    assert club_generation._fallback_current_read(fallback_skeleton) == baseline["current_read"]


def test_explicit_self_focus_requires_clicked_npc_ownership() -> None:
    foreign_intent = _fact(
        "foreign-intent",
        "Annabelle intends to watch the doors",
        fact_type="intent",
        source="npc_b",
    )

    assert not club_generation._is_explicit_self_focus(foreign_intent, "npc_a")


def test_panel_compositor_overwrites_provider_factual_prose() -> None:
    relationship = _fact(
        "relationship",
        "Damien relies on Annabelle at court",
        fact_type="ally",
        target="npc_b",
    )
    goal = _fact("goal", "Damien intends to secure the ledger tonight", fact_type="goal")
    skeleton = build_npc_panel_skeleton(_result(facts=(goal,), relationships=(relationship,)), "npc_a")
    provider_panel = npc_panel_from_skeleton(skeleton)
    provider_panel["people_here"][0]["prep_text"] = "PROVIDER INVENTED RELATIONSHIP PROSE."
    provider_panel["likely_conversation"] = ["PROVIDER INVENTED CONVERSATION PROSE."]
    provider_panel["tonight"]["current_desire"]["prep_text"] = "PROVIDER INVENTED FOCUS PROSE."

    processed = club_generation.postprocess_npc_panel(provider_panel, skeleton)

    assert "PROVIDER" not in str(processed["people_here"])
    assert "PROVIDER" not in str(processed["likely_conversation"])
    assert "PROVIDER" not in processed["tonight"]["current_desire"]["prep_text"]
    assert processed["tonight"]["current_desire"] == skeleton["tonight"]["current_desire"]


def test_focus_stays_internal_to_normal_json_and_readable_debug_uses_stored_text() -> None:
    goal = _fact("goal", "Damien intends to secure the ledger tonight", fact_type="goal")
    skeleton = build_npc_panel_skeleton(_result(facts=(goal,)), "npc_a")
    panel = npc_panel_from_skeleton(skeleton)

    visible = debug_club_generation._visible_panel_payload(panel)
    readable = debug_club_generation._panel_text(panel)

    assert "tonight" not in visible
    assert "current_desire" not in visible
    assert panel["presentation"]["agenda"]["text"] in readable
    assert "Agenda Tonight" in readable


def test_panel_schema_version_changes_without_related_version_bumps() -> None:
    assert club_generation.CLUB_PANEL_SCHEMA_VERSION == "club_panel_v12"
    assert club_generation.CLUB_PANEL_PROMPT_VERSION == "club_panel_prompt_v15"
    assert club_generation.CLUB_PANEL_AI_REQUEST_VERSION == "club_panel_ai_request_v8"
    assert club_generation.CLUB_SKELETON_VERSION == "club_skeleton_v7"


def _presentation_response(skeleton: dict) -> dict:
    conversation = skeleton["conversation"]
    current_read_basis = skeleton.get("current_read_basis")
    demeanor = current_read_basis.get("demeanor") if isinstance(current_read_basis, dict) else None
    has_demeanor_basis = bool(
        isinstance(demeanor, dict)
        and str(demeanor.get("value") or "").strip()
        and any(
            isinstance(source, dict) and str(source.get("source_id") or "").strip()
            for source in demeanor.get("sources") or []
        )
    )
    play_cue = (
        "Keep the delivery clipped and watchful."
        if has_demeanor_basis or skeleton.get("personality")
        else ""
    )

    def entries(items: list[dict], prefix: str) -> list[dict[str, str]]:
        return [
            {"item_id": item["item_id"], "text": f"{prefix} {index + 1}."}
            for index, item in enumerate(items)
        ]

    focus = skeleton["tonight"].get("current_desire")
    hook = skeleton.get("interesting_detail")
    return {
        "npc_id": skeleton["npc_id"],
        "presentation": {
            "play_cue": play_cue,
            "agenda": {"item_id": focus["item_id"], "text": "Push the ledger plan tonight."} if focus else None,
            "people_here": entries(skeleton["people_here"], "Relationship cue"),
            "if_approached": entries(conversation["likely_subjects"], "Reaction cue"),
            "keep_guarded": entries(conversation["sensitive"], "Guarded cue"),
            "hook": {"item_id": hook["item_id"], "text": "Put the group job in motion."} if hook else None,
        },
    }


def test_balanced_panel_routes_support_exclusively_with_tight_caps() -> None:
    relationship_facts = tuple(
        _fact(f"relationship-{index}", f"Damien has tie {index} to Annabelle", fact_type="ally", target="npc_b")
        for index in range(4)
    )
    agenda = _fact("agenda", "Damien intends to secure the ledger tonight", fact_type="goal")
    hook = _fact(
        "hook",
        "Blackmail Gone Wrong: the group is hired to uncover Damien's scheme",
        fact_type="story_hook",
    )
    incoming = _fact(
        "incoming",
        "Annabelle suspects Damien destroyed the missing ledger",
        fact_type="rumor",
        source="npc_b",
        mentioned=("npc_a",),
    )
    secret = _fact("secret", "Keep My Secret: Damien kills anyone who learns the truth", fact_type="goal")
    reactions = tuple(
        _fact(f"reaction-{index}", f"Conversation subject {index}", fact_type="recent_event")
        for index in range(4)
    )
    original_facts = (agenda, hook, incoming, secret, *reactions)
    result = _result(facts=original_facts, relationships=relationship_facts)

    skeleton = build_npc_panel_skeleton(result, "npc_a")
    conversation = skeleton["conversation"]

    assert skeleton["tonight"]["current_desire"]["sources"][0]["source_id"] == "agenda"
    assert len(skeleton["people_here"]) == 3
    assert skeleton["interesting_detail"]["sources"][0]["source_id"] == "hook"
    assert len(conversation["sensitive"]) == 2
    assert conversation["sensitive"][0]["sources"][0]["source_id"] == "incoming"
    assert len(conversation["likely_subjects"]) == 3
    identities = _slot_fact_ids(skeleton)
    identities.append(club_generation._canonical_fact_identity(skeleton["tonight"]["current_desire"]))
    assert len(identities) == len(set(identities))


def test_balanced_panel_selection_is_order_independent_and_non_mutating() -> None:
    facts = (
        _fact("goal", "Damien intends to secure the ledger", fact_type="goal"),
        _fact("hook", "The group can expose Damien's ledger scheme", fact_type="story_hook"),
        _fact("event-a", "Damien attended the last court", fact_type="recent_event"),
        _fact("event-b", "Damien argued about domain rights", fact_type="recent_event"),
    )
    relationships = (
        _fact("rel-a", "Damien relies on Annabelle", fact_type="ally", target="npc_b"),
        _fact("rel-b", "Damien distrusts Annabelle", fact_type="distrust", target="npc_b"),
    )
    original_facts = copy.deepcopy(facts)
    original_relationships = copy.deepcopy(relationships)

    forward = build_npc_panel_skeleton(_result(facts=facts, relationships=relationships), "npc_a")
    reversed_result = build_npc_panel_skeleton(
        _result(facts=tuple(reversed(facts)), relationships=tuple(reversed(relationships))),
        "npc_a",
    )

    assert forward == reversed_result
    assert facts == original_facts
    assert relationships == original_relationships


def test_ai_panel_presentation_is_separate_from_canonical_support() -> None:
    relationship = _fact("relationship", "Damien relies on Annabelle at court", fact_type="ally", target="npc_b")
    goal = _fact("goal", "Damien intends to secure the ledger tonight", fact_type="goal")
    skeleton = build_npc_panel_skeleton(_result(facts=(goal,), relationships=(relationship,)), "npc_a")
    response = _presentation_response(skeleton)

    panel = club_generation.postprocess_npc_presentation(response, skeleton)

    assert panel["presentation"] == response["presentation"]
    assert panel["tonight"]["current_demeanor"] == response["presentation"]["play_cue"]
    assert panel["people_here"][0]["prep_text"] == skeleton["people_here"][0]["prep_text"]
    assert panel["presentation"]["people_here"][0]["text"] == "Relationship cue 1."
    assert "sources" not in panel["presentation"]["people_here"][0]


def test_ai_panel_presentation_rejects_wrong_item_mapping_and_oversized_text() -> None:
    relationship = _fact("relationship", "Damien relies on Annabelle at court", fact_type="ally", target="npc_b")
    skeleton = build_npc_panel_skeleton(_result(relationships=(relationship,)), "npc_a")
    response = _presentation_response(skeleton)
    response["presentation"]["people_here"][0]["item_id"] = "fabricated"

    with pytest.raises(club_generation.ClubGenerationError, match="people_here"):
        club_generation.postprocess_npc_presentation(response, skeleton)

    response = _presentation_response(skeleton)
    response["presentation"]["people_here"][0]["text"] = "x" * 181
    with pytest.raises(club_generation.ClubGenerationError, match="180"):
        club_generation.postprocess_npc_presentation(response, skeleton)


def test_ai_panel_requires_play_cue_when_portrayal_basis_exists() -> None:
    skeleton = build_npc_panel_skeleton(_result(), "npc_a")
    skeleton["personality"] = ["Watchful"]
    response = _presentation_response(skeleton)
    response["presentation"]["play_cue"] = ""

    with pytest.raises(club_generation.ClubGenerationError, match="play_cue must be non-empty"):
        club_generation.postprocess_npc_presentation(response, skeleton)


def test_ai_panel_requires_play_cue_when_sourced_demeanor_exists() -> None:
    skeleton = build_npc_panel_skeleton(_result(), "npc_a")
    skeleton["current_read_basis"] = {
        "demeanor": {
            "value": "Watchful",
            "sources": [{"source_id": "demeanor-source"}],
        }
    }
    response = _presentation_response(skeleton)
    response["presentation"]["play_cue"] = ""

    with pytest.raises(club_generation.ClubGenerationError, match="play_cue must be non-empty"):
        club_generation.postprocess_npc_presentation(response, skeleton)


def test_ai_panel_rejects_ungrounded_play_cue_without_portrayal_basis() -> None:
    skeleton = build_npc_panel_skeleton(_result(), "npc_a")
    assert skeleton["current_read_basis"] is None
    assert skeleton["personality"] == []
    response = _presentation_response(skeleton)
    response["presentation"]["play_cue"] = "They casually twirl a bloodied knife."

    with pytest.raises(club_generation.ClubGenerationError, match="play_cue must be empty"):
        club_generation.postprocess_npc_presentation(response, skeleton)
