from __future__ import annotations

import copy
import json

import pytest

import core.club_generation as club_generation
import tools.debug_club_generation as debug_club_generation


class _SkeletonEchoProvider:
    def __init__(self) -> None:
        self.calls = 0

    def generate_from_messages(self, messages, *, strip_response=True, request_options=None):
        del strip_response, request_options
        self.calls += 1
        prompt = messages[-1]["content"]
        skeleton, _end = json.JSONDecoder().raw_decode(prompt.split("Skeleton JSON:\n", 1)[1])
        return json.dumps(
            club_generation.dashboard_from_skeleton(
                skeleton,
                first_impression="AI supplies only this first impression.",
            )
        )


def _attendee(npc_id: str, name: str, aliases: tuple[str, ...] = ()) -> dict:
    return {
        "npc_id": npc_id,
        "display_name": name,
        "aliases": list(aliases),
    }


def _item(
    section: str,
    item_id: str,
    summary: str,
    *,
    source: str = "alan",
    target: str | None = "tyrus",
    mentioned: tuple[str, ...] = (),
    fact_type: str = "relationship",
) -> dict:
    scope = "relationship" if target else ("mention" if mentioned else "self")
    return {
        "item_id": item_id,
        "section": section,
        "type": fact_type,
        "fact_scope": scope,
        "source_npc_id": source,
        "target_npc_id": target,
        "mentioned_npc_ids": list(mentioned),
        "characters": [source, target] if target else [source],
        "summary": summary,
        "sources": [{"source_id": f"source-{item_id}"}],
        "display_label": fact_type.title(),
        "prep_text": "PROVIDER TEXT MUST NOT SURVIVE",
    }


def _skeleton(*, top=(), pressure=(), rumors=(), attendees=None, room_groups=None) -> dict:
    attendee_rows = attendees or [
        _attendee("alan", "Alan Smythe", ("Alan",)),
        _attendee("tyrus", "Tyrus Cole"),
        _attendee("dread", "Dread", ("The Dread",)),
    ]
    all_items = [*top, *pressure, *rumors]
    return {
        "kind": "dashboard",
        "schema_version": club_generation.CLUB_SKELETON_VERSION,
        "attendee_ids": [row["npc_id"] for row in attendee_rows],
        "attendees": attendee_rows,
        "source_ids": sorted(
            {
                source["source_id"]
                for item in all_items
                if isinstance(item, dict)
                for source in item.get("sources") or []
                if isinstance(source, dict) and isinstance(source.get("source_id"), str)
            }
        ),
        "event": {
            "venue": "The Lantern Room",
            "host": "",
            "event_type": "social gathering",
            "mood": "tense",
            "unusual_circumstance": "",
            "late_arrival_id": "tyrus",
        },
        "room_groups": list(room_groups or []),
        "social_map": list(room_groups or []),
        "top_connections": list(top),
        "possible_drama": list(pressure),
        "rumors": list(rumors),
        "rumor_selection": {"limit": len(rumors), "selected_count": len(rumors), "grounded_count": len(rumors)},
        "interesting_connections": [],
        "unresolved_business": [],
        "opportunities": [],
        "notable_details": [],
        "background_details": [],
    }


def test_canonical_helpers_normalize_without_mutating() -> None:
    item = {
        "source_npc_id": " alan ",
        "target_npc_id": " tyrus ",
        "mentioned_npc_ids": ["dread", "tyrus", "dread"],
    }
    before = copy.deepcopy(item)

    assert club_generation.canonical_target_npc_id(item) == "tyrus"
    assert club_generation.canonical_involved_npc_ids(item) == ("alan", "tyrus", "dread")
    assert item == before
    assert club_generation.canonical_target_npc_id({"target_npc_id": "  "}) is None
    with pytest.raises(ValueError, match="target_npc_id"):
        club_generation.canonical_target_npc_id({"target_npc_id": 42})


