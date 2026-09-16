from __future__ import annotations

from copy import deepcopy

import pytest

from core.club_prep import PrepError, capture_document, npc_scene_context, visible_scene


def _fixture():
    documents = (
        capture_document("a", "Ada", "Ada publicly enjoys old architecture."),
        capture_document("b", "Bea", "Bea publicly restores historic buildings."),
        capture_document("c", "Cy", "Cy publicly collects local postcards."),
        capture_document("late", "Late", "Late studies the city skyline."),
    )
    evidence = {passage.passage_id: passage for document in documents for passage in document.passages}
    annotations = {
        passage_id: {"visibility": "public", "categories": ["social"]}
        for passage_id in evidence
    }
    own = {passage.npc_id: passage.passage_id for passage in evidence.values()}
    fixed = [
        {
            "number": 1,
            "members": ["a", "b"],
            "label": "Architecture",
            "cue": "A small conversation is forming.",
            "reason": "Matching tags: architecture.",
            "member_reasons": [],
            "evidence": [],
            "basis": "tag_similarity",
            "availability": "present",
        },
        {
            "number": 2,
            "members": ["c"],
            "label": "cy",
            "cue": "No matching tag group; available for an individual encounter.",
            "reason": "",
            "member_reasons": [],
            "evidence": [],
            "basis": "tag_similarity",
            "availability": "present",
        },
        {
            "number": 3,
            "members": ["late"],
            "label": "late",
            "cue": "Not here yet—reroll until arrival.",
            "reason": "",
            "member_reasons": [],
            "evidence": [],
            "basis": "tag_similarity",
            "availability": "expected",
        },
    ]
    cues = [
        {
            "number": 1,
            "members": ["a", "b"],
            "activity": "Compare details in the club's architecture.",
            "topic": "How older buildings acquire new uses.",
            "temperature": "curious but formal",
            "player_entry": "Ask which feature of the room deserves preservation.",
            "evidence": [own["a"], own["b"]],
            "knowledge_evidence": [],
        },
        {
            "number": 2,
            "members": ["c"],
            "approach": "Ask whether the club has appeared on an old postcard.",
            "evidence": [own["c"]],
            "knowledge_evidence": [],
        },
    ]
    return documents, evidence, annotations, own, fixed, cues


def test_valid_cues_preserve_exact_fixed_identity_and_visible_projection():
    from core.club_prep import apply_encounter_cues, validate_encounter_cues

    documents, evidence, annotations, _own, fixed, cues = _fixture()
    before = deepcopy(fixed)
    validated = validate_encounter_cues(cues, fixed, documents, evidence, annotations)
    joined = apply_encounter_cues(fixed, validated)

    assert fixed == before
    assert [(row["number"], row["members"]) for row in joined] == [
        (row["number"], row["members"]) for row in fixed
    ]
    assert joined[0]["conversation_cue"]["temperature"] == "curious but formal"
    assert joined[1]["conversation_cue"]["approach"].startswith("Ask whether")
    assert "conversation_cue" not in joined[2]

    projection = visible_scene({"encounters": joined})["encounters"]
    assert projection[0]["conversation_cue"] == {
        key: cues[0][key] for key in ("activity", "topic", "temperature", "player_entry")
    }
    assert projection[1]["conversation_cue"] == {"approach": cues[1]["approach"]}
    assert "evidence" not in repr(projection)
    assert "knowledge_evidence" not in repr(projection)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows.reverse(),
        lambda rows: rows[0].update(number=9),
        lambda rows: rows[0].update(members=["b", "a"]),
        lambda rows: rows[0].update(members=["a"]),
        lambda rows: rows.append(deepcopy(rows[0])),
        lambda rows: rows.pop(),
        lambda rows: rows.append({**deepcopy(rows[1]), "number": 3, "members": ["late"]}),
    ],
)
def test_cue_envelope_rejects_every_membership_mutation(mutate):
    from core.club_prep import validate_encounter_cues

    documents, evidence, annotations, _own, fixed, cues = _fixture()
    mutate(cues)
    with pytest.raises(PrepError, match="encounter_cue_identity"):
        validate_encounter_cues(cues, fixed, documents, evidence, annotations)


