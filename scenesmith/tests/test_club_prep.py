from __future__ import annotations

import json
import random
from copy import deepcopy

import pytest

from core.club_cache import ClubCacheService
from core.club_prep import (
    PrepEngine, PrepError, RequestBudget, capture_document, fallback_scene,
    retrieve_passages, scene_display_sections, validate_relevance,
    validate_scene_section, visible_scene,
)


def documents():
    return (
        capture_document("a", "Ada", "# Ada\n\nPublicly runs a shelter.\n\n### Backstory\nAs a child Ada rescued injured foxes.\n", "a.md"),
        capture_document("b", "Bea", "# Bea\n\nPublicly offers veterinary care.\n\nSecretly controls the mayor.\n", "b.md"),
        capture_document("c", "Cy", "# Cy\n\nPrefers quiet evenings alone.\n", "c.md"),
    )


def test_passages_cover_whole_sheet_and_revision_changes_identity():
    text = "# History\r\n\r\n" + "A long history. " * 3000 + "\nFinal buried motive."
    doc = capture_document("a", "Ada", text, "a.md")
    assert "".join(p.text for p in doc.passages) == text
    assert len({p.passage_id for p in doc.passages}) == len(doc.passages)
    assert doc.passages[-1].end == len(text)
    changed = capture_document("a", "Ada", text + "!", "a.md")
    assert changed.revision != doc.revision
    assert not set(p.passage_id for p in doc.passages) & set(p.passage_id for p in changed.passages)


def test_retrieval_finds_unlinked_backstory_omitted_from_card():
    docs = documents()
    before = repr(docs)
    results = retrieve_passages(docs, {}, ["rescued foxes"], [])
    assert "injured foxes" in results[0].text
    assert results[0].npc_id == "a"
    assert retrieve_passages(tuple(reversed(docs)), {}, ["rescued foxes"], []) == results
    assert repr(docs) == before


def test_explicit_evidence_first_and_unknown_references_rejected():
    docs = documents()
    chosen = docs[2].passages[-1].passage_id
    assert retrieve_passages(docs, {}, ["veterinary"], [chosen])[0].passage_id == chosen
    with pytest.raises(PrepError):
        retrieve_passages(docs, {}, [], ["fabricated"])


def test_fallback_is_numbered_complete_and_late_arrival_is_last():
    docs = documents()
    scene = fallback_scene(docs, "b", {"a": "Sheriff"})
    assert [row["number"] for row in scene["encounters"]] == [1, 2, 3]
    assert [row["members"] for row in scene["encounters"]] == [["a"], ["c"], ["b"]]
    assert scene["encounters"][-1]["availability"] == "expected"
    assert scene["power_assessed"] is False
    assert "unavailable" in scene["encounters"][0]["basis"]


def test_power_fallback_is_bounded_alphabetical_stable_and_count_accurate():
    docs = (
        capture_document("z", "Zed", "Status: Keeper"),
        capture_document("ada-2", "Ada", "Status: Gossip"),
        capture_document("bea", "Bea", "Status: Delegate"),
        capture_document("ada-1", "Ada", "Status: Sheriff"),
        capture_document("cy", "Cy", "Status: Seneschal"),
        capture_document("dee", "Dee", "Status: Hound"),
        capture_document("eve", "Eve", "Status: Herald"),
    )
    positions = {doc.npc_id: f"Position for {doc.name} ({doc.npc_id})" for doc in docs}
    docs_before = repr(docs)
    positions_before = deepcopy(positions)

    expected_ids = ["ada-1", "ada-2", "bea", "cy", "dee"]
    orders = [docs, tuple(reversed(docs))]
    shuffled = list(docs)
    random.Random(9173).shuffle(shuffled)
    orders.append(tuple(shuffled))

    for ordered_docs in orders:
        scene = fallback_scene(ordered_docs, "z", positions)
        assert [row["npc_id"] for row in scene["power_players"]] == expected_ids
        assert visible_scene(scene)["power_players"] == [
            {key: row[key] for key in ("npc_id", "text", "hidden")}
            for row in scene["power_players"]
        ]
        power_rows = dict(scene_display_sections(scene, {doc.npc_id: doc.name for doc in docs}))["Who Matters Here"]
        assert power_rows[0] == (
            "Power assessment unavailable; showing five known-position guests. "
            "Full roster remains available below."
        )
        assert len(power_rows[1:]) == 5
        assert all("most powerful" not in row.casefold() for row in power_rows)

    count_words = {1: "one", 2: "two", 3: "three", 4: "four"}
    for count, count_word in count_words.items():
        supported_ids = expected_ids[:count]
        sparse = fallback_scene(docs, "z", {npc_id: positions[npc_id] for npc_id in supported_ids})
        sparse_rows = dict(scene_display_sections(sparse, {doc.npc_id: doc.name for doc in docs}))["Who Matters Here"]
        assert [row["npc_id"] for row in sparse["power_players"]] == supported_ids
        guest_word = "guest" if count == 1 else "guests"
        assert sparse_rows[0] == (
            f"Power assessment unavailable; showing {count_word} known-position {guest_word}. "
            "Full roster remains available below."
        )

    empty = fallback_scene(docs, "z", {})
    assert empty["power_players"] == []
    assert dict(scene_display_sections(empty, {}))["Who Matters Here"] == [
        "Power assessment unavailable; no known-position guests surfaced. "
        "Full roster remains available below."
    ]
    assert repr(docs) == docs_before
    assert positions == positions_before