def test_exact_factual_grammar_replaces_provider_text_and_is_idempotent() -> None:
    top = _item("top_connections", "connection-1", "Alan knows where Tyrus hid the ledger.")
    pressure = _item("possible_drama", "pressure-1", "Tyrus may expose Alan's broken obligation.")
    rumor = _item(
        "rumors",
        "rumor-1",
        "Dread may have sold the map.",
        source="dread",
        target=None,
        fact_type="rumor",
    )
    skeleton = _skeleton(top=[top], pressure=[pressure], rumors=[rumor])

    first = club_generation._compose_dashboard_presentation(skeleton)
    second = club_generation._compose_dashboard_presentation(skeleton)

    assert first == second
    assert first.hot_connections[0].text == "Connection — Alan knows where Tyrus hid the ledger."
    assert first.possible_pressure[0].text == "Possible pressure — Tyrus may expose Alan's broken obligation."
    assert first.rumors[0].text == "Unverified rumor — Dread may have sold the map."
    for record in (*first.hot_connections, *first.possible_pressure, *first.rumors):
        assert record.support["prep_text"] == record.text
        assert "PROVIDER TEXT" not in record.text


@pytest.mark.parametrize(
    ("mutate", "category"),
    [
        (lambda item: "not-a-dict", "invalid_item_shape"),
        (lambda item: {**item, "item_id": ""}, "missing_item_id"),
        (lambda item: {**item, "item_id": 42}, "missing_item_id"),
        (lambda item: {**item, "section": "rumors"}, "invalid_item_shape"),
        (lambda item: {**item, "type": ""}, "invalid_item_shape"),
        (lambda item: {**item, "fact_scope": "mention"}, "invalid_item_shape"),
        (lambda item: {**item, "characters": ["tyrus", "alan"]}, "invalid_item_shape"),
        (lambda item: {**item, "source_npc_id": "unknown", "characters": ["unknown", "tyrus"]}, "invalid_source_npc_id"),
        (lambda item: {**item, "target_npc_id": ""}, "invalid_target_npc_id"),
        (lambda item: {**item, "target_npc_id": 42}, "invalid_target_npc_id"),
        (lambda item: {**item, "mentioned_npc_ids": "dread"}, "invalid_mentioned_npc_ids"),
        (lambda item: {**item, "mentioned_npc_ids": [42]}, "invalid_mentioned_npc_ids"),
        (lambda item: {**item, "mentioned_npc_ids": ["unknown"]}, "invalid_mentioned_npc_ids"),
        (lambda item: {**item, "summary": 42}, "invalid_summary"),
        (lambda item: {**item, "summary": "   "}, "invalid_summary"),
        (lambda item: {**item, "sources": []}, "invalid_sources"),
    ],
)
def test_malformed_items_are_omitted_with_fixed_first_category(mutate, category: str) -> None:
    item = _item("top_connections", "connection-1", "Alan knows Tyrus.")
    skeleton = _skeleton(top=[mutate(item)])

    presentation = club_generation._compose_dashboard_presentation(skeleton)

    assert presentation.hot_connections == ()
    assert len(presentation.diagnostics) == 1
    assert presentation.diagnostics[0].failure_category == category
    assert presentation.diagnostics[0].disposition == "omitted"


def test_invalid_shape_precedes_missing_item_id_diagnostic() -> None:
    item = _item("top_connections", "connection-1", "Alan knows Tyrus.")
    item["section"] = "rumors"
    item["item_id"] = ""

    presentation = club_generation._compose_dashboard_presentation(_skeleton(top=[item]))

    assert presentation.diagnostics[0].failure_category == "invalid_item_shape"


def test_unknown_source_id_is_rejected() -> None:
    item = _item("top_connections", "connection-1", "Alan knows Tyrus.")
    skeleton = _skeleton(top=[item])
    skeleton["top_connections"][0]["sources"] = [{"source_id": "unknown"}]

    presentation = club_generation._compose_dashboard_presentation(skeleton)

    assert presentation.hot_connections == ()
    assert presentation.diagnostics[0].failure_category == "invalid_sources"


