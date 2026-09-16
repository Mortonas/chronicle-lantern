from __future__ import annotations

import copy
import random
from pathlib import Path

import pytest

import core.club_generation as club_generation
import core.club_index as club_index
import tools.debug_club_generation as debug_club_generation
from core.club_cache import ClubCacheService
from core.club_generation import ClubBuildResult, build_npc_panel_skeleton, npc_panel_from_skeleton
from core.club_index import ClubIndexStore, build_club_index, club_index_from_dict
from core.club_models import ClubAttribute, ClubEvent, ClubIndex, NpcIdentity, SourceRef
from src.ui.club_tab import ClubTab, build_table_prep_text


def _identity(npc_id: str = "npc_a", name: str = "Damien") -> NpcIdentity:
    return NpcIdentity(
        npc_id=npc_id,
        current_path=f"C:/vault/{name}.md",
        display_name=name,
        aliases=(),
        content_fingerprint=f"fingerprint-{npc_id}",
    )


def _source(
    source_id: str,
    *,
    character_id: str = "npc_a",
    path: str = "C:/vault/Damien.md",
    section: str = "Demeanor",
    excerpt: str = "Calm",
) -> SourceRef:
    return SourceRef(
        character_id=character_id,
        path=path,
        section=section,
        source_id=source_id,
        excerpt=excerpt,
    )


def _result(index: ClubIndex, *, facts: tuple[dict, ...] = (), relationships: tuple[dict, ...] = ()) -> ClubBuildResult:
    identity = _identity(index.npc_id, index.name)
    return ClubBuildResult(
        event=ClubEvent(
            event_id="event",
            attendee_ids=(index.npc_id,),
            late_arrival_id=index.npc_id,
            dashboard={},
            cache_key="cache",
            seed=1,
        ),
        identities=(identity,),
        indexes_by_id={index.npc_id: index},
        attendee_relationships=relationships,
        attendee_facts=facts,
        source_map=club_generation.build_source_map([index], identities=[identity]),
        debug_context={},
    )


def _relationship(
    source_id: str,
    fact_type: str,
    summary: str,
    *,
    source_npc_id: str = "a",
    target_npc_id: str = "b",
    path: str = "C:/vault/A.md",
    section: str = "Relationships",
    display_label: str = "",
) -> dict:
    return {
        "type": fact_type,
        "fact_scope": "relationship",
        "source_npc_id": source_npc_id,
        "target_npc_id": target_npc_id,
        "mentioned_npc_ids": [],
        "link_targets": [],
        "characters": [source_npc_id, target_npc_id],
        "display_label": display_label,
        "summary": summary,
        "sources": [
            {
                "character_id": source_npc_id,
                "path": path,
                "section": section,
                "source_id": source_id,
                "excerpt": summary,
            }
        ],
    }


def test_club_attribute_is_immutable_deduplicated_and_round_trips_in_order() -> None:
    first = _source("source-a", excerpt="Calm")
    second = _source("source-b", section="Personality", excerpt="CALM")
    attribute = ClubAttribute(value="Calm", sources=(first, first, second))

    assert attribute.sources == (first, second)
    with pytest.raises(AttributeError):
        attribute.value = "Changed"  # type: ignore[misc]
    with pytest.raises(ValueError):
        ClubAttribute(value="Calm", sources=(_source("   "),))

    identity = _identity()
    index = ClubIndex(
        npc_id=identity.npc_id,
        name=identity.display_name,
        path=identity.current_path,
        sheet_revision_hash="revision",
        schema_version=club_index.CLUB_INDEX_SCHEMA_VERSION,
        prompt_version=club_index.CLUB_INDEX_PROMPT_VERSION,
        demeanor=attribute,
    )
    serialized = index.to_dict()
    restored = club_index_from_dict(serialized)

    assert serialized["appearance"] is None
    assert serialized["mask_identity"] is None
    assert serialized["origin"] is None
    assert serialized["pronouns"] is None
    assert restored.demeanor == attribute
    assert restored.demeanor is not attribute
    assert restored.demeanor.sources == (first, second)