def test_duplicate_group_members_and_absent_opening_actor_rejected():
    docs = documents()
    evidence = {p.passage_id: p for d in docs for p in d.passages}
    with pytest.raises(PrepError):
        validate_scene_section("opening", {"text": "Bea greets everyone.", "characters": ["b"], "evidence": []}, docs, "b", evidence, {})
    rows = [{"members": ["a", "a"], "cue": "Talking.", "reason": "Friends.", "member_reasons": [], "evidence": []}]
    with pytest.raises(PrepError):
        validate_scene_section("encounters", rows, docs, "c", evidence, {})


def test_private_target_evidence_cannot_become_clicked_npc_knowledge():
    docs = documents()
    own = docs[0].passages[-1]
    private = docs[1].passages[-1]
    evidence = {p.passage_id: p for d in docs for p in d.passages}
    annotations = {private.passage_id: {"visibility": "private", "known_by": ["b"]}}
    row = {"npc_id": "b", "text": "Ask Bea to influence the mayor.", "classification": "inferred", "evidence": [own.passage_id, private.passage_id]}
    with pytest.raises(PrepError):
        validate_relevance([row], "a", docs, evidence, annotations, [])


class ReadingProvider:
    def __init__(self):
        self.calls = []

    def generate_from_messages(self, messages, **kwargs):
        payload = json.loads(messages[-1]["content"].split("INPUT_JSON:\n", 1)[1])
        self.calls.append(payload)
        return json.dumps({"card": "A useful attendee.", "annotations": [
            {"passage_id": p["passage_id"], "topics": ["shelter"], "categories": ["backstory"], "visibility": "unknown", "known_by": []}
            for p in payload["passages"]
        ]})


def test_successful_readings_cached_and_malformed_entries_rebuild(tmp_path):
    provider = ReadingProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {"name": "test"})
    docs = documents()
    readings = engine.read_documents(docs, RequestBudget(64))
    assert all(r["complete"] for r in readings.values())
    assert len(provider.calls) == 3
    assert engine.read_documents(docs, RequestBudget(64)) == readings
    assert len(provider.calls) == 3
    cache_file = next((tmp_path / "prep_readings").glob("*.json"))
    cache_file.write_text('{"invalid": true}', encoding="utf-8")
    engine.read_documents(docs, RequestBudget(64))
    assert len(provider.calls) == 4


def test_reading_allowance_reserves_scene_and_rumor_guidance_requests(tmp_path):
    provider = ReadingProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    readings = engine.read_documents(documents(), RequestBudget(10), downstream_reserve=8)
    assert len(provider.calls) == 1
    assert sum(r["complete"] for r in readings.values()) == 1
    assert len(readings) == 3

    blocked_provider = ReadingProvider()
    blocked = PrepEngine(ClubCacheService(tmp_path / "blocked"), blocked_provider, {})
    blocked.read_documents(documents(), RequestBudget(9), downstream_reserve=8)
    assert blocked_provider.calls == []

    scene_only_provider = ReadingProvider()
    scene_only = PrepEngine(ClubCacheService(tmp_path / "scene-only"), scene_only_provider, {})
    scene_only.read_documents(documents(), RequestBudget(6))
    assert len(scene_only_provider.calls) == 1


