from __future__ import annotations

import json
from dataclasses import replace

import pytest

from core.club_cache import ClubCacheService
from core.club_prep import (
    CLUB_PREP_ENCOUNTER_CUE_SCHEMA_VERSION,
    CLUB_PREP_NPC_CONVERSATION_SCHEMA_VERSION,
    CLUB_PREP_REASONING_VERSION,
    PrepContext,
    PrepEngine,
    PrepError,
    capture_document,
    conversation_opening_display_rows,
    validate_conversation_openings,
)


def _context() -> PrepContext:
    clicked = capture_document(
        "ada",
        "Ada",
        "# Ada\n\nAda publicly restores old radios and welcomes questions about them.\n\n"
        "## Responsibilities\nAda is trying to keep the neighborhood shelter funded.\n\n"
        "## Guarded\nAda privately regrets abandoning Bea during the fire.\n",
        "ada.md",
    )
    target = capture_document("bea", "Bea", "# Bea\n\nBea privately fears the Director.\n", "bea.md")
    annotations = []
    for passage in clicked.passages:
        text = passage.text.casefold()
        categories = ["social"]
        visibility = "public"
        if "shelter" in text:
            categories = ["motives"]
            visibility = "unknown"
        if "regrets" in text:
            categories = ["backstory"]
            visibility = "private"
        annotations.append({
            "passage_id": passage.passage_id,
            "topics": [],
            "categories": categories,
            "visibility": visibility,
            "known_by": [],
        })
    target_annotations = [{
        "passage_id": passage.passage_id,
        "topics": [],
        "categories": ["backstory"],
        "visibility": "private",
        "known_by": [],
    } for passage in target.passages]
    readings = {
        "ada": {"card": "Radio restorer and shelter supporter.", "annotations": annotations,
                "complete": True, "completed_chunks": 1, "total_chunks": 1, "failures": []},
        "bea": {"card": "A guarded attendee.", "annotations": target_annotations,
                "complete": True, "completed_chunks": 1, "total_chunks": 1, "failures": []},
    }
    return PrepContext((clicked, target), readings, "provider-fingerprint")


def _passage(context: PrepContext, needle: str):
    return next(p for d in context.documents for p in d.passages if needle.casefold() in p.text.casefold())


def _lane(lane: str, evidence: list[str], *, topic: str | None = None) -> dict[str, object]:
    return {
        "lane": lane,
        "topic": topic or {"easy": "Restoring old radios", "meaningful": "Keeping the shelter funded", "dangerous": "The unresolved fire"}[lane],
        "response": "Ada might answer cautiously and let the players set the pace.",
        "possible_gain": "The players might earn a more candid answer.",
        "possible_risk": "Pushing could make the NPC close the subject.",
        "evidence": evidence,
    }


def test_conversation_validator_orders_lanes_and_evidence_by_clicked_source():
    context = _context()
    easy = _passage(context, "radios")
    meaningful = _passage(context, "shelter")
    dangerous = _passage(context, "regrets")
    evidence = {p.passage_id: p for d in context.documents for p in d.passages}
    rows = validate_conversation_openings(
        [_lane("dangerous", [dangerous.passage_id, meaningful.passage_id]),
         _lane("easy", [easy.passage_id]),
         _lane("meaningful", [meaningful.passage_id])],
        "ada", context.documents, evidence, context.annotations,
    )
    assert [row["lane"] for row in rows] == ["easy", "meaningful", "dangerous"]
    assert rows[-1]["evidence"] == [meaningful.passage_id, dangerous.passage_id]
    assert rows[0]["evidence"] == [easy.passage_id]


def test_conversation_validator_accepts_sparse_empty_private_danger_and_support_reuse():
    context = _context()
    mixed = _passage(context, "regrets")
    evidence = {p.passage_id: p for d in context.documents for p in d.passages}
    assert validate_conversation_openings([], "ada", context.documents, evidence, context.annotations) == []
    rows = validate_conversation_openings(
        [_lane("meaningful", [mixed.passage_id], topic="Ada's past choices"),
         _lane("dangerous", [mixed.passage_id], topic="The abandoned fire response")],
        "ada", context.documents, evidence, context.annotations,
    )
    assert len(rows) == 2