@pytest.mark.parametrize(
    ("heading", "field", "raw_value", "expected"),
    [
        ("Demeanor", "demeanor", "Calm but watchful", "Calm but watchful"),
        ("Demeanour", "demeanor", "Formal: with a brittle smile", "Formal: with a brittle smile"),
        ("Character Demeanor", "demeanor", "Predatory", "Predatory"),
        ("Character Demeanour", "demeanor", "Courtly", "Courtly"),
        ("Appearance", "appearance", "A silver evening coat", "A silver evening coat"),
        ("Mask", "mask_identity", "Morgan Vale", "Morgan Vale"),
        ("Origin", "origin", "Milwaukee", "Milwaukee"),
        ("Pronouns", "pronouns", "They / Them", "they/them"),
    ],
)
def test_each_recognized_dedicated_heading_extracts_only_its_typed_attribute(
    tmp_path: Path,
    heading: str,
    field: str,
    raw_value: str,
    expected: str,
) -> None:
    sheet = tmp_path / f"{field}-{heading}.md"
    sheet.write_text(f"### {heading}\n{raw_value}\n", encoding="utf-8")

    index = build_club_index(_identity(), sheet, sheet_revision_hash="revision")

    attribute = getattr(index, field)
    assert attribute is not None
    assert attribute.value == expected


def test_typed_extraction_routes_mask_and_mien_fields_without_personality_leak(tmp_path: Path) -> None:
    sheet = tmp_path / "Typed.md"
    sheet.write_text(
        "### Mask and Mien\n"
        "- **Mask:** Morgan Vale, a night-shift archivist.\n"
        "- **Appearance:** Tall, silver-haired, and sharply dressed.\n"
        "- **Mien:** A predatory silhouette behind the smile.\n"
        "- **Demeanor:** Calm and deliberate.\n"
        "- **Pronouns:** They / Them\n"
        "### Background Details\n"
        "- **Origin:** Milwaukee.\n"
        "### Personality\n"
        "Reserved; patient\n",
        encoding="utf-8",
    )

    index = build_club_index(_identity(), sheet, sheet_revision_hash="revision")

    assert index.mask_identity and index.mask_identity.value == "Morgan Vale, a night-shift archivist."
    assert index.appearance and index.appearance.value == "Tall, silver-haired, and sharply dressed."
    assert index.demeanor and index.demeanor.value == "Calm and deliberate."
    assert index.origin and index.origin.value == "Milwaukee."
    assert index.pronouns and index.pronouns.value == "they/them"
    assert index.personality_tags == ["reserved", "patient"]
    assert "predatory silhouette" not in str(index.to_dict()).lower()
    for attribute in (index.mask_identity, index.appearance, index.demeanor, index.origin, index.pronouns):
        assert attribute is not None
        assert attribute.sources
        assert all(source.source_id for source in attribute.sources)
    debug = _result(index).to_debug_dict()
    demeanor_source_id = index.demeanor.sources[0].source_id
    assert debug["source_map"][demeanor_source_id]["attribute_type"] == "demeanor"
    assert debug["source_map"][demeanor_source_id]["attribute_value"] == "Calm and deliberate."
    assert "predatory silhouette" not in str(debug).lower()


def test_unlabeled_and_explicit_mien_content_is_not_typed_or_personality_data(tmp_path: Path) -> None:
    sheet = tmp_path / "Mien Only.md"
    sheet.write_text(
        "### Mask and Mien\n"
        "A predatory silhouette behind the smile.\n"
        "- **Mien:** Monstrous in reflected glass.\n",
        encoding="utf-8",
    )

    index = build_club_index(_identity(), sheet, sheet_revision_hash="revision")

    assert index.mask_identity is None
    assert index.appearance is None
    assert index.demeanor is None
    assert index.personality_tags == []
    assert "predatory" not in str(index.to_dict()).lower()