def test_baseline_npc_uses_new_relevance_shape_without_provider(tmp_path):
    from core.club_generation import ClubGenerationService
    path = tmp_path / "Ada.md"
    path.write_text("# Ada\n\nPublicly supports the shelter.", encoding="utf-8")
    provider = ReadingProvider()
    service = ClubGenerationService({}, cache_root=tmp_path / "cache", provider=provider)
    result = service.build_event_result([str(path)], seed=1, use_ai=False)
    panel = service.build_npc_panel_from_result(result, result.identities[0].npc_id, use_ai=False)
    assert panel["who_matters"] == []
    assert panel["metadata"]["who_matters_stage"]["status"] == "unavailable"
    assert len(result.event.dashboard["scene_prep"]["encounters"]) == 1
    assert not provider.calls


class SceneProvider(ReadingProvider):
    def generate_from_messages(self, messages, **kwargs):
        if "INPUT_JSON:\n" not in messages[-1]["content"]:
            from test_club_generation import _panel_presentation_response, _skeleton_from_prompt
            skeleton = _skeleton_from_prompt(messages[-1]["content"])
            self.calls.append({"canonical_panel": skeleton["npc_id"]})
            return json.dumps(_panel_presentation_response(skeleton))
        payload = json.loads(messages[-1]["content"].split("INPUT_JSON:\n", 1)[1])
        if "passages" in payload:
            value = json.loads(super().generate_from_messages(messages, **kwargs))
            for a in value["annotations"]:
                a["visibility"] = "public"
                a["categories"] = ["power", "social", "backstory"]
            return json.dumps(value)
        self.calls.append(payload)
        if "rumors" in payload:
            if "evidence" not in payload:
                present = [
                    npc_id
                    for row in payload.get("arrangement", [])
                    if row.get("availability") == "present"
                    for npc_id in row.get("members", [])
                ]
                return json.dumps({"candidates": present[:1], "queries": ["public interests history"], "references": []})
            return json.dumps({"rumor_guidance": []})
        if "reading_coverage" in payload:
            if "evidence" not in payload:
                return json.dumps({"queries": ["public interests goals history"], "references": []})
            ref = next(row["passage_id"] for row in payload["evidence"] if row["text"].strip() and not row["text"].strip().startswith("#"))
            return json.dumps({"conversation_openings": [{
                "lane": "easy",
                "topic": "Supporting the local shelter",
                "response": f"{payload['name']} might offer a brief practical answer.",
                "possible_gain": "The players might learn what kind of help would be welcome.",
                "possible_risk": "Pushing for a commitment could close the subject.",
                "evidence": [ref],
            }]})
        if "evidence" not in payload:
            return json.dumps({"candidates": [r["npc_id"] for r in payload["roster"]], "queries": ["shelter veterinary foxes status"], "references": []})
        evidence = payload["evidence"]
        own = {r["npc_id"]: r["passage_id"] for r in evidence if r["text"].strip() and not r["text"].strip().startswith("#") and "rumor" not in r["annotation"]["categories"]}
        if "sections" not in payload:
            clicked = payload["npc_id"]
            target = next((n for n in own if n != clicked), None)
            return json.dumps({"who_matters": [] if target is None else [{"npc_id": target, "text": "Possible interest: ask about their work.", "classification": "inferred", "evidence": [own[clicked], own[target]]}]})
        late = payload["late_arrival_id"]
        values = {
            "opening": {"text": "Guests find their bearings. Invite the players to approach someone.", "characters": [], "evidence": []},
            "power_players": [{"npc_id": n, "text": "Has documented local influence.", "hidden": False, "evidence": [p]} for n, p in list(own.items())[:5]],
            "encounters": [{"members": [n], "cue": "Taking a quiet moment.", "reason": "", "member_reasons": [{"npc_id": n, "text": "Taking a quiet moment.", "evidence": [p]}], "evidence": [p]} for n, p in own.items() if n != late],
            "gm_notes": [],
        }
        if "encounter_cues" in payload["sections"]:
            values["encounter_cues"] = [
                ({
                    "number": row["number"], "members": row["members"],
                    "activity": "Compare details around the room.",
                    "topic": "How the neighborhood has changed.",
                    "temperature": "curious but formal",
                    "player_entry": "Ask what in the room deserves a closer look.",
                    "evidence": [own[n] for n in row["members"]], "knowledge_evidence": [],
                } if len(row["members"]) > 1 else {
                    "number": row["number"], "members": row["members"],
                    "approach": "Ask what has caught their attention tonight.",
                    "evidence": [own[row["members"][0]]], "knowledge_evidence": [],
                })
                for row in payload.get("accepted_sections", {}).get("encounters", [])
            ]
        return json.dumps({s: values[s] for s in payload["sections"]})