@pytest.mark.parametrize("mutate", [
    lambda row, context: row.update(extra="no"),
    lambda row, context: row.update(lane="mystery"),
    lambda row, context: row.update(topic="x" * 121),
    lambda row, context: row.update(response="x" * 181),
    lambda row, context: row.update(evidence=[_passage(context, "Bea privately").passage_id]),
    lambda row, context: row.update(response="Bea might approve."),
    lambda row, context: row.update(response="She might approve."),
    lambda row, context: row.update(response="It might answer cautiously."),
    lambda row, context: row.update(response="Ada might guard its answer."),
    lambda row, context: row.update(response='Player: "Tell me about the radio."'),
    lambda row, context: row.update(response="Ada might answer cautiously; Player: ask about the radio."),
    lambda row, context: row.update(topic='Players could ask, "Tell me about the radio."'),
    lambda row, context: row.update(response="Ada answers immediately."),
    lambda row, context: row.update(possible_gain="The players will secure cooperation."),
    lambda row, context: row.update(possible_gain="The players will uncover the truth."),
    lambda row, context: row.update(evidence=["unknown-passage"]),
    lambda row, context: row.update(evidence=[context.documents[0].passages[0].passage_id]),
    lambda row, context: row.update(evidence=[row["evidence"][0], row["evidence"][0]]),
])
def test_conversation_validator_rejects_invalid_or_ungrounded_rows(mutate):
    context = _context()
    easy = _passage(context, "radios")
    row = _lane("easy", [easy.passage_id])
    mutate(row, context)
    evidence = {p.passage_id: p for d in context.documents for p in d.passages}
    with pytest.raises(PrepError):
        validate_conversation_openings([row], "ada", context.documents, evidence, context.annotations)


def test_conversation_validator_rejects_nonpublic_easy_duplicate_lane_topic_and_presentation():
    context = _context()
    public = _passage(context, "radios")
    private = _passage(context, "regrets")
    evidence = {p.passage_id: p for d in context.documents for p in d.passages}
    invalid_values = [
        [_lane("easy", [private.passage_id])],
        [_lane("easy", [public.passage_id]), _lane("easy", [public.passage_id], topic="Radio repair")],
        [_lane("easy", [public.passage_id]), _lane("meaningful", [public.passage_id], topic="Restoring old radios")],
        [_lane("easy", [public.passage_id]), {**_lane("easy", [public.passage_id]), "lane": "meaningful"}],
    ]
    for value in invalid_values:
        with pytest.raises(PrepError):
            validate_conversation_openings(value, "ada", context.documents, evidence, context.annotations)


def test_conversation_validator_does_not_treat_testament_will_as_guaranteed_outcome():
    context = _context()
    public = _passage(context, "radios")
    evidence = {p.passage_id: p for d in context.documents for p in d.passages}
    row = _lane("easy", [public.passage_id], topic="Ada's last will")
    row["response"] = "Ada might discuss the will cautiously and let the players set the pace."

    assert validate_conversation_openings(
        [row], "ada", context.documents, evidence, context.annotations,
    )[0]["topic"] == "Ada's last will"


def test_conversation_validator_allows_object_pronoun_without_character_pronoun():
    context = _context()
    public = _passage(context, "radios")
    evidence = {p.passage_id: p for d in context.documents for p in d.passages}
    row = _lane("easy", [public.passage_id])
    row["response"] = "Ada might repair it if the players show a grounded interest."

    assert validate_conversation_openings(
        [row], "ada", context.documents, evidence, context.annotations,
    )[0]["response"] == row["response"]


class ConversationProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def generate_from_messages(self, messages, **_kwargs):
        payload = json.loads(messages[-1]["content"].split("INPUT_JSON:\n", 1)[1])
        self.calls.append(payload)
        value = self.outputs.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value if isinstance(value, str) else json.dumps(value)


def _final(context: PrepContext, *lanes: str):
    refs = {
        "easy": _passage(context, "radios").passage_id,
        "meaningful": _passage(context, "shelter").passage_id,
        "dangerous": _passage(context, "regrets").passage_id,
    }
    return {"conversation_openings": [_lane(lane, [refs[lane]]) for lane in lanes]}