def test_equivalent_typed_values_merge_in_document_order_and_conflicts_fail_closed(tmp_path: Path) -> None:
    equivalent = tmp_path / "Equivalent.md"
    equivalent.write_text(
        "### Demeanor\n"
        " Calm   and   deliberate \n"
        "### Notes\n"
        "- **Demeanour:** CALM AND DELIBERATE\n",
        encoding="utf-8",
    )
    conflict = tmp_path / "Conflict.md"
    conflict.write_text(
        "- **Appearance:** A red coat.\n"
        "- **Appearance:** A blue coat.\n",
        encoding="utf-8",
    )

    merged = build_club_index(_identity(), equivalent, sheet_revision_hash="revision")
    rejected = build_club_index(_identity(), conflict, sheet_revision_hash="revision")

    assert merged.demeanor and merged.demeanor.value == "Calm and deliberate"
    assert len(merged.demeanor.sources) == 2
    assert [source.excerpt for source in merged.demeanor.sources] == [
        "Calm and deliberate",
        "Demeanour: CALM AND DELIBERATE",
    ]
    assert rejected.appearance is None


@pytest.mark.parametrize(
    ("raw", "canonical", "subject"),
    [
        (" He / Him ", "he/him", "He"),
        ("SHE/HER", "she/her", "She"),
        (" they  /  them ", "they/them", "They"),
        ("IT / ITS", "it/its", "It"),
    ],
)
def test_explicit_pronoun_sets_drive_only_the_current_read_subject(
    tmp_path: Path,
    raw: str,
    canonical: str,
    subject: str,
) -> None:
    sheet = tmp_path / "Pronouns.md"
    sheet.write_text(
        "### Demeanor\n"
        "Polite, well-mannered, but unsettling\n"
        "### Details\n"
        f"- **Pronouns:** {raw}\n",
        encoding="utf-8",
    )
    index = build_club_index(_identity(), sheet, sheet_revision_hash="revision")

    skeleton = build_npc_panel_skeleton(_result(index), index.npc_id)
    panel = npc_panel_from_skeleton(skeleton)

    assert index.pronouns and index.pronouns.value == canonical
    assert skeleton["current_read"] == f"{subject}: Polite, well-mannered, but unsettling."
    assert panel["current_read_basis"] == skeleton["current_read_basis"]
    assert panel["current_read_basis"] is not skeleton["current_read_basis"]
    assert "current_read_basis" not in debug_club_generation._visible_panel_payload(panel)


@pytest.mark.parametrize("raw", ["they", "xe/xem", "they/them/theirs", "pronouns: they/them", "they-them"])
def test_invalid_or_unsupported_pronouns_use_the_display_name(tmp_path: Path, raw: str) -> None:
    sheet = tmp_path / "Invalid Pronouns.md"
    sheet.write_text(
        "### Demeanor\n"
        "Watchful\n"
        f"- **Pronouns:** {raw}\n",
        encoding="utf-8",
    )
    index = build_club_index(_identity(), sheet, sheet_revision_hash="revision")

    skeleton = build_npc_panel_skeleton(_result(index), index.npc_id)

    assert index.pronouns is None
    assert skeleton["current_read"] == "Damien: Watchful."
    assert "pronouns" not in skeleton["current_read_basis"]