def test_scene_sections_round_trip_and_skip_provider(tmp_path):
    provider = SceneProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    budget = RequestBudget()
    context = engine.context(documents(), budget)
    scene = engine.scene(context, "c", {"seed": 1}, {}, budget)
    assert scene["incomplete"] is False
    count = len(provider.calls)
    again = engine.scene(context, "c", {"seed": 1}, {}, RequestBudget())
    assert again == scene
    assert len(provider.calls) == count
    rows, stage = engine.relevance(context, "a", scene, [])
    assert rows and stage["status"] == "complete"
    count = len(provider.calls)
    assert engine.relevance(context, "a", scene, [])[0] == rows
    assert len(provider.calls) == count


def test_public_event_pipeline_preserves_canonical_rumors_and_reuses_readings(tmp_path):
    from core.club_generation import ClubGenerationService
    paths = []
    for name in ("Ada", "Bea", "Cy"):
        path = tmp_path / (name + ".md")
        path.write_text(f"# {name}\n\nStatus: Influential\n\n## Whispers\n- {name} may hide a valuable ledger.\n\n## Backstory\nPublicly supports the local shelter.\n", encoding="utf-8")
        paths.append(str(path))
    provider = SceneProvider()
    service = ClubGenerationService({}, cache_root=tmp_path / "cache", provider=provider)
    baseline = service._build_canonical_event_result(paths, seed=8, use_ai=False)
    observed = []
    result = service.build_event_result(paths, seed=8, baseline_ready=lambda r: observed.append((len(provider.calls), r)))
    assert observed[0][0] == 0
    assert result.event.dashboard["scene_prep"]["incomplete"] is False
    fields = ("rumors", "rumors_in_circulation", "rumor_selection")
    for field in fields:
        assert result.event.dashboard.get(field) == baseline.event.dashboard.get(field)
    assert result.attendee_facts == baseline.attendee_facts
    count = len(provider.calls)
    cached = service.build_event_result(paths, seed=8)
    assert len(provider.calls) == count
    assert cached.event.dashboard["scene_prep"] == result.event.dashboard["scene_prep"]
    changed = service.change_late_arrival_prep(result)
    assert changed.event.late_arrival_id != result.event.late_arrival_id
    assert changed.event.dashboard["event"]["late_arrival_id"] == changed.event.late_arrival_id
    scene_call = next(call for call in reversed(provider.calls) if "event" in call)
    guidance_call = next(call for call in reversed(provider.calls) if "rumors" in call)
    assert scene_call["event"]["late_arrival_id"] == changed.event.late_arrival_id
    assert guidance_call["late_arrival_id"] == changed.event.late_arrival_id
    assert changed.event.seed == result.event.seed
    for field in fields:
        assert changed.event.dashboard.get(field) == result.event.dashboard.get(field)
    assert changed.event.dashboard["scene_prep"]["revision"] != result.event.dashboard["scene_prep"]["revision"]
    assert len(provider.calls) == count + 4  # readings were reused; scene and rumor-guidance passes reran