def test_conversation_stage_uses_clicked_only_payload_and_caches_empty_success(tmp_path):
    context = _context()
    provider = ConversationProvider([
        {"queries": ["radios shelter fire"], "references": []},
        {"conversation_openings": []},
    ])
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {"name": "test"})
    rows, stage = engine.conversation_openings(context, "ada")
    assert rows == []
    assert stage == {"status": "complete", "from_cache": False}
    proposal = provider.calls[0]
    assert set(proposal) == {"npc_id", "name", "card", "reading_coverage"}
    assert "roster" not in proposal and "arrangement" not in proposal and "portrayal_basis" not in proposal
    assert {row["npc_id"] for row in provider.calls[1]["evidence"]} == {"ada"}
    assert engine.conversation_openings(context, "ada") == ([], {"status": "complete", "from_cache": True})
    assert len(provider.calls) == 2


def test_conversation_stage_repairs_each_phase_with_four_call_limit_and_no_failure_cache(tmp_path):
    context = _context()
    provider = ConversationProvider([
        {"bad": True},
        {"queries": [], "references": []},
        {"conversation_openings": "bad"},
        _final(context, "easy"),
    ])
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {"name": "test"})
    rows, stage = engine.conversation_openings(context, "ada")
    assert [row["lane"] for row in rows] == ["easy"]
    assert stage["status"] == "complete"
    assert len(provider.calls) == 4

    failed = ConversationProvider(["not json", "still not json"])
    failed_engine = PrepEngine(ClubCacheService(tmp_path / "failed"), failed, {"name": "test"})
    assert failed_engine.conversation_openings(context, "ada")[1]["status"] == "unavailable"
    assert not list((tmp_path / "failed" / "prep_npc_conversation").glob("*.json"))


def test_conversation_force_failure_retains_cache_and_clicked_revision_invalidates(tmp_path):
    context = _context()
    provider = ConversationProvider([
        {"queries": [], "references": []}, _final(context, "meaningful"),
        RuntimeError("offline"),
    ])
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {"name": "test"})
    cached_rows, _ = engine.conversation_openings(context, "ada")
    rows, stage = engine.conversation_openings(context, "ada", force=True)
    assert rows == cached_rows
    assert stage == {"status": "complete", "from_cache": True, "regeneration_failed": True, "reason": "transport"}

    unrelated = _context()
    unrelated = PrepContext((unrelated.documents[0], capture_document("bea", "Bea", "changed", "bea.md")),
                            unrelated.readings, unrelated.fingerprint)
    assert engine.conversation_openings(unrelated, "ada")[1]["from_cache"] is True


def test_conversation_formatter_has_visible_debug_empty_and_unavailable_states():
    context = _context()
    row = _lane("easy", [_passage(context, "radios").passage_id])
    visible = conversation_opening_display_rows([row], {"status": "complete"})
    assert visible == [
        "Easy — Topic: Restoring old radios Possible response: Ada might answer cautiously and let the players set the pace. "
        "Possible gain: The players might earn a more candid answer. Possible risk: Pushing could make the NPC close the subject."
    ]
    assert "Why? " + row["evidence"][0] in conversation_opening_display_rows([row], {"status": "complete"}, include_evidence=True)[0]
    assert conversation_opening_display_rows([], {"status": "complete"}) == ["No supported conversation openings surfaced."]
    assert "unavailable" in conversation_opening_display_rows([], {"status": "unavailable"})[0].casefold()
    assert CLUB_PREP_NPC_CONVERSATION_SCHEMA_VERSION == "club_prep_npc_conversation_v1"


def test_malformed_conversation_cache_rebuilds_and_clicked_name_invalidates(tmp_path):
    context = _context()
    provider = ConversationProvider([
        {"queries": [], "references": []}, _final(context, "easy"),
        {"queries": [], "references": []}, _final(context, "easy"),
    ])
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {"name": "test"})
    engine.conversation_openings(context, "ada")
    cache_file = next((tmp_path / "prep_npc_conversation").glob("*.json"))
    cache_file.write_text('{"bad":true}', encoding="utf-8")
    assert engine.conversation_openings(context, "ada")[1]["from_cache"] is False
    assert len(provider.calls) == 4

    renamed = PrepContext((replace(context.documents[0], name="Ada Renamed"), context.documents[1]),
                          context.readings, context.fingerprint)
    rows, stage = engine.conversation_openings(renamed, "ada")
    assert rows == [] and stage["status"] == "unavailable"