def test_cue_schema_rejects_extra_keys_lengths_and_missing_member_support():
    from core.club_prep import validate_encounter_cues

    documents, evidence, annotations, _own, fixed, cues = _fixture()
    invalid = deepcopy(cues)
    invalid[0]["friendship"] = "established"
    with pytest.raises(PrepError, match="invalid_fields"):
        validate_encounter_cues(invalid, fixed, documents, evidence, annotations)

    invalid = deepcopy(cues)
    invalid[0]["temperature"] = "x" * 41
    with pytest.raises(PrepError, match="text_length"):
        validate_encounter_cues(invalid, fixed, documents, evidence, annotations)

    invalid = deepcopy(cues)
    invalid[0]["evidence"] = invalid[0]["evidence"][:1]
    with pytest.raises(PrepError, match="missing_character_support"):
        validate_encounter_cues(invalid, fixed, documents, evidence, annotations)


def test_empty_singleton_approach_is_valid_without_evidence():
    from core.club_prep import apply_encounter_cues, validate_encounter_cues

    documents, evidence, annotations, _own, fixed, cues = _fixture()
    invalid = deepcopy(cues)
    invalid[1]["approach"] = ""
    with pytest.raises(PrepError, match="unexpected_evidence"):
        validate_encounter_cues(invalid, fixed, documents, evidence, annotations)

    cues[1].update(approach="", evidence=[])
    validated = validate_encounter_cues(cues, fixed, documents, evidence, annotations)
    joined = apply_encounter_cues(fixed, validated)
    assert "conversation_cue" not in joined[1]


def test_private_cross_member_support_requires_exact_knowledge_proof():
    from core.club_prep import validate_encounter_cues

    documents = (
        capture_document("a", "Ada", "Ada publicly likes architecture.\n\nAda knows Bea keeps a hidden chapel."),
        capture_document("b", "Bea", "Bea keeps a hidden chapel beneath the old station."),
    )
    evidence = {passage.passage_id: passage for document in documents for passage in document.passages}
    ada_passages = [passage for passage in documents[0].passages if passage.text.strip()]
    public_a, knowledge_a = ada_passages
    secret_b = next(passage for passage in documents[1].passages if passage.text.strip())
    annotations = {
        public_a.passage_id: {"visibility": "public", "categories": ["social"]},
        knowledge_a.passage_id: {"visibility": "unknown", "categories": ["knowledge"]},
        secret_b.passage_id: {"visibility": "private", "categories": ["backstory"]},
    }
    fixed = [{
        "number": 1, "members": ["a", "b"], "label": "Station",
        "cue": "A small conversation is forming.", "reason": "", "member_reasons": [],
        "evidence": [], "basis": "tag_similarity", "availability": "present",
    }]
    row = {
        "number": 1, "members": ["a", "b"], "activity": "Compare old station plans.",
        "topic": "Whether the station still has hidden rooms.", "temperature": "guarded",
        "player_entry": "Ask which parts of the station remain closed.",
        "evidence": [public_a.passage_id, secret_b.passage_id], "knowledge_evidence": [],
    }
    with pytest.raises(PrepError, match="private_target_knowledge"):
        validate_encounter_cues([row], fixed, documents, evidence, annotations)

    row["knowledge_evidence"] = [{
        "actor_npc_id": "a", "passage_id": knowledge_a.passage_id,
        "subject_npc_id": "b", "target_passage_id": secret_b.passage_id,
    }]
    assert validate_encounter_cues([row], fixed, documents, evidence, annotations)

    row["knowledge_evidence"][0]["target_passage_id"] = public_a.passage_id
    with pytest.raises(PrepError, match="unsupported_knowledge"):
        validate_encounter_cues([row], fixed, documents, evidence, annotations)


def test_npc_scene_context_excludes_all_conversation_presentation_and_support():
    from core.club_prep import apply_encounter_cues, validate_encounter_cues

    documents, evidence, annotations, _own, fixed, cues = _fixture()
    joined = apply_encounter_cues(
        fixed,
        validate_encounter_cues(cues, fixed, documents, evidence, annotations),
    )
    context = npc_scene_context({"encounters": joined, "incomplete": False})
    serialized = repr(context)
    for forbidden in ("conversation_cue", "architecture", "evidence", "player_entry", "approach"):
        assert forbidden not in serialized