def test_new_prep_ui_and_copy_have_same_numbered_entries(qapp):
    from core.club_models import ClubEvent
    from ui.club_tab import ClubTab, build_table_prep_text
    docs = documents()
    scene = fallback_scene(docs, "b", {"a": "Sheriff"})
    event = ClubEvent("e", ("a", "b", "c"), "b", {"scene_prep": scene, "rumors_in_circulation": ["A selected rumor."]}, "cache", 1)
    attendees = [{"npc_id": d.npc_id, "name": d.name} for d in docs]
    tab = ClubTab(object())
    try:
        tab._attendees = {r["npc_id"]: r for r in attendees}
        tab.summaryBrowser.setHtml(tab._dashboard_html(event.dashboard, "b"))
        visible = tab.summaryBrowser.toPlainText()
        copied = build_table_prep_text(event, attendees, {})
        for heading in ("Start the Scene", "Who Matters Here", "Social Groups and Loners", "Useful to Know", "Rumors in Circulation"):
            assert heading in visible and heading in copied
        for old in ("Hot Connections", "Possible Pressure", "Room Situation"):
            assert old not in visible and old not in copied
        for entry in ("1. Ada", "2. Cy", "3. Bea"):
            assert entry in visible and entry in copied
        assert "A selected rumor." in copied
        assert "evidence" not in copied
    finally:
        tab.close()


def test_power_fallback_keeps_full_roster_and_five_clickable_examples(tmp_path, qapp):
    from core.club_generation import ClubGenerationService
    from ui.club_tab import ClubTab, build_table_prep_text

    paths = []
    for name in ("Gale", "Ada", "Faye", "Cy", "Bea", "Eve", "Dee"):
        path = tmp_path / f"{name}.md"
        path.write_text(f"# {name}\n\nStatus: {name} position\n", encoding="utf-8")
        paths.append(str(path))
    service = ClubGenerationService({}, cache_root=tmp_path / "cache", vault_root=tmp_path)
    result = service.build_event_result(paths, seed=9, use_ai=False)
    tab = ClubTab(service)
    try:
        tab._on_event_ready(result)
        assert tab.guestList.count() == 7

        dashboard_html = tab._dashboard_html(result.event.dashboard, result.event.late_arrival_id)
        start = dashboard_html.index("<h2>Who Matters Here</h2>")
        end = dashboard_html.index("<h2>Social Groups and Loners</h2>", start)
        who_matters_html = dashboard_html[start:end]
        ordered_identities = sorted(result.identities, key=lambda item: (item.display_name.casefold(), item.npc_id))
        for identity in ordered_identities[:5]:
            assert f'href="npc:{identity.npc_id}"' in who_matters_html
        for identity in ordered_identities[5:]:
            assert f'href="npc:{identity.npc_id}"' not in who_matters_html

        copied = build_table_prep_text(result.event, result.attendee_summaries(), {})
        copied_section = copied.split("Who Matters Here\n", 1)[1].split("\nSocial Groups and Loners", 1)[0]
        assert "Power assessment unavailable; showing five known-position guests." in copied_section
        assert sum(f"{identity.display_name}:" in copied_section for identity in result.identities) == 5
    finally:
        tab.close()


def evidence_context(docs):
    evidence = {p.passage_id: p for d in docs for p in d.passages}
    annotations = {p.passage_id: {"visibility": "public", "topics": [], "categories": ["social", "power"]} for p in evidence.values()}
    own = {d.npc_id: next(p.passage_id for p in reversed(d.passages) if p.text.strip()) for d in docs}
    return evidence, annotations, own


def test_supported_group_and_lonely_guests_cover_everyone_once():
    docs = documents()
    evidence, annotations, own = evidence_context(docs)
    group = {"members": ["b", "a"], "cue": "Discussing shelter work.", "reason": "A public interest in animal care.", "evidence": [own["a"], own["b"]],
             "member_reasons": [{"npc_id": n, "text": "Interested in animal care.", "evidence": [own["a"], own["b"]]} for n in ("a", "b")]}
    rows = validate_scene_section("encounters", [group], docs, "c", evidence, annotations)
    from core.club_prep import number_encounters
    pending = fallback_scene(docs, "c")["encounters"][-1]
    numbered = number_encounters([pending, *rows], docs, "c")
    assert [r["members"] for r in numbered] == [["a", "b"], ["c"]]
    assert [r["number"] for r in numbered] == [1, 2]
    assert group["members"] == ["b", "a"]  # no mutation


