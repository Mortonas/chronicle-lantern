from __future__ import annotations

import json
from copy import deepcopy

import pytest

from core.club_cache import ClubCacheService
from core.club_models import ClubEvent
from core.club_prep import (
    CLUB_PREP_RUMOR_GUIDANCE_SCHEMA_VERSION,
    PrepContext,
    PrepEngine,
    PrepError,
    RequestBudget,
    capture_document,
    rumor_guidance_display_rows,
    validate_rumor_guidance,
    visible_rumor_guidance,
)


def _context() -> PrepContext:
    ada = capture_document(
        "ada",
        "Ada",
        "# Ada\n\nAda publicly studies old city records and welcomes careful questions.\n\n"
        "Ada knows Bea keeps a private ledger.\n",
        "ada.md",
    )
    bea = capture_document(
        "bea",
        "Bea",
        "# Bea\n\nBea publicly supports the neighborhood archive.\n\n"
        "Bea keeps a private ledger beneath the archive.\n",
        "bea.md",
    )
    late = capture_document("late", "Late", "# Late\n\nLate arrives after the others.\n", "late.md")
    annotations = {}
    for document in (ada, bea, late):
        for passage in document.passages:
            text = passage.text.casefold()
            categories = ["social"]
            visibility = "public"
            if "knows bea" in text:
                categories = ["knowledge"]
                visibility = "unknown"
            elif "private ledger" in text:
                categories = ["backstory"]
                visibility = "private"
            annotations[passage.passage_id] = {
                "passage_id": passage.passage_id,
                "topics": [],
                "categories": categories,
                "visibility": visibility,
                "known_by": [],
            }
    readings = {
        document.npc_id: {
            "card": f"Compact reading for {document.name}.",
            "annotations": [annotations[p.passage_id] for p in document.passages],
            "complete": True,
            "completed_chunks": 1,
            "total_chunks": 1,
            "failures": [],
        }
        for document in (ada, bea, late)
    }
    return PrepContext((ada, bea, late), readings, "provider-fingerprint")


def _passage(context: PrepContext, needle: str):
    return next(p for d in context.documents for p in d.passages if needle.casefold() in p.text.casefold())


def _rumor(item_id: str = "rumor-1") -> dict[str, object]:
    return {
        "item_id": item_id,
        "type": "rumor",
        "summary": "A sealed ledger may have vanished from the old archive.",
        "source_npc_id": "ada",
        "target_npc_id": None,
        "mentioned_npc_ids": ["bea"],
        "characters": ["ada"],
        "sources": [{"source_id": "source-rumor-1", "path": "ada.md", "section": "Whispers"}],
    }


def _arrangement() -> list[dict[str, object]]:
    return [
        {"number": 1, "members": ["ada", "bea"], "cue": "A conversation is forming.", "reason": "",
         "availability": "present", "basis": "tag_similarity"},
        {"number": 2, "members": ["late"], "cue": "Expected later.", "reason": "",
         "availability": "expected", "basis": "late_arrival"},
    ]


def _npc_row(context: PrepContext, *, rumor_item_id: str = "rumor-1") -> dict[str, object]:
    support = _passage(context, "studies old city records")
    return {
        "rumor_item_id": rumor_item_id,
        "approach": {"kind": "npc", "npc_ids": ["ada"]},
        "why_productive": "Ada's interest in city records may make a careful question productive.",
        "natural_opening": "Possible opening: mention missing records while discussing the old archive.",
        "possible_gain": "The players might learn where to look next.",
        "social_risk": "Pushing too hard could make the question seem accusatory.",
        "evidence": [support.passage_id],
        "knowledge_evidence": [],
    }