def test_service_joins_successful_conversation_stage_without_changing_existing_panel_contracts(tmp_path):
    from core.club_generation import (
        CLUB_DASHBOARD_AI_REQUEST_VERSION, CLUB_DASHBOARD_PROMPT_VERSION,
        CLUB_DASHBOARD_SCHEMA_VERSION, CLUB_PANEL_AI_REQUEST_VERSION,
        CLUB_PANEL_PROMPT_VERSION, CLUB_PANEL_SCHEMA_VERSION,
        CLUB_SKELETON_VERSION, ClubGenerationService, build_npc_panel_skeleton,
    )
    from test_club_prep import SceneProvider

    paths = []
    for name in ("Ada", "Bea", "Cy"):
        path = tmp_path / f"{name}.md"
        path.write_text(f"# {name}\n\n{name} publicly supports the local shelter.\n", encoding="utf-8")
        paths.append(str(path))
    service = ClubGenerationService({}, cache_root=tmp_path / "cache", vault_root=tmp_path, provider=SceneProvider())
    result = service.build_event_result(paths, seed=41)
    clicked = result.event.attendee_ids[0]
    skeleton_before = build_npc_panel_skeleton(result, clicked)
    panel = service.build_npc_panel_from_result(result, clicked)

    assert panel["conversation_openings"][0]["lane"] == "easy"
    assert panel["conversation_openings"][0]["evidence"]
    assert panel["metadata"]["conversation_openings_stage"] == {"status": "complete", "from_cache": False}
    assert panel["who_matters"]
    assert build_npc_panel_skeleton(result, clicked) == skeleton_before
    assert CLUB_DASHBOARD_SCHEMA_VERSION == "club_dashboard_v20"
    assert CLUB_DASHBOARD_PROMPT_VERSION == "club_dashboard_prompt_v18"
    assert CLUB_DASHBOARD_AI_REQUEST_VERSION == "club_dashboard_ai_request_v9"
    assert CLUB_PANEL_SCHEMA_VERSION == "club_panel_v12"
    assert CLUB_PANEL_PROMPT_VERSION == "club_panel_prompt_v15"
    assert CLUB_PANEL_AI_REQUEST_VERSION == "club_panel_ai_request_v8"
    assert CLUB_SKELETON_VERSION == "club_skeleton_v7"
    assert CLUB_PREP_REASONING_VERSION == "club_prep_reasoning_v4"
    assert CLUB_PREP_ENCOUNTER_CUE_SCHEMA_VERSION == "club_prep_encounter_cue_v1"


@pytest.mark.parametrize("failed_stage", ["relevance", "conversation"])
def test_relevance_and_conversation_failures_are_independent(tmp_path, failed_stage):
    from core.club_generation import ClubGenerationService
    from test_club_prep import SceneProvider

    class IndependentFailureProvider(SceneProvider):
        def generate_from_messages(self, messages, **kwargs):
            if "INPUT_JSON:\n" in messages[-1]["content"]:
                payload = json.loads(messages[-1]["content"].split("INPUT_JSON:\n", 1)[1])
                is_conversation_final = "reading_coverage" in payload and "evidence" in payload
                is_relevance_final = "roster" in payload and "evidence" in payload and "sections" not in payload
                if (failed_stage == "conversation" and is_conversation_final) or (failed_stage == "relevance" and is_relevance_final):
                    self.calls.append(payload)
                    return json.dumps({"wrong": True})
            return super().generate_from_messages(messages, **kwargs)

    paths = []
    for name in ("Ada", "Bea", "Cy"):
        path = tmp_path / f"{name}.md"
        path.write_text(f"# {name}\n\n{name} publicly supports the local shelter.\n", encoding="utf-8")
        paths.append(str(path))
    service = ClubGenerationService({}, cache_root=tmp_path / "cache", provider=IndependentFailureProvider())
    result = service.build_event_result(paths, seed=51)
    panel = service.build_npc_panel_from_result(result, result.event.attendee_ids[0])
    if failed_stage == "conversation":
        assert panel["who_matters"]
        assert panel["metadata"]["who_matters_stage"]["status"] == "complete"
        assert panel["conversation_openings"] == []
        assert panel["metadata"]["conversation_openings_stage"]["status"] == "unavailable"
    else:
        assert panel["metadata"]["who_matters_stage"]["status"] == "unavailable"
        assert panel["conversation_openings"]
        assert panel["metadata"]["conversation_openings_stage"]["status"] == "complete"