def test_rumor_rejects_non_null_target() -> None:
    rumor = _item(
        "rumors",
        "rumor-1",
        "Alan may know the truth.",
        source="alan",
        target="tyrus",
        fact_type="rumor",
    )

    presentation = club_generation._compose_dashboard_presentation(_skeleton(rumors=[rumor]))

    assert presentation.rumors == ()
    assert presentation.diagnostics[0].failure_category == "invalid_target_npc_id"


def test_composition_does_not_mutate_canonical_items() -> None:
    top = _item("top_connections", "connection-1", "Alan knows Tyrus.")
    skeleton = _skeleton(top=[top])
    before = copy.deepcopy(skeleton)

    club_generation._compose_dashboard_presentation(skeleton)

    assert skeleton == before


def test_duplicate_ids_omit_every_duplicate_without_slice_backfill() -> None:
    duplicate_a = _item("top_connections", "duplicate", "Alan watches Tyrus.")
    duplicate_b = _item("top_connections", " duplicate ", "Tyrus watches Alan.", source="tyrus", target="alan")
    admitted = _item("top_connections", "safe", "Dread watches Tyrus.", source="dread", target="tyrus")
    outside_slice = _item("top_connections", "outside", "Tyrus watches Dread.", source="tyrus", target="dread")

    presentation = club_generation._compose_dashboard_presentation(
        _skeleton(top=[duplicate_a, duplicate_b, admitted, outside_slice])
    )

    assert [record.item_id for record in presentation.hot_connections] == ["safe"]
    assert [diagnostic.failure_category for diagnostic in presentation.diagnostics] == ["duplicate_item_id"]
    assert "outside" not in presentation.room_situation


def test_name_matching_is_longest_first_unicode_aware_and_possessive_safe() -> None:
    attendees = [
        _attendee("ana", "Ana"),
        _attendee("ana_maria", "Ana Maria", ("María-José", "Maria")),
        _attendee("alan", "Alan Smythe", ("Alan", "Al")),
    ]

    resolved = club_generation._resolved_attendee_ids(
        "Ana Maria met María-José; Alan’s note survived, but Anagram and Al did not count.",
        attendees,
    )

    assert resolved == ("ana_maria", "alan")


def test_ambiguous_aliases_are_ignored_but_unique_unsupported_names_omit() -> None:
    attendees = [
        _attendee("alan", "Alan Smythe", ("Dread",)),
        _attendee("tyrus", "Tyrus Cole", ("Dread",)),
        _attendee("outsider", "Ana Maria"),
    ]
    ambiguous = _item("top_connections", "ambiguous", "Dread controls the door.", source="alan", target="tyrus")
    unsupported = _item("top_connections", "unsupported", "Ana Maria controls the door.", source="alan", target="tyrus")

    ambiguous_result = club_generation._compose_dashboard_presentation(_skeleton(top=[ambiguous], attendees=attendees))
    unsupported_result = club_generation._compose_dashboard_presentation(_skeleton(top=[unsupported], attendees=attendees))

    assert [record.item_id for record in ambiguous_result.hot_connections] == ["ambiguous"]
    assert unsupported_result.hot_connections == ()
    assert unsupported_result.diagnostics[0].failure_category == "unsupported_attendee_reference"