def _group_row(context: PrepContext) -> dict[str, object]:
    public_a = _passage(context, "studies old city records")
    public_b = _passage(context, "supports the neighborhood archive")
    return {
        "rumor_item_id": "rumor-1",
        "approach": {"kind": "group", "number": 1, "npc_ids": ["ada", "bea"]},
        "why_productive": "Their separate interests in records and archives may offer a useful opening.",
        "natural_opening": "Possible opening: ask how old records are normally handled.",
        "possible_gain": "The players might identify a useful archival lead.",
        "social_risk": "The question could sound like an accusation if pressed.",
        "evidence": [public_a.passage_id, public_b.passage_id],
        "knowledge_evidence": [],
    }


def _evidence(context: PrepContext) -> dict[str, object]:
    return {p.passage_id: p for d in context.documents for p in d.passages}


def test_validator_accepts_suggestion_only_npc_and_exact_group_routes():
    context = _context()
    rumors = [_rumor()]
    assert validate_rumor_guidance(
        [_npc_row(context)], rumors, _arrangement(), context.documents, _evidence(context), context.annotations,
    )[0]["approach"] == {"kind": "npc", "npc_ids": ["ada"]}
    assert validate_rumor_guidance(
        [_group_row(context)], rumors, _arrangement(), context.documents, _evidence(context), context.annotations,
    )[0]["approach"] == {"kind": "group", "number": 1, "npc_ids": ["ada", "bea"]}
    question = _npc_row(context)
    question["natural_opening"] = "Ask Ada whether she has heard the story mentioned around the archive."
    assert validate_rumor_guidance(
        [question], rumors, _arrangement(), context.documents, _evidence(context), context.annotations,
    )
    framed = _npc_row(context)
    framed["natural_opening"] = "Ask whether the sealed ledger vanished from the old archive."
    assert validate_rumor_guidance(
        [framed], rumors, _arrangement(), context.documents, _evidence(context), context.annotations,
    )


@pytest.mark.parametrize("mutate", [
    lambda row: row.update(extra="no"),
    lambda row: row.update(rumor_item_id="unselected"),
    lambda row: row["approach"].update(npc_ids=["late"]),
    lambda row: row["approach"].update(kind="group", number=1),
    lambda row: row.update(evidence=["unknown"]),
    lambda row: row.update(why_productive="x" * 181),
    lambda row: row.update(natural_opening="Ada knows this rumor is true."),
    lambda row: row.update(natural_opening="The rumor is being spread by Ada."),
    lambda row: row.update(natural_opening="Ada is telling people about the missing ledger."),
    lambda row: row.update(natural_opening="Mention that the sealed ledger vanished from the old archive."),
    lambda row: row.update(natural_opening="People say Ada has been passing the story around."),
    lambda row: row.update(natural_opening="Ask Ada about the archive; she has heard people discussing the ledger."),
    lambda row: row.update(natural_opening="If the players ask, say that the sealed ledger vanished from the old archive."),
    lambda row: row.update(natural_opening="Ask Ada if the timing feels right, because she has heard the story."),
    lambda row: row.update(possible_gain="The rumor has been confirmed."),
    lambda row: row.update(social_risk="Late may resent the question."),
])
def test_validator_rejects_invalid_identity_evidence_or_claims(mutate):
    context = _context()
    row = _npc_row(context)
    mutate(row)
    with pytest.raises(PrepError):
        validate_rumor_guidance(
            [row], [_rumor()], _arrangement(), context.documents, _evidence(context), context.annotations,
        )


def test_validator_requires_selected_order_and_unique_rows():
    context = _context()
    rumors = [_rumor("rumor-1"), _rumor("rumor-2")]
    first = _npc_row(context, rumor_item_id="rumor-1")
    second = _npc_row(context, rumor_item_id="rumor-2")
    with pytest.raises(PrepError, match="rumor_guidance_identity"):
        validate_rumor_guidance(
            [second, first], rumors, _arrangement(), context.documents, _evidence(context), context.annotations,
        )
    with pytest.raises(PrepError, match="rumor_guidance_identity"):
        validate_rumor_guidance(
            [first, deepcopy(first)], rumors, _arrangement(), context.documents, _evidence(context), context.annotations,
        )


