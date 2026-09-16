from __future__ import annotations

import json
from copy import deepcopy

import pytest

from core.club_cache import ClubCacheService
from core.club_prep import PrepEngine, PrepError, RequestBudget, capture_document
from test_club_prep import SceneProvider, documents
from test_club_prep_ui import prepared_event


def test_npc_requests_exclude_all_gm_authored_prose(tmp_path):
    provider = SceneProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    context = engine.context(documents(), RequestBudget())
    scene = engine.scene(context, "c", {}, {}, RequestBudget())
    scene["opening"]["text"] = "GM_PRIVATE_OPENING"
    scene["gm_notes"] = [{"text": "GM_PRIVATE_NOTE", "classification": "established"}]
    scene["power_players"][0]["text"] = "GM_PRIVATE_POWER"
    scene["encounters"][0].update(cue="GM_PRIVATE_CUE", reason="GM_PRIVATE_REASON")
    original = deepcopy(scene)
    start = len(provider.calls)
    rows, stage = engine.relevance(context, "a", scene, [])
    assert rows and stage["status"] == "complete"
    assert scene == original
    for request in provider.calls[start:]:
        assert "GM_PRIVATE" not in json.dumps(request)
        assert request["arrangement"]["late_arrival_id"] == "c"
        assert request["arrangement"]["encounters"][-1]["members"] == ["c"]
        assert all(r["card_use"] == ("own_context" if r["npc_id"] == "a" else "research_only") for r in request["roster"])


@pytest.mark.parametrize("reverse", [False, True])
def test_long_other_sheet_cannot_crowd_out_own_backstory(tmp_path, reverse):
    docs = [capture_document("a", "A", "x" * 800 * 100),
            capture_document("z", "Z", "# Z\n\nAppearance: blue coat.\n\n# Backstory\nRescued injured foxes.")]
    provider = SceneProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    context = engine.context(tuple(reversed(docs)) if reverse else docs, RequestBudget())
    for a in context.readings["z"]["annotations"]:
        a["categories"] = ["backstory"] if a["passage_id"] == docs[1].passages[-1].passage_id else ["other"]
    payload, evidence = engine._evidence_payload(context, {"candidates": ["z", "a"], "queries": [], "references": []},
                                                {"roster": engine._roster(context), "npc_id": "z", "arrangement": {}}, "npc_final")
    assert docs[1].passages[-1].passage_id in evidence
    assert len(engine._prompt("npc_final", payload)) // 4 <= 8500


def test_explicit_unread_passage_is_not_silently_skipped(tmp_path):
    engine = PrepEngine(ClubCacheService(tmp_path), SceneProvider(), {})
    context = engine.context(documents(), RequestBudget())
    ref = context.readings["b"]["annotations"].pop()["passage_id"]
    with pytest.raises(PrepError, match="required_evidence_unavailable"):
        engine._evidence_payload(context, {"candidates": [], "queries": [], "references": [ref]}, {}, "scene_final")