def test_fixed_cues_generate_with_other_sections_and_round_trip_cache(tmp_path):
    from core.club_cache import ClubCacheService
    from core.club_prep import PrepEngine, RequestBudget
    from test_club_prep import SceneProvider

    documents, _evidence, _annotations, _own, fixed, _cues = _fixture()
    provider = SceneProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {}, reasoning_identity="dashboard-v1")
    context = engine.context(documents, RequestBudget())
    scene = engine.scene(context, "late", {}, {}, RequestBudget(), fixed_encounters=fixed)

    assert not scene["incomplete"]
    assert scene["stages"]["encounters"] == {"status": "complete", "method": "tags"}
    assert scene["stages"]["encounter_cues"] == {"status": "complete"}
    assert scene["encounters"][0]["conversation_cue"]["activity"]
    assert scene["encounters"][1]["conversation_cue"]["approach"]
    assert "conversation_cue" not in scene["encounters"][2]
    final = next(call for call in reversed(provider.calls) if "evidence" in call and "sections" in call)
    assert "encounter_cues" in final["sections"]
    assert "encounters" not in final["sections"]
    assert final["accepted_sections"]["encounters"] == [
        {key: row[key] for key in ("number", "members", "basis", "availability")}
        for row in fixed if row["availability"] == "present"
    ]
    assert "Architecture" not in repr(final["accepted_sections"])

    call_count = len(provider.calls)
    cached = engine.scene(context, "late", {}, {}, RequestBudget(), fixed_encounters=fixed)
    assert cached == scene
    assert len(provider.calls) == call_count


def test_membership_mutation_retries_then_keeps_exact_deterministic_rows(tmp_path):
    import json

    from core.club_cache import ClubCacheService
    from core.club_prep import PrepEngine, RequestBudget
    from test_club_prep import SceneProvider

    class MutatingProvider(SceneProvider):
        def generate_from_messages(self, messages, **kwargs):
            raw = super().generate_from_messages(messages, **kwargs)
            if "INPUT_JSON:\n" not in messages[-1]["content"]:
                return raw
            payload = json.loads(messages[-1]["content"].split("INPUT_JSON:\n", 1)[1])
            value = json.loads(raw)
            if "encounter_cues" in value:
                value["encounter_cues"][0]["members"] = list(reversed(value["encounter_cues"][0]["members"]))
            return json.dumps(value)

    documents, _evidence, _annotations, _own, fixed, _cues = _fixture()
    provider = MutatingProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {}, reasoning_identity="dashboard-v1")
    context = engine.context(documents, RequestBudget())
    scene = engine.scene(context, "late", {}, {}, RequestBudget(), fixed_encounters=fixed)

    assert scene["incomplete"]
    assert scene["stages"]["encounter_cues"]["status"] == "unavailable"
    assert [(row["number"], row["members"], row["cue"]) for row in scene["encounters"]] == [
        (row["number"], row["members"], row["cue"]) for row in fixed
    ]
    assert all("conversation_cue" not in row for row in scene["encounters"])
    finals = [call for call in provider.calls if "sections" in call and "evidence" in call]
    assert len(finals) == 2
    assert not list((tmp_path / "prep_sections").glob("*_encounter_cues.json"))


def test_one_bad_cue_response_repairs_and_caches_only_validated_output(tmp_path):
    import json

    from core.club_cache import ClubCacheService
    from core.club_prep import PrepEngine, RequestBudget
    from test_club_prep import SceneProvider

    class RepairingProvider(SceneProvider):
        bad = True

        def generate_from_messages(self, messages, **kwargs):
            raw = super().generate_from_messages(messages, **kwargs)
            if "INPUT_JSON:\n" not in messages[-1]["content"]:
                return raw
            value = json.loads(raw)
            if self.bad and "encounter_cues" in value:
                self.bad = False
                value["encounter_cues"][0]["number"] = 99
            return json.dumps(value)

    documents, _evidence, _annotations, _own, fixed, _cues = _fixture()
    provider = RepairingProvider()
    cache = ClubCacheService(tmp_path)
    engine = PrepEngine(cache, provider, {}, reasoning_identity="dashboard-v1")
    context = engine.context(documents, RequestBudget())
    scene = engine.scene(context, "late", {}, {}, RequestBudget(), fixed_encounters=fixed)
    assert not scene["incomplete"]
    assert scene["encounters"][0]["conversation_cue"]
    assert len([call for call in provider.calls if "sections" in call and "evidence" in call]) == 2
    cached_files = list((tmp_path / "prep_sections").glob("*_encounter_cues.json"))
    assert len(cached_files) == 1