def test_group_private_support_requires_exact_actor_owned_knowledge_proof():
    context = _context()
    row = _group_row(context)
    private_b = _passage(context, "keeps a private ledger beneath")
    knowledge_a = _passage(context, "knows Bea keeps")
    row["evidence"] = [row["evidence"][0], private_b.passage_id]
    with pytest.raises(PrepError, match="private_target_knowledge"):
        validate_rumor_guidance(
            [row], [_rumor()], _arrangement(), context.documents, _evidence(context), context.annotations,
        )
    row["knowledge_evidence"] = [{
        "actor_npc_id": "ada",
        "passage_id": knowledge_a.passage_id,
        "subject_npc_id": "bea",
        "target_passage_id": private_b.passage_id,
    }]
    assert validate_rumor_guidance(
        [row], [_rumor()], _arrangement(), context.documents, _evidence(context), context.annotations,
    )


def test_validator_rejects_rumor_passage_as_route_support():
    context = _context()
    row = _npc_row(context)
    annotations = deepcopy(context.annotations)
    annotations[row["evidence"][0]]["categories"] = ["rumor"]
    row["why_productive"] = "The subject may provide a conversational opening."
    row["natural_opening"] = "Ask whether the report has more context."

    with pytest.raises(PrepError, match="rumor_source_as_guidance"):
        validate_rumor_guidance(
            [row], [_rumor()], _arrangement(), context.documents, _evidence(context), annotations,
        )


class GuidanceProvider:
    def __init__(self, context: PrepContext, outputs=None):
        self.context = context
        self.outputs = list(outputs or [])
        self.calls: list[dict[str, object]] = []

    def generate_from_messages(self, messages, **_kwargs):
        payload = json.loads(messages[-1]["content"].split("INPUT_JSON:\n", 1)[1])
        self.calls.append(payload)
        if self.outputs:
            value = self.outputs.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value if isinstance(value, str) else json.dumps(value)
        if "evidence" not in payload:
            return json.dumps({"candidates": ["ada"], "queries": ["city records archive"], "references": []})
        return json.dumps({"rumor_guidance": [_npc_row(self.context)]})


def test_rumor_evidence_payload_excludes_non_candidate_and_late_arrival_passages(tmp_path):
    context = _context()
    provider = GuidanceProvider(context, [
        {"candidates": ["ada"], "queries": ["arrives after the others"], "references": []},
        {"rumor_guidance": []},
    ])
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})

    rows, stage = engine.rumor_guidance(
        context, [_rumor()], _arrangement(), "late", RequestBudget(),
    )

    assert rows == []
    assert stage == {"status": "complete", "from_cache": False}
    assert {row["npc_id"] for row in provider.calls[-1]["evidence"]} <= {"ada"}


def test_rumor_proposal_rejects_passage_references_outside_its_candidates(tmp_path):
    context = _context()
    late_passage = _passage(context, "arrives after the others")
    engine = PrepEngine(ClubCacheService(tmp_path), GuidanceProvider(context), {})

    with pytest.raises(PrepError, match="unknown_reference"):
        engine._rumor_proposal(
            {
                "candidates": ["ada"],
                "queries": [],
                "references": [late_passage.passage_id],
            },
            context,
            {"ada", "bea"},
        )


def test_rumor_stage_never_sends_rumor_passages_as_approach_evidence(tmp_path):
    original = _context()
    readings = deepcopy(original.readings)
    support = _passage(original, "studies old city records")
    annotation = next(
        row for row in readings["ada"]["annotations"]
        if row["passage_id"] == support.passage_id
    )
    annotation["categories"] = ["social", "rumor"]
    context = PrepContext(original.documents, readings, original.fingerprint)
    provider = GuidanceProvider(context, [
        {"candidates": ["ada"], "queries": ["old city records"], "references": []},
        {"rumor_guidance": []},
    ])
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})

    with pytest.raises(PrepError, match="unknown_reference"):
        engine._rumor_proposal(
            {
                "candidates": ["ada"],
                "queries": [],
                "references": [support.passage_id],
            },
            context,
            {"ada", "bea"},
        )

    rows, stage = engine.rumor_guidance(
        context, [_rumor()], _arrangement(), "late", RequestBudget(),
    )

    assert rows == []
    assert stage == {"status": "complete", "from_cache": False}
    assert support.passage_id not in {
        row["passage_id"] for row in provider.calls[-1]["evidence"]
    }
    assert all(
        "rumor" not in row["annotation"]["categories"]
        for row in provider.calls[-1]["evidence"]
    )