def test_current_read_uses_only_demeanor_and_keeps_focus_separate(tmp_path: Path) -> None:
    sheet = tmp_path / "Damien.md"
    sheet.write_text(
        "Affiliation: Dockworkers\n"
        "Faction: Council\n"
        "### Mask and Mien\n"
        "- **Mask:** A cheerful promoter.\n"
        "- **Appearance:** Immaculate evening wear.\n"
        "### Background Details\n"
        "- **Origin:** Milwaukee.\n"
        "### Personality\n"
        "Aggressive; suspicious\n"
        "### Demeanor\n"
        "Quietly attentive\n",
        encoding="utf-8",
    )
    index = build_club_index(_identity(), sheet, sheet_revision_hash="revision")
    focus = {
        "type": "goal",
        "fact_scope": "self",
        "source_npc_id": "npc_a",
        "target_npc_id": None,
        "mentioned_npc_ids": [],
        "link_targets": [],
        "characters": ["npc_a"],
        "summary": "Damien intends to secure the ledger tonight",
        "sources": [
            {
                "character_id": "npc_a",
                "path": str(sheet),
                "section": "Goals",
                "source_id": "focus-source",
                "excerpt": "Damien intends to secure the ledger tonight",
            }
        ],
    }

    skeleton = build_npc_panel_skeleton(_result(index, facts=(focus,)), index.npc_id)
    panel = npc_panel_from_skeleton(skeleton)

    assert skeleton["current_read"] == "Damien: Quietly attentive."
    assert "ledger" not in skeleton["current_read"]
    assert skeleton["tonight"]["current_desire"]["summary"] == focus["summary"]
    assert panel["tonight"]["current_desire"] == skeleton["tonight"]["current_desire"]
    assert "focus-source" not in str(panel["current_read_basis"])
    assert "focus-source" in str(panel["tonight"]["current_desire"])
    panel["current_read_basis"]["demeanor"]["value"] = "Changed"
    assert skeleton["current_read_basis"]["demeanor"]["value"] == "Quietly attentive"


def test_mask_mien_appearance_origin_identity_and_focus_cannot_replace_missing_demeanor(tmp_path: Path) -> None:
    sheet = tmp_path / "No Demeanor.md"
    sheet.write_text(
        "Affiliation: Dockworkers\n"
        "Faction: Council\n"
        "Status: Feared\n"
        "### Mask and Mien\n"
        "- **Mask:** A cheerful promoter.\n"
        "- **Appearance:** Immaculate evening wear.\n"
        "- **Mien:** Predatory and cold.\n"
        "### Background Details\n"
        "- **Origin:** Milwaukee.\n"
        "### Personality\n"
        "Aggressive; suspicious\n",
        encoding="utf-8",
    )
    index = build_club_index(_identity(), sheet, sheet_revision_hash="revision")

    skeleton = build_npc_panel_skeleton(_result(index), index.npc_id)

    assert skeleton["current_read"] == "Damien has limited attendee-specific prep in the current indexes."
    assert skeleton["current_read_basis"] is None
    assert "predatory" not in str(index.to_dict()).lower()


def test_old_and_malformed_typed_index_cache_entries_are_rebuilt(tmp_path: Path) -> None:
    sheet = tmp_path / "Cached.md"
    sheet.write_text("### Demeanor\nCalm\n", encoding="utf-8")
    identity = _identity()
    cache = ClubCacheService(tmp_path / ".club-cache")
    store = ClubIndexStore(cache)
    built = build_club_index(identity, sheet)

    for schema_version, malformed in (
        ("club_index_v5", {"value": "Wrong", "sources": []}),
        (club_index.CLUB_INDEX_SCHEMA_VERSION, {"value": "", "sources": []}),
    ):
        cached = built.to_dict()
        cached["schema_version"] = schema_version
        cached["demeanor"] = malformed
        cache.write_json(
            "indexes",
            f"{identity.npc_id}.json",
            data={"cache_key": "stale", "index": cached},
        )

        restored = store.get_or_build(identity, sheet)

        assert restored.schema_version == "club_index_v7"
        assert restored.demeanor and restored.demeanor.value == "Calm"

    missing_null_contract = built.to_dict()
    del missing_null_contract["appearance"]
    cache.write_json(
        "indexes",
        f"{identity.npc_id}.json",
        data={"cache_key": "missing-null", "index": missing_null_contract},
    )

    restored = store.get_or_build(identity, sheet)

    assert restored.appearance is None
    assert "appearance" in restored.to_dict()
    assert store.get_or_build(identity, sheet).to_dict() == restored.to_dict()