def test_guests_and_room_consume_only_admitted_records_and_choose_next_safe_cue() -> None:
    rejected = _item("possible_drama", "rejected", "Dread will expose Alan.", source="alan", target=None)
    safe = _item("possible_drama", "safe", "Alan and Tyrus may clash.")
    hot = _item("top_connections", "hot", "Alan and Tyrus share a debt.")
    skeleton = _skeleton(
        top=[hot],
        pressure=[rejected, safe],
        room_groups=[{"npc_ids": ["alan", "tyrus"], "location": "Unsafe label", "summary": "Unsafe group prose"}],
    )

    presentation = club_generation._compose_dashboard_presentation(skeleton)

    assert presentation.guest_brief[0] == (
        "Alan Smythe: watch Tyrus Cole; Possible pressure — Alan and Tyrus may clash."
    )
    assert presentation.guest_brief[1] == (
        "Tyrus Cole (late arrival): watch Alan Smythe; Possible pressure — Alan and Tyrus may clash."
    )
    assert presentation.guest_brief[2] == "Dread: no grounded attendee tie surfaced."
    assert "Unsafe label" not in presentation.room_situation
    assert "Unsafe group prose" not in presentation.room_situation
    assert presentation.room_situation.endswith("Connection — Alan and Tyrus share a debt.")
    assert "rejected" not in presentation.room_situation


def test_diagnostics_are_stable_sorted_and_set_deduplicated() -> None:
    bad = _item("top_connections", "bad", "Dread is involved.", source="alan", target="tyrus")
    skeleton = _skeleton(top=[bad])

    first = club_generation._compose_dashboard_presentation(skeleton)
    second = club_generation._compose_dashboard_presentation(skeleton)

    assert first.diagnostics == second.diagnostics
    assert len(set(first.diagnostics)) == len(first.diagnostics) == 1


def test_diagnostic_emission_set_deduplicates_and_does_not_change_payload(capsys) -> None:
    diagnostic = club_generation._PresentationDiagnostic(
        "top_connections",
        "item-1",
        "invalid_summary",
        "omitted",
    )

    club_generation._emit_presentation_diagnostics((diagnostic, diagnostic))

    assert capsys.readouterr().out.splitlines() == [
        "club_presentation section=top_connections item_id=item-1 "
        "failure_category=invalid_summary disposition=omitted"
    ]


def test_postprocess_ignores_ai_factual_and_room_prose_but_keeps_first_impression() -> None:
    target = _item(
        "possible_drama",
        "target",
        "Tyrus remains a potential target.",
        source="alan",
        target="tyrus",
        fact_type="danger",
    )
    bond = _item(
        "possible_drama",
        "bond",
        "Tyrus remains trapped by a Coercive Bond.",
        source="dread",
        target="tyrus",
        fact_type="coercive bond",
    )
    skeleton = _skeleton(pressure=[target, bond])
    provider_dashboard = club_generation.dashboard_from_skeleton(skeleton)
    provider_dashboard["first_impression"] = "AI atmosphere remains allowed."
    provider_dashboard["room_situation"] = "Dread is the target and Alan exploits the bond."
    provider_dashboard["possible_pressure"] = [
        "Dread is the target.",
        "Alan exploits Tyrus through the Coercive Bond.",
    ]
    provider_dashboard["possible_drama"][0]["prep_text"] = "Dread is the target."
    provider_dashboard["possible_drama"][1]["prep_text"] = "Alan exploits Tyrus through the Coercive Bond."

    processed = club_generation.postprocess_dashboard(provider_dashboard, skeleton)

    assert processed["first_impression"] == "AI atmosphere remains allowed."
    assert processed["possible_pressure"] == [
        "Possible pressure — Tyrus remains a potential target.",
        "Possible pressure — Tyrus remains trapped by a Coercive Bond.",
    ]
    assert "Dread is the target" not in json.dumps(processed)
    assert "Alan exploits" not in json.dumps(processed)