def test_stage_payload_is_narrow_and_empty_success_is_cached(tmp_path):
    context = _context()
    provider = GuidanceProvider(context, [
        {"candidates": [], "queries": [], "references": []},
        {"rumor_guidance": []},
    ])
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {"name": "test"}, reasoning_identity="dashboard")
    rows, stage = engine.rumor_guidance(
        context, [_rumor()], _arrangement(), "late", RequestBudget(),
    )
    assert rows == [] and stage == {"status": "complete", "from_cache": False}
    assert set(provider.calls[0]) == {"rumors", "roster", "arrangement", "late_arrival_id"}
    payload_text = repr(provider.calls)
    for forbidden in ("conversation_cue", "scene_prep", "relationship", "tag", "gm_notes"):
        assert forbidden not in payload_text
    assert engine.rumor_guidance(
        context, [_rumor()], _arrangement(), "late", RequestBudget(),
    ) == ([], {"status": "complete", "from_cache": True})
    assert len(provider.calls) == 2


def test_stage_repairs_with_four_call_limit_and_never_caches_failure(tmp_path):
    context = _context()
    provider = GuidanceProvider(context, [
        {"bad": True},
        {"candidates": ["ada"], "queries": [], "references": []},
        {"rumor_guidance": "bad"},
        {"rumor_guidance": [_npc_row(context)]},
    ])
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {"name": "test"})
    rows, stage = engine.rumor_guidance(context, [_rumor()], _arrangement(), "late", RequestBudget())
    assert rows and stage["status"] == "complete" and len(provider.calls) == 4

    failed_root = tmp_path / "failed"
    failed = PrepEngine(
        ClubCacheService(failed_root), GuidanceProvider(context, ["bad", "still bad"]), {"name": "test"},
    )
    assert failed.rumor_guidance(context, [_rumor()], _arrangement(), "late", RequestBudget())[1]["status"] == "unavailable"
    assert not list((failed_root / "prep_rumor_guidance").glob("*.json"))


def test_force_failure_retains_exact_cache_and_version_is_isolated(tmp_path, monkeypatch):
    import core.club_prep as prep

    context = _context()
    provider = GuidanceProvider(context)
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {"name": "test"}, reasoning_identity="dashboard-v1")
    first, _stage = engine.rumor_guidance(context, [_rumor()], _arrangement(), "late", RequestBudget())
    provider.outputs[:] = [RuntimeError("offline"), RuntimeError("offline")]
    retained, stage = engine.rumor_guidance(
        context, [_rumor()], _arrangement(), "late", RequestBudget(), force=True,
    )
    assert retained == first
    assert stage == {"status": "complete", "from_cache": True, "regeneration_failed": True, "reason": "transport"}

    calls = len(provider.calls)
    monkeypatch.setattr(prep, "CLUB_PREP_RUMOR_GUIDANCE_SCHEMA_VERSION", "club_prep_rumor_guidance_v2")
    provider.outputs.clear()
    engine.rumor_guidance(context, [_rumor()], _arrangement(), "late", RequestBudget())
    assert len(provider.calls) == calls + 2


def test_no_selected_rumors_is_complete_without_provider_or_cache(tmp_path):
    context = _context()
    provider = GuidanceProvider(context)
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {"name": "test"})
    assert engine.rumor_guidance(context, [], _arrangement(), "late", RequestBudget()) == (
        [], {"status": "not_applicable", "from_cache": False},
    )
    assert provider.calls == []
    assert not (tmp_path / "prep_rumor_guidance").exists()