def test_old_panel_cache_version_is_invalidated_and_current_read_basis_is_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attribute = ClubAttribute(value="Calm", sources=(_source("demeanor-source"),))
    identity = _identity()
    index = ClubIndex(
        npc_id=identity.npc_id,
        name=identity.display_name,
        path=identity.current_path,
        sheet_revision_hash="revision",
        schema_version=club_index.CLUB_INDEX_SCHEMA_VERSION,
        prompt_version=club_index.CLUB_INDEX_PROMPT_VERSION,
        demeanor=attribute,
    )
    result = _result(index)
    service = club_generation.ClubGenerationService({}, cache_root=tmp_path / ".club-cache", vault_root=tmp_path)

    monkeypatch.setattr(club_generation, "CLUB_PANEL_SCHEMA_VERSION", "club_panel_v8")
    old_panel = service.build_npc_panel_from_result(result, index.npc_id, use_ai=False)
    monkeypatch.setattr(club_generation, "CLUB_PANEL_SCHEMA_VERSION", "club_panel_v12")
    new_panel = service.build_npc_panel_from_result(result, index.npc_id, use_ai=False)
    cached_panel = service.build_npc_panel_from_result(result, index.npc_id, use_ai=False)

    assert len(list((tmp_path / ".club-cache" / "npc_panels").glob("*.json"))) == 2
    assert old_panel["metadata"]["from_cache"] is False
    assert new_panel["metadata"]["from_cache"] is False
    assert cached_panel["metadata"]["from_cache"] is True
    assert cached_panel["current_read_basis"] == new_panel["current_read_basis"]


def test_room_group_label_uses_strongest_supported_type_not_faction_affiliation_or_display_label() -> None:
    identities = {
        "a": {"display_name": "Annabelle", "faction": "Council", "affiliation": "Dockworkers"},
        "b": {"display_name": "Damien", "faction": "Council", "affiliation": "Dockworkers"},
    }
    relationships = [
        _relationship("ally", "ally", "They share a useful contact.", display_label="Council knot"),
        _relationship(
            "blackmail",
            "blackmail",
            "Annabelle holds blackmail over Damien.",
            display_label="Council pressure",
        ),
    ]

    room_map = club_generation.room_facing_social_map(["a", "b"], relationships, identity_by_id=identities)

    assert room_map[0]["location"] == "Blackmail pressure point"
    assert "Council" not in room_map[0]["location"]
    assert "Dockworkers" not in room_map[0]["location"]


def test_one_supported_attendee_relationship_edge_is_sufficient_to_label_a_component() -> None:
    identities = {"a": {"display_name": "A"}, "b": {"display_name": "B"}}
    relationships = [_relationship("enemy", "enemy", "A names B as an enemy.")]

    room_map = club_generation.room_facing_social_map(["a", "b"], relationships, identity_by_id=identities)

    assert room_map[0]["location"] == "Enemy pressure point"


def test_mention_only_fact_cannot_label_a_connected_component() -> None:
    identities = {"a": {"display_name": "A"}, "b": {"display_name": "B"}}
    mention = _relationship("mention", "blackmail", "A mentions blackmail involving B.")
    mention["fact_scope"] = "mention"

    room_map = club_generation.room_facing_social_map(["a", "b"], [mention], identity_by_id=identities)

    assert room_map[0]["location"] == "Connected cluster"


@pytest.mark.parametrize(
    "fact_type",
    ["", "relationship", "connection", "connected", "contact", "associate", "association", "other", "unknown"],
)
def test_generic_room_relationship_types_use_connected_cluster(fact_type: str) -> None:
    identities = {"a": {"display_name": "A"}, "b": {"display_name": "B"}}
    relationships = [_relationship("generic", fact_type, "A and B know one another.")]

    room_map = club_generation.room_facing_social_map(["a", "b"], relationships, identity_by_id=identities)

    assert room_map[0]["location"] == "Connected cluster"