def test_postprocess_dashboard_is_byte_equivalent_on_second_call() -> None:
    top = _item("top_connections", "connection-1", "Alan knows Tyrus.")
    pressure = _item("possible_drama", "pressure-1", "Tyrus may expose Alan's broken obligation.")
    skeleton = _skeleton(top=[top], pressure=[pressure])
    provider_dashboard = club_generation.dashboard_from_skeleton(skeleton)
    provider_dashboard["hot_connections"] = ["Provider connection prose."]
    provider_dashboard["possible_pressure"] = ["Provider pressure prose."]

    first = club_generation.postprocess_dashboard(provider_dashboard, skeleton)
    second = club_generation.postprocess_dashboard(first, skeleton)

    assert first == second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(
        second,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    for visible_field, support_field in (
        ("hot_connections", "top_connections"),
        ("possible_pressure", "possible_drama"),
        ("rumors_in_circulation", "rumors"),
    ):
        assert len(first[visible_field]) == len(first[support_field])
        assert first[visible_field] == [item["prep_text"] for item in first[support_field]]


def test_dashboard_cache_identity_versions_ai_and_deterministic_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs = {
        "attendee_ids": ["alan", "tyrus"],
        "index_revisions": {"alan": "a1", "tyrus": "t1"},
        "venue": "The Lantern Room",
        "event_type": "social gathering",
        "guest_paths": ["Alan.md", "Tyrus.md"],
        "skeleton": {"kind": "dashboard"},
        "seed": 7,
    }
    deterministic_current = club_generation._dashboard_cache_identity(**kwargs, use_ai=False)
    ai_current = club_generation._dashboard_cache_identity(**kwargs, use_ai=True)

    monkeypatch.setattr(club_generation, "CLUB_DASHBOARD_SCHEMA_VERSION", "club_dashboard_v12")
    deterministic_v12 = club_generation._dashboard_cache_identity(**kwargs, use_ai=False)
    ai_v12 = club_generation._dashboard_cache_identity(**kwargs, use_ai=True)
    assert deterministic_v12 != deterministic_current
    assert ai_v12 != ai_current

    monkeypatch.setattr(club_generation, "CLUB_DASHBOARD_SCHEMA_VERSION", deterministic_current["schema_version"])
    monkeypatch.setattr(club_generation, "CLUB_DASHBOARD_AI_REQUEST_VERSION", "changed-request")
    assert club_generation._dashboard_cache_identity(**kwargs, use_ai=False) == deterministic_current
    assert club_generation._dashboard_cache_identity(**kwargs, use_ai=True) != ai_current


@pytest.mark.parametrize("use_ai", [False, True])
def test_stored_v13_dashboard_is_not_returned_for_current_identity(tmp_path, monkeypatch, use_ai: bool) -> None:
    alan = tmp_path / "Alan.md"
    tyrus = tmp_path / "Tyrus.md"
    alan.write_text(
        "### Relationships\n- [[Tyrus]] (Danger): Tyrus remains a potential target.\n",
        encoding="utf-8",
    )
    tyrus.write_text("Affiliation: Executives\n", encoding="utf-8")
    cache_root = tmp_path / ".club-cache"

    monkeypatch.setattr(club_generation, "CLUB_DASHBOARD_SCHEMA_VERSION", "club_dashboard_v13")
    old_provider = _SkeletonEchoProvider()
    old_result = club_generation.ClubGenerationService(
        {}, cache_root=cache_root, vault_root=tmp_path, provider=old_provider
    )._build_canonical_event_result([str(alan), str(tyrus)], seed=11, use_ai=use_ai)

    monkeypatch.setattr(club_generation, "CLUB_DASHBOARD_SCHEMA_VERSION", "club_dashboard_v15")
    new_provider = _SkeletonEchoProvider()
    new_result = club_generation.ClubGenerationService(
        {}, cache_root=cache_root, vault_root=tmp_path, provider=new_provider
    )._build_canonical_event_result([str(alan), str(tyrus)], seed=11, use_ai=use_ai)

    assert old_result.event.cache_key != new_result.event.cache_key
    assert new_result.event.metadata["from_cache"] is False
    assert new_provider.calls == int(use_ai)


def test_duplicate_visible_text_debug_support_is_strictly_index_bound() -> None:
    grounded = [
        {"item_id": "first", "prep_text": "Duplicate text"},
        {"item_id": "second", "prep_text": "Duplicate text"},
    ]

    assert debug_club_generation._support_for_visible_text("Duplicate text", grounded, 1)["item_id"] == "second"
    assert debug_club_generation._support_for_visible_text("Mismatch", grounded, 1) is None