def test_shared_private_goals_do_not_create_a_group():
    docs = documents()
    evidence, annotations, own = evidence_context(docs)
    for a in annotations.values():
        a["visibility"] = "private"
        a["categories"] = ["motives"]
    group = {"members": ["a", "b"], "cue": "Planning together.", "reason": "Similar secret goals.", "evidence": [own["a"], own["b"]],
             "member_reasons": [{"npc_id": n, "text": "Has a private goal.", "evidence": [own[n]]} for n in ("a", "b")]}
    with pytest.raises(PrepError, match="accessible_connection"):
        validate_scene_section("encounters", [group], docs, "c", evidence, annotations)


def test_power_order_preserves_ai_assessment_and_marks_hidden_power():
    docs = documents()
    evidence, annotations, own = evidence_context(docs)
    annotations[own["b"]]["visibility"] = "private"
    rows = [{"npc_id": "b", "text": "Secretly controls the mayor.", "hidden": True, "evidence": [own["b"]]},
            {"npc_id": "a", "text": "Runs the public shelter.", "hidden": False, "evidence": [own["a"]]}]
    result = validate_scene_section("power_players", rows, docs, "c", evidence, annotations)
    assert [r["npc_id"] for r in result] == ["b", "a"]
    rows[0]["hidden"] = False
    with pytest.raises(PrepError, match="hidden_power"):
        validate_scene_section("power_players", rows, docs, "c", evidence, annotations)


def test_explicit_actor_knowledge_can_support_private_target_interest():
    docs = (capture_document("a", "Ada", "Ada knows Bea secretly controls the mayor.", "a.md"), documents()[1])
    evidence, annotations, own = evidence_context(docs)
    annotations[own["a"]]["categories"] = ["knowledge"]
    annotations[own["b"]]["visibility"] = "private"
    row = {"npc_id": "b", "text": "Ask for an introduction to the mayor.", "classification": "inferred", "evidence": [own["a"], own["b"]],
           "knowledge_evidence": [{"passage_id": own["a"], "subject_npc_id": "b", "target_passage_id": own["b"]}]}
    assert validate_relevance([row], "a", docs, evidence, annotations, [])[0]["npc_id"] == "b"


def test_knowing_one_secret_does_not_unlock_unrelated_private_material():
    docs = (capture_document("a", "Ada", "Ada knows Bea controls the mayor."),
            capture_document("b", "Bea", "Controls the mayor.\n\nSecretly plans to burn the shelter."))
    evidence, annotations, own = evidence_context(docs)
    annotations[own["a"]]["categories"] = ["knowledge"]
    for p in docs[1].passages:
        annotations[p.passage_id]["visibility"] = "private"
    row = {"npc_id": "b", "text": "Confront the arson plan.", "classification": "inferred",
           "evidence": [own["a"], own["b"]], "knowledge_evidence": [
               {"passage_id": own["a"], "subject_npc_id": "b", "target_passage_id": docs[1].passages[0].passage_id}]}
    with pytest.raises(PrepError, match="private_target_knowledge"):
        validate_relevance([row], "a", docs, evidence, annotations, [])


def test_read_complete_without_power_evidence_remains_unassessed(tmp_path):
    provider = SceneProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    context = engine.context(documents(), RequestBudget())
    for a in context.readings["c"]["annotations"]:
        a["categories"] = ["social"]
    scene = engine.scene(context, "c", {}, {}, RequestBudget())
    assert scene["power_incomplete"] is True


def test_failed_reading_retains_others_and_retry_resumes(tmp_path):
    class OneFailure(SceneProvider):
        fail = True
        def generate_from_messages(self, messages, **kwargs):
            payload = json.loads(messages[-1]["content"].split("INPUT_JSON:\n", 1)[1])
            if self.fail and payload.get("npc_id") == "b" and "passages" in payload:
                self.calls.append(payload)
                raise TimeoutError()
            return super().generate_from_messages(messages, **kwargs)
    provider = OneFailure()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    context = engine.context(documents(), RequestBudget())
    assert context.readings["a"]["complete"] and context.readings["c"]["complete"]
    assert not context.readings["b"]["complete"]
    count = len(provider.calls)
    provider.fail = False
    retried = engine.context(documents(), RequestBudget())
    assert all(r["complete"] for r in retried.readings.values())
    assert len(provider.calls) == count + 1
    assert context.digest != retried.digest