def test_validation_and_cache_retries_receive_accepted_constraints(tmp_path):
    class Partial(SceneProvider):
        rejected = False
        def generate_from_messages(self, messages, **kwargs):
            value = json.loads(super().generate_from_messages(messages, **kwargs))
            if "encounters" in value and not self.rejected:
                self.rejected = True
                value["encounters"] = "invalid"
            return json.dumps(value)
    provider = Partial()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    context = engine.context(documents(), RequestBudget())
    first = engine.scene(context, "c", {}, {}, RequestBudget())
    assert provider.calls[-1]["sections"] == ["encounters"]
    assert provider.calls[-1]["accepted_sections"]["opening"] == first["opening"]
    for p in (tmp_path / "prep_sections").glob("*_encounters.json"):
        p.write_text("{}", encoding="utf-8")
    start = len(provider.calls)
    second = engine.scene(context, "c", {}, {}, RequestBudget())
    assert second["opening"] == first["opening"]
    for request in provider.calls[start:]:
        assert request["accepted_sections"]["opening"] == first["opening"]


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("cached", [False, True])
def test_authentication_stops_npc_operation_and_allows_cached_relevance(tmp_path, status, cached):
    service, _, result = prepared_event(tmp_path)
    npc = result.event.attendee_ids[0]
    prior = service.build_npc_panel_from_result(result, npc) if cached else None
    class Unauthorized:
        calls = 0
        def generate_from_messages(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError(f"Unauthorized {status}")
    provider = Unauthorized()
    service.provider = provider
    panel = service.build_npc_panel_from_result(result, npc, force=cached)
    assert provider.calls == 1
    assert panel["metadata"]["ai_error_type"] == "provider_authentication_failed"
    stage = panel["metadata"]["who_matters_stage"]
    conversation_stage = panel["metadata"]["conversation_openings_stage"]
    if cached:
        assert panel["who_matters"] == prior["who_matters"]
        assert stage["from_cache"] and stage["regeneration_failed"]
        assert panel["conversation_openings"] == prior["conversation_openings"]
        assert conversation_stage["from_cache"] and conversation_stage["regeneration_failed"]
    else:
        assert stage["status"] == "unavailable" and stage["reason"] == "authentication"
        assert conversation_stage["status"] == "unavailable" and conversation_stage["reason"] == "authentication"


@pytest.mark.parametrize("message", ["timed out after 1401 milliseconds", "timed out after 401 milliseconds", "could not allocate 403 output tokens"])
def test_incidental_status_digits_do_not_stop_readings_as_authentication(tmp_path, message):
    class Failure:
        calls = 0
        def generate_from_messages(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError(message)
    provider = Failure()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    engine.context(documents(), RequestBudget())
    assert provider.calls == 3


def test_one_person_relevance_needs_no_inference_request(tmp_path):
    provider = SceneProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    context = engine.context(documents()[:1], RequestBudget())
    start = len(provider.calls)
    rows, stage = engine.relevance(context, "a", {}, [])
    assert rows == [] and stage["status"] == "complete"
    assert len(provider.calls) == start


def test_partial_arrangement_is_pending_not_an_immutable_section(tmp_path):
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
    provider.omit = False
    start = len(provider.calls)
    second = engine.scene(context, "c", {}, {}, RequestBudget())
    assert not second["incomplete"]
    for request in provider.calls[start:]:
        assert "encounters" not in request["accepted_sections"]
        assert request["accepted_sections"]["opening"] == first["opening"]


def test_missing_own_reading_stops_before_finalization(tmp_path):
    provider = SceneProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    context = engine.context(documents(), RequestBudget())
    context.readings["a"].update(annotations=[], complete=False, completed_chunks=0)
    count = len(provider.calls)
    rows, stage = engine.relevance(context, "a", {}, [])
    assert not rows and stage == {"status": "unavailable", "reason": "required_evidence_unavailable"}
    assert len(provider.calls) == count + 1  # proposal only
    assert not list((tmp_path / "prep_relevance").glob("*.json"))


def test_required_evidence_overflow_does_not_send_a_truncated_final(tmp_path):
    provider = SceneProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    docs = [capture_document("a", "Ada", "x" * 800 * 50), documents()[1]]
    context = engine.context(docs, RequestBudget())
    count = len(provider.calls)
    refs = [p.passage_id for p in docs[0].passages]
    with pytest.raises(PrepError, match="required_evidence_budget"):
        engine._evidence_payload(context, {"candidates": ["a", "b"], "queries": [], "references": refs},
                                 {"roster": engine._roster(context), "npc_id": "a", "arrangement": {}}, "npc_final")
    assert len(provider.calls) == count


def test_reasoning_revision_invalidates_sections_not_readings(tmp_path, monkeypatch):
    import core.club_prep as prep
    provider = SceneProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    context = engine.context(documents(), RequestBudget())
    current = prep.CLUB_PREP_REASONING_VERSION
    monkeypatch.setattr(prep, "CLUB_PREP_REASONING_VERSION", "club_prep_reasoning_v1")
    old = engine.scene(context, "c", {}, {}, RequestBudget())
    engine.relevance(context, "a", old, [])
    count = len(provider.calls)
    monkeypatch.setattr(prep, "CLUB_PREP_REASONING_VERSION", current)
    assert engine.context(documents(), RequestBudget()).digest == context.digest
    assert len(provider.calls) == count
    new = engine.scene(context, "c", {}, {}, RequestBudget())
    engine.relevance(context, "a", new, [])
    assert new["revision"] != old["revision"]
    assert len(provider.calls) == count + 4


@pytest.mark.parametrize("kind", ["status", "response", "typed", "missing"])
def test_shared_authentication_classifier_handles_chains(kind):
    from core.club_generation import _ai_failure_details
    from core.club_prep import authentication_failure
    class AuthenticationError(Exception):
        pass
    inner = AuthenticationError("rejected") if kind == "typed" else RuntimeError("Missing credentials" if kind == "missing" else "rejected")
    if kind == "status":
        inner.status_code = 401
    if kind == "response":
        from types import SimpleNamespace
        inner.response = SimpleNamespace(status_code=403)
    outer = RuntimeError("provider failed")
    outer.__cause__ = inner
    assert authentication_failure(outer) == ("missing_credentials" if kind == "missing" else "authentication")
    assert _ai_failure_details(outer)["ai_error_type"] == ("provider_missing_credentials" if kind == "missing" else "provider_authentication_failed")


def test_large_roster_retry_preserves_constraints_and_handles_overflow(tmp_path, monkeypatch):
    import core.club_prep as prep
    provider = SceneProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    docs = [capture_document(str(n), f"Guest {n}", f"Guest {n} publicly funds a shelter.") for n in range(50)]
    context = engine.context(docs, RequestBudget())
    first = engine.scene(context, "49", {}, {}, RequestBudget())
    assert not first["incomplete"]
    for p in (tmp_path / "prep_sections").glob("*_gm_notes.json"):
        p.write_text("{}", encoding="utf-8")
    count = len(provider.calls)
    second = engine.scene(context, "49", {}, {}, RequestBudget())
    assert not second["incomplete"] and len(provider.calls) == count + 2
    assert provider.calls[-1]["sections"] == ["gm_notes"]
    assert len(provider.calls[-1]["accepted_sections"]["encounters"]) == 49
    assert second["encounters"] == first["encounters"]
    for p in (tmp_path / "prep_sections").glob("*_gm_notes.json"):
        p.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(prep, "PREP_TOKEN_BUDGET", 700)
    count = len(provider.calls)
    partial = engine.scene(context, "49", {}, {}, RequestBudget())
    assert partial["incomplete"] and len(provider.calls) == count
    assert partial["encounters"] == second["encounters"]
    assert partial["opening"] == second["opening"]


def test_bad_replacement_cannot_poison_valid_cached_section(tmp_path):
    class Invalid(SceneProvider):
        bad = False
        def generate_from_messages(self, messages, **kwargs):
            value = json.loads(super().generate_from_messages(messages, **kwargs))
            if self.bad and "opening" in value:
                value["opening"]["characters"] = ["c"]  # late arrival
            return json.dumps(value)
    provider = Invalid()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    context = engine.context(documents(), RequestBudget())
    first = engine.scene(context, "c", {}, {}, RequestBudget())
    path = next((tmp_path / "prep_sections").glob("*_opening.json"))
    cached_bytes = path.read_bytes()
    provider.bad = True
    second = engine.scene(context, "c", {}, {}, RequestBudget(), force=True)
    assert path.read_bytes() == cached_bytes
    assert second["opening"] == first["opening"]
    assert second["stages"]["opening"]["regeneration_failed"]