def test_cue_version_invalidates_only_cues_and_reuses_readings(tmp_path, monkeypatch):
    import core.club_prep as prep
    from core.club_cache import ClubCacheService
    from test_club_prep import SceneProvider

    documents, _evidence, _annotations, _own, fixed, _cues = _fixture()
    provider = SceneProvider()
    engine = prep.PrepEngine(ClubCacheService(tmp_path), provider, {}, reasoning_identity="dashboard-v1")
    context = engine.context(documents, prep.RequestBudget())
    engine.scene(context, "late", {}, {}, prep.RequestBudget(), fixed_encounters=fixed)
    calls = len(provider.calls)

    monkeypatch.setattr(prep, "CLUB_PREP_ENCOUNTER_CUE_SCHEMA_VERSION", "club_prep_encounter_cue_v2")
    updated = engine.scene(context, "late", {}, {}, prep.RequestBudget(), fixed_encounters=fixed)
    new_calls = provider.calls[calls:]
    assert [call["sections"] for call in new_calls if "sections" in call] == [
        ["encounter_cues"], ["encounter_cues"]
    ]
    assert updated["encounters"][0]["conversation_cue"]


def test_malformed_cue_cache_rebuilds_only_cue_section(tmp_path):
    from core.club_cache import ClubCacheService
    from core.club_prep import PrepEngine, RequestBudget
    from test_club_prep import SceneProvider

    documents, _evidence, _annotations, _own, fixed, _cues = _fixture()
    provider = SceneProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {}, reasoning_identity="dashboard-v1")
    context = engine.context(documents, RequestBudget())
    engine.scene(context, "late", {}, {}, RequestBudget(), fixed_encounters=fixed)
    cue_cache = next((tmp_path / "prep_sections").glob("*_encounter_cues.json"))
    cue_cache.write_text("{}", encoding="utf-8")
    calls = len(provider.calls)

    rebuilt = engine.scene(context, "late", {}, {}, RequestBudget(), fixed_encounters=fixed)
    assert [call["sections"] for call in provider.calls[calls:] if "sections" in call] == [
        ["encounter_cues"], ["encounter_cues"]
    ]
    assert rebuilt["encounters"][0]["conversation_cue"]


def test_failed_forced_regeneration_retains_valid_cached_cues(tmp_path):
    import json

    from core.club_cache import ClubCacheService
    from core.club_prep import PrepEngine, RequestBudget
    from test_club_prep import SceneProvider

    class FailingFinalProvider(SceneProvider):
        fail = False

        def generate_from_messages(self, messages, **kwargs):
            if self.fail and "INPUT_JSON:\n" in messages[-1]["content"]:
                payload = json.loads(messages[-1]["content"].split("INPUT_JSON:\n", 1)[1])
                if "evidence" in payload:
                    self.calls.append(payload)
                    raise TimeoutError()
            return super().generate_from_messages(messages, **kwargs)

    documents, _evidence, _annotations, _own, fixed, _cues = _fixture()
    provider = FailingFinalProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {}, reasoning_identity="dashboard-v1")
    context = engine.context(documents, RequestBudget())
    first = engine.scene(context, "late", {}, {}, RequestBudget(), fixed_encounters=fixed)
    provider.fail = True
    retained = engine.scene(context, "late", {}, {}, RequestBudget(), fixed_encounters=fixed, force=True)

    assert retained["encounters"] == first["encounters"]
    assert retained["stages"]["encounter_cues"] == {
        "status": "complete", "from_cache": True, "regeneration_failed": True,
    }
    assert not retained["incomplete"]