@pytest.mark.parametrize("message,expected_calls", [("Unauthorized 401", 1), ("timed out", 3)])
def test_provider_circuit_stops_current_pass(tmp_path, message, expected_calls):
    class Failure:
        calls = 0
        def generate_from_messages(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError(message)
    provider = Failure()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    docs = tuple(capture_document(str(n), str(n), "Some source.") for n in range(8))
    budget = RequestBudget()
    context = engine.context(docs, budget)
    scene = engine.scene(context, "7", {}, {}, budget)
    assert provider.calls == expected_calls
    assert scene["incomplete"] and len(scene["encounters"]) == 8
    assert not list((tmp_path / "prep_readings").glob("*.json"))


def test_malformed_annotation_value_retries_without_losing_other_readings(tmp_path):
    class Invalid(ReadingProvider):
        def generate_from_messages(self, messages, **kwargs):
            result = json.loads(super().generate_from_messages(messages, **kwargs))
            result["annotations"][0]["passage_id"] = {"unhashable": True}
            return json.dumps(result)
    provider = Invalid()
    readings = PrepEngine(ClubCacheService(tmp_path), provider, {}).read_documents(documents(), RequestBudget())
    assert len(provider.calls) == 6
    assert not any(r["complete"] for r in readings.values())


def test_section_retry_keeps_good_sections_and_cached_force_failure(tmp_path):
    class Partial(SceneProvider):
        rejected_once = False
        fail = False
        def generate_from_messages(self, messages, **kwargs):
            if self.fail:
                self.calls.append({"failed": True})
                raise TimeoutError()
            data = json.loads(super().generate_from_messages(messages, **kwargs))
            if "encounters" in data and not self.rejected_once:
                self.rejected_once = True
                data["encounters"] = "invalid"
            return json.dumps(data)
    provider = Partial()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    budget = RequestBudget()
    context = engine.context(documents(), budget)
    scene = engine.scene(context, "c", {}, {}, budget)
    assert scene["incomplete"] is False
    assert provider.calls[-1]["sections"] == ["encounters"]
    provider.fail = True
    again = engine.scene(context, "c", {}, {}, RequestBudget(), force=True)
    assert again["opening"] == scene["opening"] and again["encounters"] == scene["encounters"]
    assert again["stages"]["opening"]["regeneration_failed"] is True


def test_fifty_guest_roster_is_present_in_both_scene_passes(tmp_path):
    docs = tuple(capture_document(str(n), f"Guest {n}", f"Guest {n} publicly supports a shelter.") for n in range(50))
    provider = SceneProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    budget = RequestBudget()
    context = engine.context(docs, budget)
    scene = engine.scene(context, "49", {}, {}, budget)
    scene_calls = [p for p in provider.calls if "roster" in p]
    assert len(scene_calls) == 2
    assert all(len(p["roster"]) == 50 for p in scene_calls)
    assert budget.used == 52
    assert len(scene["encounters"]) == 50
    assert all(r["complete"] for r in context.readings.values())


def test_new_schema_policy_requires_prep_owner_for_prompt_changes():
    from tools.check_schema_version_bump import check_paths
    assert check_paths(["scenesmith/templates/club_prep.j2"])
    assert not check_paths(["scenesmith/templates/club_prep.j2", "scenesmith/core/club_prep.py"])


def test_omitted_attendee_keeps_partial_arrangement_and_can_retry(tmp_path):
    class Omission(SceneProvider):
        omit = True
        def generate_from_messages(self, messages, **kwargs):
            value = json.loads(super().generate_from_messages(messages, **kwargs))
            if self.omit and "encounters" in value:
                value["encounters"] = value["encounters"][:1]
            return json.dumps(value)
    provider = Omission()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    context = engine.context(documents(), RequestBudget())
    first = engine.scene(context, "c", {}, {}, RequestBudget())
    assert first["incomplete"]
    assert len(first["encounters"]) == 3
    assert sum(r["basis"] == "suggested" for r in first["encounters"]) == 1
    count = len(provider.calls)
    provider.omit = False
    second = engine.scene(context, "c", {}, {}, RequestBudget())
    assert not second["incomplete"]
    assert len(provider.calls) == count + 2
    assert provider.calls[-1]["sections"] == ["encounters"]


@pytest.mark.parametrize("field,value", [("deepseek_thinking", True), ("supports_deepseek_thinking", True), ("timeout", 90)])
def test_transport_configuration_changes_reading_identity(tmp_path, field, value):
    provider = ReadingProvider()
    cache = ClubCacheService(tmp_path)
    PrepEngine(cache, provider, {}).read_documents(documents()[:1], RequestBudget())
    PrepEngine(cache, provider, {field: value}).read_documents(documents()[:1], RequestBudget())
    assert len(provider.calls) == 2


def test_debug_support_redacts_secrets_and_never_dumps_full_sheet(tmp_path):
    provider = ReadingProvider()
    docs = (capture_document("a", "Ada", "api_key=sk-testcredential " + "Private source. " * 200),)
    context = PrepEngine(ClubCacheService(tmp_path), provider, {}).context(docs, RequestBudget())
    support = context.debug_support([docs[0].passages[0].passage_id])
    serialized = json.dumps(support)
    assert "sk-testcredential" not in serialized
    assert len(serialized) < 650


def test_retry_of_early_chunk_reuses_successful_later_chunk(tmp_path):
    class EarlyFailure(ReadingProvider):
        fail = True
        def generate_from_messages(self, messages, **kwargs):
            payload = json.loads(messages[-1]["content"].split("INPUT_JSON:\n", 1)[1])
            if self.fail and payload["passages"][0]["text"].startswith("FIRST"):
                self.calls.append(payload)
                raise TimeoutError()
            return super().generate_from_messages(messages, **kwargs)
    doc = capture_document("a", "Ada", "FIRST " + "Shelter detail. " * 4000)
    provider = EarlyFailure()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    first = engine.read_documents([doc], RequestBudget())["a"]
    assert first["completed_chunks"] >= 1 and not first["complete"]
    count = len(provider.calls)
    provider.fail = False
    second = engine.read_documents([doc], RequestBudget())["a"]
    assert second["complete"] and len(provider.calls) == count + 1


def test_oversized_mandatory_roster_skips_provider_but_keeps_all_guests(tmp_path, monkeypatch):
    import core.club_prep as prep
    provider = SceneProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    context = engine.context(documents(), RequestBudget())
    count = len(provider.calls)
    monkeypatch.setattr(prep, "PREP_TOKEN_BUDGET", 500)
    scene = engine.scene(context, "c", {}, {}, RequestBudget())
    assert len(provider.calls) == count
    assert scene["incomplete"] and len(scene["encounters"]) == 3


def test_other_sheet_and_reasoning_version_invalidate_only_dependent_prep(tmp_path):
    provider = SceneProvider()
    cache = ClubCacheService(tmp_path)
    engine = PrepEngine(cache, provider, {}, reasoning_identity="v1")
    context = engine.context(documents(), RequestBudget())
    original = engine.scene(context, "c", {}, {}, RequestBudget())
    engine.relevance(context, "a", original, [])
    count = len(provider.calls)
    changed_docs = (*documents()[:1], capture_document("b", "Bea", "Publicly owns a veterinary hospital."), documents()[2])
    changed = engine.context(changed_docs, RequestBudget())
    assert len(provider.calls) == count + 1
    newer = engine.scene(changed, "c", {}, {}, RequestBudget())
    assert newer["revision"] != original["revision"]
    engine.relevance(changed, "a", newer, [])
    assert len(provider.calls) == count + 5
    engine_v2 = PrepEngine(cache, provider, {}, reasoning_identity="v2")
    count = len(provider.calls)
    assert engine_v2.context(changed_docs, RequestBudget()).digest == changed.digest
    engine_v2.scene(changed, "c", {}, {}, RequestBudget())
    assert len(provider.calls) == count + 2