def test_cache_identity_tracks_only_guidance_inputs_not_shared_reasoning_identity(tmp_path):
    context = _context()
    provider = GuidanceProvider(context)
    cache = ClubCacheService(tmp_path)
    first = PrepEngine(cache, provider, {"name": "test"}, reasoning_identity="dashboard-v1")
    first.rumor_guidance(context, [_rumor()], _arrangement(), "late", RequestBudget())
    count = len(provider.calls)

    second = PrepEngine(cache, provider, {"name": "test"}, reasoning_identity="dashboard-v2")
    assert second.rumor_guidance(
        context, [_rumor()], _arrangement(), "late", RequestBudget(),
    )[1]["from_cache"] is True
    assert len(provider.calls) == count

    changed_rumor = _rumor()
    changed_rumor["summary"] = "A different selected canonical rumor."
    second.rumor_guidance(context, [changed_rumor], _arrangement(), "late", RequestBudget())
    assert len(provider.calls) == count + 2

    changed_arrangement = deepcopy(_arrangement())
    changed_arrangement[0]["number"] = 7
    second.rumor_guidance(context, [_rumor()], changed_arrangement, "late", RequestBudget())
    assert len(provider.calls) == count + 4


def test_malformed_current_guidance_cache_rebuilds(tmp_path):
    context = _context()
    provider = GuidanceProvider(context)
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {"name": "test"})
    engine.rumor_guidance(context, [_rumor()], _arrangement(), "late", RequestBudget())
    cache_file = next((tmp_path / "prep_rumor_guidance").glob("*.json"))
    cache_file.write_text('{"bad":true}', encoding="utf-8")
    count = len(provider.calls)
    assert engine.rumor_guidance(
        context, [_rumor()], _arrangement(), "late", RequestBudget(),
    )[1]["from_cache"] is False
    assert len(provider.calls) == count + 2


def test_visible_and_display_projection_are_suggestion_only_and_strip_support():
    context = _context()
    row = _npc_row(context)
    names = {"ada": "Ada", "bea": "Bea", "late": "Late"}
    display = rumor_guidance_display_rows([row], names)
    assert display == [{
        "rumor_item_id": "rumor-1",
        "lines": [
            "Possible approach: ask Ada.",
            "Why it may work: Ada's interest in city records may make a careful question productive.",
            "Bring it up: Possible opening: mention missing records while discussing the old archive.",
            "Possible gain: The players might learn where to look next.",
            "Social risk: Pushing too hard could make the question seem accusatory.",
        ],
    }]
    visible = visible_rumor_guidance([row], names)
    assert visible == display
    assert "evidence" not in json.dumps(visible)
    assert "knowledge_evidence" not in json.dumps(visible)
    assert CLUB_PREP_RUMOR_GUIDANCE_SCHEMA_VERSION == "club_prep_rumor_guidance_v1"


def test_retry_state_counts_guidance_unavailable_but_not_empty_complete():
    from ui.club_tab import _event_prep_incomplete

    dashboard = {"scene_prep": {"incomplete": False}, "rumors": [_rumor()], "rumor_guidance": []}
    unavailable = ClubEvent(
        "event", ("ada",), "", dashboard, "cache", 1,
        {"prep_stages": {"rumor_guidance": {"status": "unavailable"}}},
    )
    complete = ClubEvent(
        "event", ("ada",), "", dashboard, "cache", 1,
        {"prep_stages": {"rumor_guidance": {"status": "complete"}}},
    )
    no_rumors = ClubEvent("event", ("ada",), "", {**dashboard, "rumors": []}, "cache", 1)
    assert _event_prep_incomplete(unavailable)
    assert not _event_prep_incomplete(complete)
    assert not _event_prep_incomplete(no_rumors)