def test_malformed_json_exhaustion_keeps_neutral_cues_and_writes_no_cue_cache(tmp_path):
    import json

    from core.club_cache import ClubCacheService
    from core.club_prep import PrepEngine, RequestBudget
    from test_club_prep import SceneProvider

    class MalformedCueProvider(SceneProvider):
        def generate_from_messages(self, messages, **kwargs):
            if "INPUT_JSON:\n" in messages[-1]["content"]:
                payload = json.loads(messages[-1]["content"].split("INPUT_JSON:\n", 1)[1])
                if payload.get("sections") == ["encounter_cues"]:
                    self.calls.append(payload)
                    return "not json"
            return super().generate_from_messages(messages, **kwargs)

    documents, _evidence, _annotations, _own, fixed, _cues = _fixture()
    cache = ClubCacheService(tmp_path)
    warm_provider = SceneProvider()
    engine = PrepEngine(cache, warm_provider, {}, reasoning_identity="dashboard-v1")
    context = engine.context(documents, RequestBudget())
    engine.scene(context, "late", {}, {}, RequestBudget(), fixed_encounters=fixed)
    next((tmp_path / "prep_sections").glob("*_encounter_cues.json")).unlink()

    provider = MalformedCueProvider()
    failed = PrepEngine(cache, provider, {}, reasoning_identity="dashboard-v1").scene(
        context, "late", {}, {}, RequestBudget(), fixed_encounters=fixed,
    )
    assert failed["incomplete"]
    assert failed["stages"]["encounter_cues"]["status"] == "unavailable"
    assert all("conversation_cue" not in row for row in failed["encounters"])
    assert [row["cue"] for row in failed["encounters"]] == [row["cue"] for row in fixed]
    assert not list((tmp_path / "prep_sections").glob("*_encounter_cues.json"))


def test_real_qt_group_and_singleton_cue_links_preserve_compact_navigation(qapp, qtbot, monkeypatch):
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication, QToolTip

    from core.club_models import ClubEvent
    from core.club_prep import apply_encounter_cues, validate_encounter_cues
    from ui.club_tab import ClubTab, build_table_prep_text

    documents, evidence, annotations, _own, fixed, cues = _fixture()
    encounters = apply_encounter_cues(
        fixed,
        validate_encounter_cues(cues, fixed, documents, evidence, annotations),
    )
    scene = {
        "opening": {"text": "Choose someone to approach.", "characters": [], "evidence": []},
        "power_players": [], "power_assessed": False, "power_incomplete": True,
        "encounters": encounters, "gm_notes": [], "incomplete": False,
    }
    event = ClubEvent("event", tuple(d.npc_id for d in documents), "late", {"scene_prep": scene}, "cache", 1)
    attendees = [{"npc_id": d.npc_id, "name": d.name} for d in documents]
    tab = ClubTab(object())
    qtbot.addWidget(tab)
    tab._event = event
    tab._attendees = {row["npc_id"]: row for row in attendees}
    tab.summaryBrowser.setHtml(tab._dashboard_html(event.dashboard, "late"))

    page = tab.summaryBrowser.toPlainText()
    html = tab.summaryBrowser.toHtml()
    assert "Architecture · 2 guests" in page
    assert "Suggested Activity" not in page
    assert "npc:c" in html and "group:2" in html and "Approach" in page
    assert "group:3" not in html

    previews = []
    monkeypatch.setattr(QToolTip, "showText", lambda point, text, widget: previews.append(text))
    tab.summaryBrowser.highlighted.emit(QUrl("group:1"))
    assert previews[-1] == "Ada, Bea"
    tab.summaryBrowser.anchorClicked.emit(QUrl("group:1"))
    drawer = tab.drawer.toPlainText()
    for label in ("Suggested Activity", "Possible Topic", "Social Temperature", "Player Entry"):
        assert label in drawer
    assert "Ada" in drawer and "Bea" in drawer

    tab.summaryBrowser.anchorClicked.emit(QUrl("group:2"))
    assert "Optional Approach" in tab.drawer.toPlainText()
    assert "npc:c" in tab.drawer.toHtml()

    before = deepcopy(event.dashboard)
    copied = build_table_prep_text(event, attendees, {})
    assert "Suggested Activity: Compare details in the club's architecture." in copied
    assert "Optional Approach: Ask whether the club has appeared on an old postcard." in copied
    assert "1. Ada, Bea" in copied and "2. Cy" in copied and "3. Late" in copied
    assert "1. Ada, Bea — Architecture — Matching tags: architecture." in copied
    assert "evidence" not in copied and "knowledge_evidence" not in copied
    assert event.dashboard == before
    QApplication.clipboard().clear()