def test_presentation_fallback_does_not_block_conversation_stage(tmp_path):
    from core.club_generation import ClubGenerationService
    from test_club_prep import SceneProvider

    class PresentationFailureProvider(SceneProvider):
        def generate_from_messages(self, messages, **kwargs):
            if "INPUT_JSON:\n" not in messages[-1]["content"]:
                self.calls.append({"canonical_panel": "malformed"})
                return json.dumps({"wrong": True})
            return super().generate_from_messages(messages, **kwargs)

    paths = []
    for name in ("Ada", "Bea", "Cy"):
        path = tmp_path / f"{name}.md"
        path.write_text(f"# {name}\n\n{name} publicly supports the local shelter.\n", encoding="utf-8")
        paths.append(str(path))
    service = ClubGenerationService({}, cache_root=tmp_path / "cache", provider=PresentationFailureProvider())
    result = service.build_event_result(paths, seed=52)
    panel = service.build_npc_panel_from_result(result, result.event.attendee_ids[0])
    assert panel["metadata"]["fallback_used"] is True
    assert panel["metadata"]["who_matters_stage"]["status"] == "complete"
    assert panel["metadata"]["conversation_openings_stage"]["status"] == "complete"
    assert panel["conversation_openings"]


def test_render_export_and_normal_json_share_conversation_projection(qapp):
    from core.club_models import ClubEvent
    from tools.debug_club_generation import _panel_text, _visible_panel_payload
    from ui.club_tab import ClubTab, build_table_prep_text

    context = _context()
    row = _lane("easy", [_passage(context, "radios").passage_id])
    panel = {
        "npc_id": "ada", "name": "Ada", "current_read": "Watching the room.",
        "conversation_openings": [row],
        "metadata": {"conversation_openings_stage": {"status": "complete"}},
        "presentation": {}, "tonight": {}, "conversation": {},
    }
    event = ClubEvent("event", ("ada",), "ada", {}, "cache", 1)
    table = build_table_prep_text(event, [{"npc_id": "ada", "name": "Ada"}], {"ada": panel})
    tab = ClubTab(object())
    try:
        tab._attendees = {"ada": {"npc_id": "ada", "name": "Ada"}}
        visible = tab._panel_html("ada", panel)
    finally:
        tab.close()
    normal = _visible_panel_payload(panel)
    debug = _panel_text(panel, [{"npc_id": "ada", "name": "Ada"}])
    formatted = conversation_opening_display_rows([row], {"status": "complete"})[0]
    assert formatted in table and formatted in visible
    assert visible.index("Conversation Openings") < visible.index("If Approached")
    assert table.index("Conversation Openings") < table.index("If Approached")
    assert normal["conversation_openings"] == [{key: row[key] for key in ("lane", "topic", "response", "possible_gain", "possible_risk")}]
    assert "evidence" not in json.dumps(normal["conversation_openings"])
    assert f"Why? {row['evidence'][0]}" in debug


def test_npc_status_combines_both_supplemental_stages_and_counts_empty_as_complete():
    from ui.club_tab import _npc_status_for_panel

    complete = {
        "who_matters": [], "conversation_openings": [],
        "metadata": {
            "generation_mode": "ai",
            "who_matters_stage": {"status": "complete", "from_cache": False},
            "conversation_openings_stage": {"status": "complete", "from_cache": True},
        },
    }
    assert _npc_status_for_panel(complete) == (
        "NPC panel ready from AI. Relevance complete; no grounded material surfaced. "
        "Conversation openings complete from cached AI; no grounded material surfaced."
    )
    unavailable = {
        **complete,
        "metadata": {
            **complete["metadata"],
            "conversation_openings_stage": {"status": "unavailable", "reason": "validation"},
        },
    }
    status = _npc_status_for_panel(unavailable)
    assert "NPC panel partially prepared" in status
    assert "Conversation openings unavailable: conversation-opening output did not validate." in status
    no_whole_sheet = {
        "conversation_openings": [],
        "metadata": {
            "generation_mode": "deterministic",
            "conversation_openings_stage": {"status": "unavailable", "reason": "deterministic"},
        },
    }
    assert _npc_status_for_panel(no_whole_sheet) == (
        "NPC panel partially prepared from deterministic preparation. "
        "Conversation openings unavailable: AI conversation-opening analysis was not run."
    )