def test_non_attendee_relationship_cannot_label_an_attendee_component() -> None:
    identities = {"a": {"display_name": "A"}, "b": {"display_name": "B"}}
    relationships = [
        _relationship("generic", "relationship", "A and B are connected."),
        _relationship("outside", "blackmail", "A blackmails X.", target_npc_id="x"),
    ]

    room_map = club_generation.room_facing_social_map(["a", "b"], relationships, identity_by_id=identities)

    assert room_map[0]["location"] == "Connected cluster"


def test_equal_ranked_room_labels_are_stable_across_input_order_and_do_not_mutate() -> None:
    identities = {
        "a": {"display_name": "A"},
        "b": {"display_name": "B"},
        "c": {"display_name": "C"},
    }
    relationships = [
        _relationship("source-a", "enemy", "A treats B as an enemy.", target_npc_id="b"),
        _relationship("source-b", "rival", "A treats C as a rival.", target_npc_id="c"),
    ]
    baseline = copy.deepcopy(relationships)
    shuffled = copy.deepcopy(relationships)
    random.Random(17).shuffle(shuffled)

    original = club_generation.room_facing_social_map(["a", "b", "c"], relationships, identity_by_id=identities)
    reversed_result = club_generation.room_facing_social_map(
        ["a", "b", "c"], list(reversed(relationships)), identity_by_id=identities
    )
    shuffled_result = club_generation.room_facing_social_map(["a", "b", "c"], shuffled, identity_by_id=identities)

    assert original == reversed_result == shuffled_result
    assert original[0]["location"] == "Enemy pressure point"
    assert relationships == baseline


def test_first_impression_is_a_view_layer_fold_and_visible_json_stays_raw(qapp) -> None:
    dashboard = {
        "event": {"venue": "The Lantern Room", "event_type": "social gathering", "mood": "tense"},
        "first_impression": "The room hums with restrained violence.",
        "room_situation": "Two grounded conversation groups occupy the room.",
        "hot_connections": [],
        "possible_pressure": [],
        "rumors_in_circulation": [],
        "guest_brief": [],
    }
    event = ClubEvent("event", (), "", dashboard, "cache", 1)
    tab = ClubTab(None)  # type: ignore[arg-type]

    html = tab._dashboard_html(dashboard, "")
    exported = build_table_prep_text(event, [], {})
    readable = debug_club_generation._dashboard_text(dashboard, [], "")
    visible_json = debug_club_generation._visible_dashboard_payload(dashboard)

    for rendered in (html, exported, readable):
        assert rendered.count("Room Situation") == 1
        assert rendered.index("First impression (AI presentation):") < rendered.index("Grounded situation:")
        assert "The room hums with restrained violence." in rendered
        assert "Two grounded conversation groups occupy the room." in rendered
    assert html.count("<h2>") == 5
    assert visible_json["room_situation"] == "Two grounded conversation groups occupy the room."
    assert "first_impression" not in visible_json


def test_phase3_versions_are_narrowly_bumped() -> None:
    assert club_index.CLUB_INDEX_SCHEMA_VERSION == "club_index_v7"
    assert club_index.CLUB_INDEX_PROMPT_VERSION == "deterministic_index_v5"
    assert club_generation.CLUB_PANEL_SCHEMA_VERSION == "club_panel_v12"
    assert club_generation.CLUB_DASHBOARD_SCHEMA_VERSION == "club_dashboard_v20"
    assert club_generation.CLUB_PANEL_PROMPT_VERSION == "club_panel_prompt_v15"
    assert club_generation.CLUB_DASHBOARD_PROMPT_VERSION == "club_dashboard_prompt_v18"
    assert club_generation.CLUB_PANEL_AI_REQUEST_VERSION == "club_panel_ai_request_v8"
    assert club_generation.CLUB_DASHBOARD_AI_REQUEST_VERSION == "club_dashboard_ai_request_v9"
    assert club_generation.CLUB_SKELETON_VERSION == "club_skeleton_v7"