def test_dashboard_ui_copy_normal_json_and_debug_share_guidance_projection(qapp, qtbot):
    from core.club_generation import ClubBuildResult
    from tools.debug_club_generation import _dashboard_text, _visible_dashboard_payload
    from ui.club_tab import ClubTab, build_table_prep_text

    context = _context()
    row = _npc_row(context)
    canonical = _rumor()
    dashboard = {
        "event": {"venue": "Club", "event_type": "social gathering", "mood": "tense"},
        "scene_prep": {"encounters": _arrangement(), "incomplete": False},
        "rumors": [canonical],
        "rumors_in_circulation": [canonical["summary"]],
        "rumor_guidance": [row],
    }
    event = ClubEvent("event", ("ada", "bea", "late"), "late", dashboard, "cache", 1)
    attendees = [{"npc_id": d.npc_id, "name": d.name} for d in context.documents]
    tab = ClubTab(object())
    qtbot.addWidget(tab)
    tab._event = event
    tab._attendees = {item["npc_id"]: item for item in attendees}
    rendered = tab._dashboard_html(dashboard, "late")
    tab.summaryBrowser.setHtml(rendered)
    rendered_text = tab.summaryBrowser.toPlainText()
    copied = build_table_prep_text(event, attendees, {})
    readable = _dashboard_text(dashboard, attendees, "late")
    normal = _visible_dashboard_payload(dashboard, {d.npc_id: d.name for d in context.documents})

    for output in (rendered_text, copied, readable):
        assert canonical["summary"] in output
        assert "Possible approach: ask Ada." in output
    assert rendered_text.index(canonical["summary"]) < rendered_text.index("Possible approach: ask Ada.")
    assert copied.index(canonical["summary"]) < copied.index("Possible approach: ask Ada.")
    assert normal["rumors_in_circulation"] == [canonical["summary"]]
    assert normal["rumor_guidance"] == rumor_guidance_display_rows([row], {d.npc_id: d.name for d in context.documents})
    assert "evidence" not in json.dumps(normal)

    build = ClubBuildResult(event, (), {}, (), (), {}, {}, context)
    debug = build.to_debug_dict()
    assert row["evidence"][0] in debug["prep_support"]
    assert debug["event"]["dashboard"]["rumors"] == [canonical]


def test_service_joins_guidance_without_changing_selected_rumors_or_scene_revision(tmp_path, monkeypatch):
    from core.club_generation import ClubGenerationService
    from core.club_prep import npc_scene_context
    from test_club_prep import SceneProvider

    class ServiceGuidanceProvider(SceneProvider):
        def generate_from_messages(self, messages, **kwargs):
            if "INPUT_JSON:\n" in messages[-1]["content"]:
                payload = json.loads(messages[-1]["content"].split("INPUT_JSON:\n", 1)[1])
                if "rumors" in payload:
                    self.calls.append(payload)
                    if "evidence" not in payload:
                        present = [
                            npc_id
                            for row in payload["arrangement"]
                            if row["availability"] == "present"
                            for npc_id in row["members"]
                        ]
                        return json.dumps({"candidates": present[:1], "queries": ["public interests"], "references": []})
                    target = payload["proposal"]["candidates"][0]
                    ref = next(
                        row["passage_id"] for row in payload["evidence"]
                        if row["npc_id"] == target and row["text"].strip() and not row["text"].startswith("#")
                    )
                    return json.dumps({"rumor_guidance": [{
                        "rumor_item_id": payload["rumors"][0]["item_id"],
                        "approach": {"kind": "npc", "npc_ids": [target]},
                        "why_productive": "An interest in local institutions may make a careful question productive.",
                        "natural_opening": "Mention the subject while discussing recent changes around the city.",
                        "possible_gain": "The players might identify a useful next question.",
                        "social_risk": "Pressing too hard could make the approach seem accusatory.",
                        "evidence": [ref],
                        "knowledge_evidence": [],
                    }]})
            return super().generate_from_messages(messages, **kwargs)

    paths = []
    for name in ("Ada", "Bea", "Cy"):
        path = tmp_path / f"{name}.md"
        path.write_text(
            f"# {name}\n\n{name} publicly supports the neighborhood archive.\n\n"
            f"## Whispers\n- {name} may hide a valuable ledger.\n",
            encoding="utf-8",
        )
        paths.append(str(path))
    provider = ServiceGuidanceProvider()
    service = ClubGenerationService({}, cache_root=tmp_path / "cache", vault_root=tmp_path, provider=provider)
    context_reserves = []
    original_context = PrepEngine.context

    def capture_context(self, documents, budget, *, downstream_reserve=4):
        context_reserves.append(downstream_reserve)
        return original_context(
            self, documents, budget, downstream_reserve=downstream_reserve,
        )

    monkeypatch.setattr(PrepEngine, "context", capture_context)
    baseline = service._build_canonical_event_result(paths, seed=73, use_ai=False)
    before = {
        key: deepcopy(baseline.event.dashboard[key])
        for key in ("rumors", "rumors_in_circulation", "rumor_selection")
    }
    result = service.build_event_result(paths, seed=73)

    assert {key: result.event.dashboard[key] for key in before} == before
    assert all(row["target_npc_id"] is None for row in result.event.dashboard["rumors"])
    assert result.event.dashboard["rumor_guidance"]
    assert context_reserves == [8]
    assert result.event.metadata["prep_stages"]["rumor_guidance"] == {
        "status": "complete", "from_cache": False,
    }
    assert result.event.metadata["prep_revision"] == result.event.dashboard["scene_prep"]["revision"]
    assert "rumor_guidance" not in repr(npc_scene_context(result.event.dashboard["scene_prep"]))
    assert (tmp_path / "cache" / "events" / f"{result.event.cache_key}.json").exists()