def test_cue_projection_escapes_provider_html(qapp, qtbot):
    from PySide6.QtCore import QUrl

    from core.club_models import ClubEvent
    from ui.club_tab import ClubTab

    _documents, _evidence, _annotations, _own, fixed, _cues = _fixture()
    fixed[0]["conversation_cue"] = {
        "activity": "<script>bad()</script>", "topic": "A safe topic.",
        "temperature": "uncertain", "player_entry": "Ask a question.",
        "evidence": [], "knowledge_evidence": [],
    }
    scene = {"encounters": fixed, "incomplete": False}
    event = ClubEvent("event", ("a", "b", "c", "late"), "late", {"scene_prep": scene}, "cache", 1)
    tab = ClubTab(object())
    qtbot.addWidget(tab)
    tab._event = event
    tab._attendees = {npc_id: {"npc_id": npc_id, "name": npc_id.title()} for npc_id in event.attendee_ids}
    tab.summaryBrowser.anchorClicked.emit(QUrl("group:1"))
    assert "<script>" not in tab.drawer.toHtml().casefold()
    assert "bad()" in tab.drawer.toPlainText()


def test_production_service_keeps_tag_identity_and_debug_visibility_boundaries(tmp_path):
    import json

    from core.club_generation import ClubGenerationService
    from core.club_prep import npc_scene_context, prep_references
    from test_club_prep import SceneProvider
    from tools.debug_club_generation import _visible_dashboard_payload, _visible_event_payload

    paths = []
    for index, tag in enumerate(("architecture", "architecture", "architecture", "architecture", "postcards", "music")):
        path = tmp_path / f"Guest {index}.md"
        path.write_text(
            f"# Guest {index}\n\nGuest {index} publicly enjoys discussing the city.\n\n#{tag}\n",
            encoding="utf-8",
        )
        paths.append(str(path))
    provider = SceneProvider()
    service = ClubGenerationService({}, cache_root=tmp_path / "cache", vault_root=tmp_path, provider=provider)
    baseline = service.build_event_result(paths, seed=19, use_ai=False)
    result = service.build_event_result(paths, seed=19)
    before = baseline.event.dashboard["scene_prep"]["encounters"]
    after = result.event.dashboard["scene_prep"]["encounters"]

    assert [(row["number"], row["members"], row["label"], row["basis"], row["availability"]) for row in after] == [
        (row["number"], row["members"], row["label"], row["basis"], row["availability"]) for row in before
    ]
    assert all("conversation_cue" in row for row in after if row["availability"] == "present")
    assert "conversation_cue" not in after[-1]

    visible = _visible_dashboard_payload(result.event.dashboard)
    serialized_visible = json.dumps(visible)
    assert "conversation_cue" in serialized_visible
    assert "knowledge_evidence" not in serialized_visible
    assert "passage_id" not in serialized_visible
    assert "evidence" not in serialized_visible

    visible_event = _visible_event_payload(result.event.to_dict())
    assert "encounter_cues" not in visible_event["metadata"].get("prep_stages", {})
    assert "encounter_cues" in result.event.metadata["prep_stages"]

    references = prep_references(result.event.dashboard["scene_prep"])
    debug = result.to_debug_dict()
    assert references
    assert set(references) <= set(debug["prep_support"])
    assert "conversation_cue" not in json.dumps(npc_scene_context(result.event.dashboard["scene_prep"]))

    calls = len(provider.calls)
    cached = service.build_event_result(paths, seed=19)
    assert cached.event.dashboard["scene_prep"] == result.event.dashboard["scene_prep"]
    assert len(provider.calls) == calls