def test_unavailable_guidance_keeps_event_retryable_and_out_of_success_cache(tmp_path):
    from core.club_generation import ClubGenerationService
    from test_club_prep import SceneProvider
    from ui.club_tab import _event_prep_incomplete

    class InvalidGuidanceProvider(SceneProvider):
        def generate_from_messages(self, messages, **kwargs):
            if "INPUT_JSON:\n" in messages[-1]["content"]:
                payload = json.loads(messages[-1]["content"].split("INPUT_JSON:\n", 1)[1])
                if "rumors" in payload and "evidence" in payload:
                    self.calls.append(payload)
                    return json.dumps({"wrong": True})
            return super().generate_from_messages(messages, **kwargs)

    paths = []
    for name in ("Ada", "Bea", "Cy"):
        path = tmp_path / f"{name}.md"
        path.write_text(
            f"# {name}\n\n{name} publicly supports the archive.\n\n"
            f"## Whispers\n- {name} may hide a ledger.\n",
            encoding="utf-8",
        )
        paths.append(str(path))
    cache_root = tmp_path / "cache"
    service = ClubGenerationService({}, cache_root=cache_root, vault_root=tmp_path, provider=InvalidGuidanceProvider())
    result = service.build_event_result(paths, seed=74)

    assert not result.event.dashboard["scene_prep"]["incomplete"]
    assert result.event.dashboard["rumor_guidance"] == []
    assert result.event.metadata["prep_stages"]["rumor_guidance"]["status"] == "unavailable"
    assert result.event.metadata["generation_mode"] == "partial_ai"
    assert _event_prep_incomplete(result.event)
    assert not (cache_root / "events" / f"{result.event.cache_key}.json").exists()


def test_rumor_browser_does_not_render_selected_guidance(qapp, qtbot):
    from ui.club_tab import ClubTab

    context = _context()
    tab = ClubTab(object())
    qtbot.addWidget(tab)
    tab._attendees = {d.npc_id: {"npc_id": d.npc_id, "name": d.name} for d in context.documents}
    browser = tab._rumor_pool_html({"rumor_pool": [_rumor()], "rumor_guidance": [_npc_row(context)]})
    assert _rumor()["summary"] in browser
    assert "Possible approach" not in browser
