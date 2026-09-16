from __future__ import annotations

import copy
import json
import threading
from dataclasses import replace

import pytest
from PySide6.QtCore import QThread, Qt, QUrl

from core.club_generation import ClubBuildResult
from core.club_models import ClubEvent, ClubIndex, NpcIdentity
from ui.club_tab import ClubTab, _status_for_metadata, build_table_prep_text


class FakeService:
    def __init__(self) -> None:
        self.panel_calls = 0

    def build_npc_panel_from_result(self, _result, npc_id, *, use_ai=True, force=False):
        self.panel_calls += 1
        return {
            "npc_id": npc_id,
            "name": "Damien",
            "identity": {"affiliation": "Dockworkers"},
            "personality": [],
            "tonight": {},
            "people_here": [_relationship()],
            "conversation": {"likely_subjects": ["Damien distrusts Annabelle."], "sensitive": []},
            "interesting_detail": "",
            "metadata": {"generation_mode": "deterministic", "from_cache": False},
        }


class BlockingService:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.use_ai: bool | None = None
        self.rumor_limit: int | None = None

    def build_event_result(self, _guest_paths, *, host_path=None, rumor_limit=None, use_ai=True, progress=None, baseline_ready=None):
        self.use_ai = use_ai
        self.rumor_limit = rumor_limit
        self.started.set()
        self.release.wait(timeout=2)
        event = ClubEvent(
            event_id="event1",
            attendee_ids=("npc_a",),
            late_arrival_id="npc_a",
            cache_key="cache",
            seed=1,
            dashboard={
                "event": {"venue": "The Lantern Room"},
                "first_impression": "",
                "social_map": [],
                "notable_details": [],
                "rumors": [],
                "possible_drama": [],
                "interesting_connections": [],
                "unresolved_business": [],
                "opportunities": [],
                "top_connections": [],
                "background_details": [],
            },
        )
        identity = _identity("npc_a", "Damien")
        index = _index(identity)
        return _build_result(event, [identity], [index])


class BaselineBlockingService(FakeService):
    def __init__(self, baseline: ClubBuildResult, final: ClubBuildResult, *, fail_after_baseline: bool = False) -> None:
        super().__init__()
        self.baseline_result = baseline
        self.final_result = final
        self.fail_after_baseline = fail_after_baseline
        self.baseline_emitted = threading.Event()
        self.release = threading.Event()
        self.block_retry = False
        self.retry_started = threading.Event()
        self.retry_release = threading.Event()

    def build_event_result(self, _guest_paths, *, host_path=None, rumor_limit=None, use_ai=True, progress=None, baseline_ready=None):
        if baseline_ready:
            baseline_ready(self.baseline_result)
        self.baseline_emitted.set()
        self.release.wait(timeout=2)
        if self.fail_after_baseline:
            raise RuntimeError("DeepSeek leaked api_key=sk-test-secret from C:/vault/private.md")
        return self.final_result

    def complete_event_prep(self, _result, *, progress=None):
        self.retry_started.set()
        if self.block_retry:
            self.retry_release.wait(timeout=2)
        return self.final_result


class PanelService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def build_npc_panel_from_result(self, _result, npc_id, *, use_ai=True, force=False):
        self.calls.append({"npc_id": npc_id, "use_ai": use_ai, "force": force})
        return {
            "npc_id": npc_id,
            "name": "Damien",
            "identity": {"affiliation": "Dockworkers"},
            "personality": ["direct"],
            "tonight": {"current_demeanor": "watchful"},
            "people_here": [_relationship()],
            "conversation": {"likely_subjects": ["Damien distrusts Annabelle."], "sensitive": []},
            "interesting_detail": "",
            "metadata": {"generation_mode": "ai", "from_cache": False},
        }


class BlockingPanelService:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.started = threading.Event()
        self.release = threading.Event()

    def build_npc_panel_from_result(self, _result, npc_id, *, use_ai=True, force=False):
        self.calls.append({"npc_id": npc_id, "use_ai": use_ai, "force": force})
        self.started.set()
        self.release.wait(timeout=2)
        return {
            "npc_id": npc_id,
            "name": "Damien" if npc_id == "npc_a" else "Annabelle",
            "identity": {"affiliation": "Dockworkers"},
            "personality": [],
            "tonight": {},
            "people_here": [_relationship(f"{npc_id} panel.")],
            "conversation": {"likely_subjects": [], "sensitive": []},
            "interesting_detail": "",
            "metadata": {"generation_mode": "ai", "from_cache": False},
        }


class LateReplacementRaceService(PanelService):
    def __init__(self) -> None:
        super().__init__()
        self.panel_started = threading.Event()
        self.release_panel = threading.Event()

    def build_npc_panel_from_result(self, result, npc_id, *, use_ai=True, force=False):
        if npc_id == "npc_a":
            self.panel_started.set()
            self.release_panel.wait(timeout=2)
        return super().build_npc_panel_from_result(result, npc_id, use_ai=use_ai, force=force)

    def change_late_arrival_prep(self, result, *, host_path=None, progress=None):
        dashboard = copy.deepcopy(result.event.dashboard)
        dashboard["scene_prep"] = {
            "revision": "late-replacement",
            "opening": {"text": "The room settles into a new arrangement."},
            "power_players": [],
            "encounters": [
                {"number": 1, "members": ["npc_b"], "availability": "present", "basis": "tag_similarity", "cue": ""},
                {"number": 2, "members": ["npc_a"], "availability": "expected", "basis": "late_arrival", "cue": ""},
            ],
            "gm_notes": [],
            "incomplete": False,
        }
        event = replace(
            result.event,
            late_arrival_id="npc_a",
            cache_key="cache-event1-late-replacement",
            dashboard=dashboard,
        )
        return result.with_event(event)


class StaticPanelService:
    def __init__(self, panel: dict) -> None:
        self.panel = panel
        self.calls: list[dict] = []

    def build_npc_panel_from_result(self, _result, npc_id, *, use_ai=True, force=False):
        self.calls.append({"npc_id": npc_id, "use_ai": use_ai, "force": force})
        return copy.deepcopy(self.panel)


class FailingPanelService:
    def build_npc_panel_from_result(self, _result, _npc_id, *, use_ai=True, force=False):
        raise RuntimeError("DeepSeek leaked api_key=sk-test-secret from C:/vault/private.md")


class StirTrapCache:
    def __init__(self) -> None:
        self.writes = 0

    def write_json(self, *_args, **_kwargs) -> None:
        self.writes += 1
        raise AssertionError("Stir the Room must not write cache state")


class StirTrapProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_from_messages(self, *_args, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("Stir the Room must not call the provider")


class StirTrapService:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.cache = StirTrapCache()
        self.provider = StirTrapProvider()

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        self.calls.append(name)
        raise AssertionError(f"Stir the Room must not call service method {name}")


def _identity(npc_id: str, name: str) -> NpcIdentity:
    return NpcIdentity(
        npc_id=npc_id,
        current_path=f"C:/vault/{name}.md",
        display_name=name,
        aliases=(),
        content_fingerprint=npc_id,
    )


def _index(identity: NpcIdentity) -> ClubIndex:
    return ClubIndex(
        npc_id=identity.npc_id,
        name=identity.display_name,
        path=identity.current_path,
        sheet_revision_hash="rev",
        schema_version="test",
        prompt_version="test",
        affiliation="Dockworkers" if identity.display_name == "Damien" else "Artists",
    )


def _relationship(summary: str = "Damien distrusts Annabelle.") -> dict:
    return {
        "item_id": "rel-1",
        "section": "people_here",
        "type": "distrust",
        "fact_scope": "relationship",
        "source_npc_id": "npc_a",
        "target_npc_id": "npc_b",
        "mentioned_npc_ids": [],
        "display_label": "Distrust",
        "summary": summary,
        "prep_text": summary,
        "characters": ["npc_a", "npc_b"],
        "sources": [{"source_id": "s"}],
    }


def _panel_with_relevance(
    *,
    npc_id: str = "npc_a",
    name: str = "Damien",
    metadata: dict | None = None,
    who_matters: list[dict] | None = None,
) -> dict:
    panel_metadata = {
        "generation_mode": "ai",
        "from_cache": False,
        "fallback_used": False,
        "ai_error_type": None,
        "who_matters_stage": {"status": "complete", "from_cache": False},
    }
    if metadata:
        panel_metadata.update(metadata)
    return {
        "npc_id": npc_id,
        "name": name,
        "identity": {"affiliation": "Dockworkers"},
        "personality": ["direct"],
        "tonight": {"current_demeanor": "watchful"},
        "people_here": [_relationship()],
        "conversation": {"likely_subjects": ["Ask what changed tonight."], "sensitive": []},
        "interesting_detail": "",
        "who_matters": [
            {
                "npc_id": "npc_b",
                "text": "Possible leverage is worth watching.",
                "classification": "inferred",
                "evidence": [],
            }
        ]
        if who_matters is None
        else who_matters,
        "metadata": panel_metadata,
    }


def _ui_build_result(event_id: str = "event1") -> ClubBuildResult:
    event = ClubEvent(
        event_id=event_id,
        attendee_ids=("npc_a", "npc_b"),
        late_arrival_id="npc_b",
        cache_key=f"cache-{event_id}",
        seed=1,
        dashboard={"event": {"venue": "The Lantern Room"}},
    )
    identities = [_identity("npc_a", "Damien"), _identity("npc_b", "Annabelle")]
    return _build_result(event, identities, [_index(identity) for identity in identities], [_relationship()])


def _many_ui_build_result(count: int = 6) -> ClubBuildResult:
    names = ("Ada", "Bea", "Cy", "Dee", "Evie", "Faye")[:count]
    identities = [_identity(f"npc_{index}", name) for index, name in enumerate(names)]
    event = ClubEvent(
        event_id="many-event",
        attendee_ids=tuple(identity.npc_id for identity in identities),
        late_arrival_id=identities[-1].npc_id,
        cache_key="cache-many-event",
        seed=1,
        dashboard={"event": {"venue": "The Lantern Room"}},
    )
    return _build_result(event, identities, [_index(identity) for identity in identities])


def _list_ids(widget) -> list[str]:
    return [str(widget.item(row).data(Qt.UserRole)) for row in range(widget.count())]


def _click_list_row(qtbot, widget, row: int) -> None:
    item = widget.item(row)
    widget.scrollToItem(item)
    qtbot.mouseClick(widget.viewport(), Qt.MouseButton.LeftButton, pos=widget.visualItemRect(item).center())


def _stir_build_result(
    *,
    event_id: str = "stir-event",
    seed: int = 0,
    late_arrival_id: str = "npc_late",
    rumor: str = "The missing ledger may be changing hands.",
) -> ClubBuildResult:
    identities = [
        _identity("npc_a", "Ada"),
        _identity("npc_b", "Bea"),
        _identity("npc_c", "Cy"),
        _identity("npc_d", "Dee"),
        _identity("npc_e", "Evie"),
        _identity("npc_late", "Late"),
    ]
    event = ClubEvent(
        event_id=event_id,
        attendee_ids=tuple(identity.npc_id for identity in identities),
        late_arrival_id=late_arrival_id,
        cache_key=f"cache-{event_id}",
        seed=seed,
        dashboard={
            "event": {"venue": "The Lantern Room"},
            "rumors_in_circulation": [rumor] if rumor else [],
            "rumors": [],
            "scene_prep": {
                "opening": {"text": "Choose someone to approach."},
                "power_players": [],
                "encounters": [
                    {"number": 1, "members": ["npc_a", "npc_b"], "availability": "present", "basis": "tag_similarity", "cue": ""},
                    {"number": 2, "members": ["npc_c", "npc_d"], "availability": "present", "basis": "tag_similarity", "cue": ""},
                    {"number": 3, "members": ["npc_e"], "availability": "present", "basis": "tag_similarity", "cue": ""},
                    {"number": 4, "members": ["npc_late"], "availability": "expected", "basis": "late_arrival", "cue": ""},
                ],
                "gm_notes": [],
                "incomplete": False,
            },
        },
    )
    return _build_result(event, identities, [_index(identity) for identity in identities])


def _readiness_build_result(mode: str, *, incomplete: bool, revision: str) -> ClubBuildResult:
    result = _stir_build_result(event_id=f"readiness-{revision}")
    dashboard = copy.deepcopy(result.event.dashboard)
    dashboard["scene_prep"]["incomplete"] = incomplete
    dashboard["scene_prep"]["revision"] = revision
    metadata = {
        "generation_mode": mode,
        "from_cache": False,
        "ai_attempted": mode != "deterministic",
        "fallback_used": mode in {"partial_ai", "deterministic_fallback"},
        "ai_error_type": "provider_request_failed" if incomplete else None,
    }
    event = replace(result.event, dashboard=dashboard, metadata=metadata)
    return result.with_event(event)


def _build_result(
    event: ClubEvent,
    identities: list[NpcIdentity],
    indexes: list[ClubIndex],
    relationships: list[dict] | None = None,
) -> ClubBuildResult:
    relationships = relationships or []
    return ClubBuildResult(
        event=event,
        identities=tuple(identities),
        indexes_by_id={index.npc_id: index for index in indexes},
        attendee_relationships=tuple(relationships),
        attendee_facts=tuple(relationships),
        source_map={
            "s": {
                "source": {"source_id": "s", "section": "Relationships"},
                "summary": "Damien distrusts Annabelle.",
                "target_npc_id": "npc_b",
            }
        },
        debug_context={"test": True},
    )


def test_club_tab_renders_dashboard_and_clickable_names(qapp) -> None:
    tab = ClubTab(FakeService())
    try:
        event = ClubEvent(
            event_id="event1",
            attendee_ids=("npc_a", "npc_b"),
            late_arrival_id="npc_b",
            cache_key="cache",
            seed=1,
            dashboard={
                "event": {"venue": "The Lantern Room", "event_type": "social gathering", "mood": "tense"},
                "first_impression": "The room hums.",
                "room_situation": "The room is split between court smiles and visible grudges.",
                "hot_connections": ["Damien and Annabelle are the tie everyone can see."],
                "possible_pressure": ["Existing tension could become a scene lever tonight."],
                "rumors_in_circulation": [],
                "guest_brief": ["Damien: watching Annabelle.", "Annabelle: late arrival."],
                "social_map": [{"location": "Bar", "npc_ids": ["npc_a"], "summary": "Watching."}],
                "notable_details": [],
                "rumors": [],
                "possible_drama": [{"summary": "Existing tension.", "characters": ["npc_a", "npc_b"], "sources": [{"source_id": "s"}]}],
                "interesting_connections": [],
                "unresolved_business": [],
                "opportunities": [],
                "top_connections": [],
                "background_details": [],
            },
        )

        identities = [_identity("npc_a", "Damien"), _identity("npc_b", "Annabelle")]
        tab._on_event_ready(_build_result(event, identities, [_index(identity) for identity in identities], [_relationship("Existing tension.")]))

        assert tab.guestList.count() == 2
        assert "npc:npc_b" in tab.summaryBrowser.toHtml()
        assert "Late Arrival" in tab.summaryBrowser.toHtml()
        assert "Room Situation" in tab.summaryBrowser.toPlainText()
        assert "The room is split between court smiles and visible grudges." in tab.summaryBrowser.toPlainText()
    finally:
        tab.close()


def test_build_table_prep_text_is_gm_only_and_excludes_debug_data() -> None:
    event = ClubEvent(
        event_id="event1",
        attendee_ids=("npc_a", "npc_b"),
        late_arrival_id="npc_b",
        cache_key="cache",
        seed=1,
        dashboard={
            "event": {"venue": "The Lantern Room", "event_type": "social gathering", "mood": "tense"},
            "room_situation": "The room is split between court smiles and grudges.",
            "hot_connections": ["Damien and Annabelle are visibly tense."],
            "possible_pressure": ["A blackmail threat could surface tonight."],
            "rumors_in_circulation": ["People may be whispering: Damien keeps a ledger."],
            "guest_brief": ["Damien: watching Annabelle.", "Annabelle: late arrival."],
            "source_map": {"s": {"excerpt": "raw source line"}},
            "rumors": [{"summary": "raw grounded rumor", "sources": [{"source_id": "s"}]}],
        },
        metadata={"validation_error": "provider failed api_key=sk-test-secret"},
    )
    panel = {
        "npc_id": "npc_a",
        "name": "Damien",
        "current_read": "Dockworkers; watchful.",
        "people_here": [_relationship("Damien can pressure Annabelle tonight.")],
        "likely_conversation": ["Ask about the ledger."],
        "sensitive_subjects": ["Do not volunteer that Damien holds blackmail evidence."],
        "useful_hook": "Press Damien about the ledger.",
        "metadata": {"validation_error": "RAW_PROVIDER_BODY_DO_NOT_LEAK"},
        "sources": [{"source_id": "s"}],
        "skeleton": {"source_ids": ["s"]},
    }

    text = build_table_prep_text(
        event,
        [{"npc_id": "npc_a", "name": "Damien"}, {"npc_id": "npc_b", "name": "Annabelle"}],
        {"npc_a": panel},
    )

    assert "GM TABLE PREP" in text
    assert "Keep Guarded\n- Do not volunteer" in text
    assert "People may be whispering" in text
    assert "Press Damien about the ledger." in text
    for forbidden in (
        "source_map",
        "sources",
        "skeleton",
        "metadata",
        "validation_error",
        "raw source line",
        "source_id",
        "sk-test-secret",
        "RAW_PROVIDER_BODY_DO_NOT_LEAK",
        "event1",
        "npc_a",
    ):
        assert forbidden not in text


def test_table_prep_uses_generated_rumor_shortlist_when_ui_control_changes(qapp) -> None:
    tab = ClubTab(FakeService())
    try:
        event = ClubEvent(
            event_id="event1",
            attendee_ids=("npc_a",),
            late_arrival_id="npc_a",
            cache_key="cache",
            seed=1,
            dashboard={
                "event": {"venue": "The Lantern Room"},
                "room_situation": "The room is ready.",
                "hot_connections": [],
                "possible_pressure": [],
                "rumors_in_circulation": ["Selected rumor 1.", "Selected rumor 2.", "Selected rumor 3."],
                "rumor_selection": {"limit": 3, "selected_count": 3, "grounded_count": 7},
                "guest_brief": [],
            },
        )
        identity = _identity("npc_a", "Damien")
        tab._on_event_ready(_build_result(event, [identity], [_index(identity)]))
        tab.rumorLimitCombo.setCurrentText("7")

        text = build_table_prep_text(tab._event, list(tab._attendees.values()), tab._panels_by_npc_id)

        assert "Rumors in Circulation - 3 selected from 7 grounded rumors" in text
        assert "Selected rumor 3." in text
        assert "Selected rumor 4." not in text
    finally:
        tab.close()


def test_rumor_pool_browse_uses_allowlisted_display_fields(qapp) -> None:
    tab = ClubTab(FakeService())
    try:
        event = ClubEvent(
            event_id="event1",
            attendee_ids=("npc_a",),
            late_arrival_id="npc_a",
            cache_key="cache",
            seed=1,
            dashboard={"event": {"venue": "The Lantern Room"}},
        )
        identity = _identity("npc_a", "Damien")
        result = _build_result(event, [identity], [_index(identity)])
        result.source_map.clear()
        result.source_map["secret-src-123"] = {
            "source": {
                "source_id": "secret-src-123",
                "path": r"C:\Vault\Characters\Damien.md",
                "section": "Whispers",
                "character_id": "npc_a",
            },
            "npc_id": "npc_a",
            "name": "Damien",
            "fact_type": "rumor",
            "summary": "Raw provenance text",
            "target_npc_id": None,
        }
        tab._on_event_ready(result)
        rumor = {
            "type": "rumor",
            "fact_scope": "self",
            "source_npc_id": "npc_a",
            "target_npc_id": None,
            "mentioned_npc_ids": [],
            "characters": ["npc_a"],
            "summary": "Someone says Damien keeps a hidden ledger.",
            "prep_text": "Someone says Damien keeps a hidden ledger.",
            "sources": [
                {
                    "source_id": "secret-src-123",
                    "path": r"C:\Vault\Characters\Damien.md",
                    "section": "Whispers",
                    "character_id": "npc_a",
                    "excerpt": "RAW_PROVIDER_BODY_DO_NOT_LEAK",
                }
            ],
        }

        rendered = tab._rumor_pool_html(
            {
                "rumor_pool": [rumor],
                "rumor_selection_audit": [
                    {"fact": rumor, "selection_state": "selected_after_source_relaxation"}
                ],
            }
        )

        assert "Someone says Damien keeps a hidden ledger." in rendered
        assert "Damien - Whispers" in rendered
        for forbidden in (
            "secret-src-123",
            "Vault",
            "Characters",
            "npc_a",
            "selected_after_source_relaxation",
            "RAW_PROVIDER_BODY_DO_NOT_LEAK",
            "Raw provenance text",
        ):
            assert forbidden not in rendered
    finally:
        tab.close()


def test_build_table_prep_text_orders_panels_by_attendee_order_and_omits_orphans() -> None:
    event = ClubEvent(
        event_id="event1",
        attendee_ids=("npc_b", "npc_a"),
        late_arrival_id="npc_b",
        cache_key="cache",
        seed=1,
        dashboard={
            "event": {"venue": "The Lantern Room"},
            "room_situation": "The room is ready.",
            "hot_connections": [],
            "possible_pressure": [],
            "rumors_in_circulation": [],
            "guest_brief": [],
        },
    )
    text = build_table_prep_text(
        event,
        [{"npc_id": "npc_a", "name": "Damien"}, {"npc_id": "npc_b", "name": "Annabelle"}],
        {
            "npc_a": {"name": "Damien", "current_read": "Damien waits.", "people_here": [], "likely_conversation": [], "sensitive_subjects": [], "useful_hook": ""},
            "npc_b": {"name": "Annabelle", "current_read": "Annabelle waits.", "people_here": [], "likely_conversation": [], "sensitive_subjects": [], "useful_hook": ""},
            "npc_x": {"name": "Orphan", "current_read": "Should not export."},
        },
    )

    assert text.index("Annabelle") < text.index("Damien")
    assert "Orphan" not in text
    assert "Should not export" not in text


def test_build_table_prep_text_with_no_generated_panels_is_dashboard_only() -> None:
    event = ClubEvent(
        event_id="event1",
        attendee_ids=("npc_a",),
        late_arrival_id="npc_a",
        cache_key="cache",
        seed=1,
        dashboard={
            "event": {"venue": "The Lantern Room"},
            "room_situation": "The room is quiet.",
            "hot_connections": [],
            "possible_pressure": [],
            "rumors_in_circulation": [],
            "guest_brief": ["Damien: late arrival."],
        },
    )

    text = build_table_prep_text(event, [{"npc_id": "npc_a", "name": "Damien"}], {})

    assert "The room is quiet." in text
    assert "NPC Panel" not in text
    assert "GM ONLY - DO NOT VOLUNTEER" not in text


def test_club_tab_demotes_empty_sections_and_cleans_dashboard_labels(qapp) -> None:
    tab = ClubTab(FakeService())
    try:
        top_item = {
            "type": "paramour",
            "display_label": "Paramour",
            "summary": "Legacy hot connection raw summary.",
            "prep_text": "Visible hot connection prose.",
            "characters": ["npc_a", "npc_b"],
            "sources": [{"source_id": "s"}],
        }
        extra_item = {
            "type": "business",
            "display_label": "Business",
            "summary": "A second grounded connection.",
            "characters": ["npc_a", "npc_b"],
            "sources": [{"source_id": "s2"}],
        }
        drama_item = {
            "type": "distrust",
            "display_label": "Distrust",
            "summary": "Legacy pressure raw summary.",
            "prep_text": "Damien and Annabelle have a pressure point the PCs can notice.",
            "characters": ["npc_a", "npc_b"],
            "sources": [{"source_id": "s3"}],
        }
        event = ClubEvent(
            event_id="event1",
            attendee_ids=("npc_a", "npc_b"),
            late_arrival_id="npc_b",
            cache_key="cache",
            seed=1,
            dashboard={
                "event": {"venue": "The Lantern Room", "event_type": "social gathering", "mood": "tense"},
                "first_impression": "The room hums.",
                "room_situation": "Visible room situation prose.",
                "hot_connections": ["Visible hot connection prose."],
                "possible_pressure": ["Damien and Annabelle have a pressure point the PCs can notice."],
                "rumors_in_circulation": ["Visible rumor prose."],
                "guest_brief": ["Damien: visible table cue.", "Annabelle: visible table cue."],
                "social_map": [{"location": "Bar", "npc_ids": ["npc_a", "npc_b"], "summary": "Damien and Annabelle anchor the visible distrust tension."}],
                "notable_details": [],
                "rumors": [
                    {
                        "type": "rumor",
                        "display_label": "Topic",
                        "summary": "Test Subject: Some claim Aluc is studying Alexa.",
                        "prep_text": "Visible rumor prose.",
                        "characters": ["npc_a"],
                        "sources": [{"source_id": "s"}],
                    }
                ],
                "possible_drama": [drama_item],
                "interesting_connections": [top_item, drama_item, extra_item],
                "unresolved_business": [],
                "opportunities": [],
                "top_connections": [top_item],
                "background_details": [],
            },
        )
        identities = [_identity("npc_a", "Damien"), _identity("npc_b", "Annabelle")]

        tab._on_event_ready(_build_result(event, identities, [_index(identity) for identity in identities], [_relationship("Shared visible tie.")]))

        text = tab.summaryBrowser.toPlainText()
        html = tab.summaryBrowser.toHtml()
        assert "Topic:" not in text
        assert "Test Subject: Some claim Aluc is studying Alexa." not in text
        assert "Legacy hot connection raw summary." not in text
        assert "Visible hot connection prose." in text
        assert "Damien and Annabelle have a pressure point the PCs can notice." in text
        assert "Legacy pressure raw summary." not in text
        assert "Visible rumor prose." in text
        assert "A second grounded connection." not in text
        assert "Hot Connections" in text
        assert "Possible Pressure" in text
        assert "Rumors in Circulation" in text
        assert "Guests" in text
        assert "Nothing else grounded right now:" not in text
        assert "Unresolved Business" not in text
        assert "No grounded entries for this section." not in text
        assert "npc:npc_b" in html
        assert "Why?" not in text
        assert "s3" not in text
        assert "Relationships" not in text
    finally:
        tab.close()


def test_club_tab_missing_visible_fields_renders_first_impression_but_not_legacy_arrays(qapp) -> None:
    tab = ClubTab(FakeService())
    try:
        event = ClubEvent(
            event_id="event1",
            attendee_ids=("npc_a", "npc_b"),
            late_arrival_id="npc_b",
            cache_key="cache",
            seed=1,
            dashboard={
                "event": {"venue": "The Lantern Room", "event_type": "social gathering", "mood": "tense"},
                "first_impression": "Legacy first impression.",
                "social_map": [],
                "notable_details": ["Legacy notable detail."],
                "rumors": [{"summary": "Legacy rumor raw summary.", "characters": ["npc_a"], "sources": [{"source_id": "s"}]}],
                "possible_drama": [{"summary": "Legacy pressure raw summary.", "characters": ["npc_a", "npc_b"], "sources": [{"source_id": "s"}]}],
                "interesting_connections": [{"summary": "Legacy interesting raw summary.", "characters": ["npc_a", "npc_b"], "sources": [{"source_id": "s"}]}],
                "unresolved_business": [{"summary": "Legacy unresolved raw summary.", "characters": ["npc_a"], "sources": [{"source_id": "s"}]}],
                "opportunities": [{"summary": "Legacy opportunity raw summary.", "characters": ["npc_a"], "sources": [{"source_id": "s"}]}],
                "top_connections": [{"summary": "Legacy top raw summary.", "characters": ["npc_a", "npc_b"], "sources": [{"source_id": "s"}]}],
                "background_details": [],
            },
        )
        identities = [_identity("npc_a", "Damien"), _identity("npc_b", "Annabelle")]

        tab._on_event_ready(_build_result(event, identities, [_index(identity) for identity in identities]))

        text = tab.summaryBrowser.toPlainText()
        assert "First impression (AI presentation): Legacy first impression." in text
        assert "Legacy notable detail." not in text
        assert "Legacy rumor raw summary." not in text
        assert "Legacy pressure raw summary." not in text
        assert "Legacy interesting raw summary." not in text
        assert "Legacy unresolved raw summary." not in text
        assert "Legacy opportunity raw summary." not in text
        assert "Legacy top raw summary." not in text
        assert "Other Grounded Hooks" not in text
        assert "No grounded prep surfaced for this section." in text
    finally:
        tab.close()


def test_club_tab_retains_worker_until_thread_finishes(qapp, qtbot) -> None:
    service = BlockingService()
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    try:
        tab._run_event_worker(["C:/vault/Damien.md"], None)

        qtbot.waitUntil(lambda: service.started.is_set(), timeout=1000)
        assert service.use_ai is True
        assert service.rumor_limit == 5
        assert tab._workers

        service.release.set()
        qtbot.waitUntil(lambda: not tab._workers, timeout=3000)
        assert tab.statusLabel.text() == "Club event ready."
    finally:
        service.release.set()
        tab.close()


def test_initial_baseline_stays_visible_but_gates_npc_prep_and_export_until_final(qapp, qtbot, capsys) -> None:
    baseline = _readiness_build_result("deterministic", incomplete=True, revision="baseline-revision")
    final = _readiness_build_result("ai", incomplete=False, revision="final-revision")
    service = BaselineBlockingService(baseline, final)
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    qapp.clipboard().setText("unchanged")
    try:
        tab._run_event_worker(["C:/vault/Ada.md"], None)
        qtbot.waitUntil(lambda: service.baseline_emitted.is_set() and tab._event is baseline.event, timeout=3000)

        assert "Social Groups and Loners" in tab.summaryBrowser.toPlainText()
        assert tab.guestList.count() == len(baseline.event.attendee_ids)
        assert not tab.guestList.isEnabled()
        assert not tab.copyTablePrepBtn.isEnabled()
        assert "npc:npc_a" not in tab.summaryBrowser.toHtml()
        assert tab.statusLabel.text() == (
            "Roster and groups are ready. Full event prep is still building; "
            "NPC prep and GM export will unlock when it settles."
        )

        tab._on_anchor_clicked(QUrl("group:1"))
        assert "Ada" in tab.drawer.toPlainText() and "Bea" in tab.drawer.toPlainText()
        assert "npc:npc_a" not in tab.drawer.toHtml()
        tab._open_npc("npc_a")
        tab._run_panel_worker("npc_a")
        tab.copy_table_prep()
        assert service.panel_calls == 0
        assert qapp.clipboard().text() == "unchanged"

        service.release.set()
        qtbot.waitUntil(lambda: tab._event is final.event and not tab._event_busy, timeout=3000)
        assert tab.guestList.isEnabled()
        assert tab.copyTablePrepBtn.isEnabled()
        assert "npc:npc_e" in tab.summaryBrowser.toHtml()
        assert tab.statusLabel.text() == "Club event ready from AI."

        tab._run_panel_worker("npc_a")
        qtbot.waitUntil(lambda: service.panel_calls == 1, timeout=3000)
        qtbot.waitUntil(lambda: not tab._workers, timeout=3000)
        output = capsys.readouterr().out
        assert output.count("readiness_transition=baseline_visible") == 1
        assert output.count("readiness_transition=prep_settled_final") == 1
        assert "generation_token=1 prep_revision=baseline-revision" in output
        assert "generation_token=1 prep_revision=final-revision" in output
    finally:
        service.release.set()
        tab.close()


@pytest.mark.parametrize(
    ("mode", "transition", "status_fragment", "export_fragment", "copy_fragment"),
    [
        (
            "partial_ai",
            "prep_settled_incomplete_ai",
            "available with incomplete partial AI prep",
            "Prep Status: INCOMPLETE — partial AI prep",
            "Incomplete partial-AI GM table prep copied",
        ),
        (
            "deterministic_fallback",
            "prep_settled_fallback",
            "available with incomplete deterministic fallback",
            "Prep Status: INCOMPLETE — deterministic fallback",
            "Incomplete deterministic-fallback GM table prep copied",
        ),
    ],
)
def test_settled_incomplete_event_unlocks_actions_and_labels_export(
    qapp, capsys, mode, transition, status_fragment, export_fragment, copy_fragment
) -> None:
    baseline = _readiness_build_result("deterministic", incomplete=True, revision="baseline")
    final = _readiness_build_result(mode, incomplete=True, revision="settled")
    tab = ClubTab(FakeService())
    try:
        tab._event_generation_token = 7
        tab._pending_event_token = 7
        tab._accept_baseline_result(7, baseline)
        tab._accept_event_result(7, final)

        assert tab.guestList.isEnabled()
        assert tab.copyTablePrepBtn.isEnabled()
        assert tab.retryPrepBtn.isEnabled()
        assert tab.copyTablePrepBtn.text() == "Copy Incomplete GM Table Prep (0 NPCs)"
        assert status_fragment in tab.statusLabel.text()

        tab.copy_table_prep()
        assert export_fragment in qapp.clipboard().text()
        assert copy_fragment in tab.statusLabel.text()
        output = capsys.readouterr().out
        assert output.count(f"readiness_transition={transition}") == 1
        assert "generation_token=7 prep_revision=settled" in output
    finally:
        tab.close()


def test_failure_after_baseline_keeps_actions_gated_until_retry_settles(qapp, qtbot, capsys) -> None:
    baseline = _readiness_build_result("deterministic", incomplete=True, revision="baseline")
    final = _readiness_build_result("ai", incomplete=False, revision="retry-final")
    service = BaselineBlockingService(baseline, final, fail_after_baseline=True)
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    try:
        tab._run_event_worker(["C:/vault/Ada.md"], None)
        qtbot.waitUntil(lambda: service.baseline_emitted.is_set() and tab._event is baseline.event, timeout=3000)
        service.release.set()
        qtbot.waitUntil(
            lambda: not tab._event_busy and not tab._workers and tab.retryPrepBtn.isEnabled(),
            timeout=3000,
        )

        assert not tab.guestList.isEnabled()
        assert not tab.copyTablePrepBtn.isEnabled()
        assert tab.retryPrepBtn.isEnabled()
        assert tab.statusLabel.text() == (
            "Roster and groups remain visible, but full event prep did not finish. "
            "NPC prep and GM export remain unavailable; use Retry Incomplete Prep."
        )
        output = capsys.readouterr().out
        assert output.count("readiness_transition=prep_failed_with_baseline") == 1
        assert "generation_token=1 prep_revision=baseline" in output
        assert "sk-test-secret" not in output
        assert "C:/vault" not in output

        service.fail_after_baseline = False
        service.block_retry = True
        qapp.clipboard().setText("still unchanged")
        tab.retryPrepBtn.click()
        qtbot.waitUntil(lambda: service.retry_started.is_set(), timeout=3000)
        tab._run_panel_worker("npc_a")
        tab.copy_table_prep()
        assert service.panel_calls == 0
        assert qapp.clipboard().text() == "still unchanged"
        service.retry_release.set()
        qtbot.waitUntil(lambda: tab._event is final.event and not tab._event_busy, timeout=3000)
        assert tab.guestList.isEnabled()
        assert tab.copyTablePrepBtn.isEnabled()
        assert not tab.retryPrepBtn.isEnabled()
        assert tab.statusLabel.text() == "Club event ready from AI."
        qtbot.waitUntil(lambda: not tab._workers, timeout=3000)
    finally:
        service.release.set()
        service.retry_release.set()
        tab.close()


def test_stale_event_signals_cannot_unlock_or_relabel_pending_baseline(qapp, capsys) -> None:
    current = _readiness_build_result("deterministic", incomplete=True, revision="current-baseline")
    stale = _readiness_build_result("ai", incomplete=False, revision="stale-final")
    tab = ClubTab(FakeService())
    try:
        tab._event_generation_token = 2
        tab._pending_event_token = 2
        tab._accept_baseline_result(2, current)
        status = tab.statusLabel.text()
        capsys.readouterr()

        tab._accept_baseline_result(1, stale)
        tab._accept_event_result(1, stale)
        tab._event_error(1, "stale raw error")

        assert tab._event is current.event
        assert not tab.guestList.isEnabled()
        assert not tab.copyTablePrepBtn.isEnabled()
        assert tab.statusLabel.text() == status
        assert "readiness_transition" not in capsys.readouterr().out
    finally:
        tab.close()


def test_club_tab_has_no_non_ai_toggle(qapp) -> None:
    tab = ClubTab(FakeService())
    try:
        assert not hasattr(tab, "useAiCheck")
        assert "AI prep panel" in tab.drawer.toPlainText()
    finally:
        tab.close()


def test_status_for_metadata_reports_fallback_once_without_raw_validation_error() -> None:
    metadata = {
        "generation_mode": "deterministic_fallback",
        "from_cache": False,
        "fallback_used": True,
        "ai_error_type": "provider_malformed_response",
        "validation_error": "DeepSeek response did not include non-empty choices[0].message.content.",
    }

    status = _status_for_metadata("Club event", metadata)

    assert status == "Club event ready using deterministic fallback. AI failed: provider malformed response."
    assert status.count("AI failed") == 1
    assert "DeepSeek" not in status
    assert "choices[0]" not in status


def test_status_for_metadata_distinguishes_cached_ai_regeneration_failure() -> None:
    metadata = {
        "generation_mode": "ai",
        "from_cache": True,
        "ai_attempted": True,
        "fallback_used": False,
        "ai_error_type": "provider_request_failed",
        "validation_error": "DeepSeek request failed: raw transport detail.",
    }

    status = _status_for_metadata("NPC panel", metadata)

    assert status == "NPC panel ready from cached AI. Regeneration failed: provider request failed."
    assert "deterministic fallback" not in status
    assert "raw transport detail" not in status


@pytest.mark.parametrize(
    ("metadata", "who_matters", "expected", "forbidden"),
    [
        (
            {},
            None,
            "NPC panel ready from AI. Relevance ready from AI.",
            (),
        ),
        (
            {
                "from_cache": True,
                "ai_attempted": True,
                "ai_error_type": "provider_request_failed",
                "validation_error": "DeepSeek raw transport detail",
                "who_matters_stage": {
                    "status": "complete",
                    "from_cache": True,
                    "regeneration_failed": True,
                    "reason": "transport",
                },
            },
            None,
            (
                "NPC panel ready from cached AI. Presentation regeneration failed: provider request failed. "
                "Relevance ready from cached AI. Relevance regeneration failed: provider request failed."
            ),
            ("DeepSeek", "raw transport detail"),
        ),
        (
            {},
            [],
            "NPC panel ready from AI. Relevance complete; no grounded material surfaced.",
            (),
        ),
        (
            {
                "from_cache": True,
                "who_matters_stage": {"status": "complete", "from_cache": True},
            },
            [],
            "NPC panel ready from cached AI. Relevance complete from cached AI; no grounded material surfaced.",
            (),
        ),
        (
            {
                "generation_mode": "deterministic_fallback",
                "fallback_used": True,
                "ai_error_type": "provider_timeout",
                "who_matters_stage": {"status": "unavailable", "reason": "required_evidence_unavailable"},
            },
            [],
            (
                "NPC panel partially prepared using deterministic fallback. AI presentation failed: provider timed out. "
                "Relevance unavailable: grounded evidence unavailable."
            ),
            (),
        ),
        (
            {
                "who_matters_stage": {
                    "status": "unavailable",
                    "reason": "DeepSeek leaked api_key=sk-test-secret from C:/vault/private.md",
                }
            },
            [],
            "NPC panel partially prepared from AI. Relevance unavailable: relevance analysis failed.",
            ("DeepSeek", "sk-test-secret", "C:/vault"),
        ),
        (
            {"who_matters_stage": "malformed"},
            [],
            "NPC panel partially prepared from AI. Relevance unavailable: relevance analysis failed.",
            ("malformed",),
        ),
    ],
)
def test_npc_status_combines_presentation_and_relevance_on_visible_label(
    qapp, qtbot, metadata, who_matters, expected, forbidden
) -> None:
    panel = _panel_with_relevance(metadata=metadata, who_matters=who_matters)
    service = StaticPanelService(panel)
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    try:
        tab._on_event_ready(_ui_build_result())

        tab.guestList.itemClicked.emit(tab.guestList.item(0))

        qtbot.waitUntil(lambda: not tab._workers, timeout=3000)
        assert tab.statusLabel.text() == expected
        assert tab.copyTablePrepBtn.text() == "Copy GM Table Prep (1 NPC)"
        for text in forbidden:
            assert text not in tab.statusLabel.text()
    finally:
        tab.close()


def test_table_prep_count_tracks_valid_results_export_and_event_replacement(qapp) -> None:
    tab = ClubTab(FakeService())
    try:
        tab._on_event_ready(_ui_build_result())
        token = tab._event_generation_token
        revision = tab._prep_revision
        first = _panel_with_relevance()
        second = _panel_with_relevance(npc_id="npc_b", name="Annabelle", who_matters=[])
        tab.statusLabel.setText("Building Annabelle")
        tab._current_npc_id = "npc_b"
        tab._panel_request_counter = 2
        tab._panel_requests_by_npc = {"npc_a": 1, "npc_b": 2}
        tab._active_panel_request_id = 2

        tab._accept_panel_result(token, revision, "npc_a", 1, first)

        assert tab.copyTablePrepBtn.text() == "Copy GM Table Prep (1 NPC)"
        assert tab.statusLabel.text() == "Building Annabelle"
        assert tab._current_panel is None
        assert "npc_a" not in tab._panel_requests_by_npc
        assert tab._panel_requests_by_npc["npc_b"] == 2

        tab._accept_panel_result(token, revision, "npc_b", 1, {"name": "STALE REQUEST"})
        tab._accept_panel_result(token - 1, revision, "npc_b", 2, {"name": "STALE EVENT"})
        tab._accept_panel_result(token, "stale revision", "npc_b", 2, {"name": "STALE REVISION"})
        assert tab.copyTablePrepBtn.text() == "Copy GM Table Prep (1 NPC)"
        assert tab.statusLabel.text() == "Building Annabelle"
        assert tab._panel_requests_by_npc["npc_b"] == 2

        tab._accept_panel_result(token, revision, "npc_b", 2, second)

        assert tab.copyTablePrepBtn.text() == "Copy GM Table Prep (2 NPCs)"
        assert tab.statusLabel.text() == "NPC panel ready from AI. Relevance complete; no grounded material surfaced."

        tab._panel_request_counter = 3
        tab._panel_requests_by_npc["npc_b"] = 3
        tab._active_panel_request_id = 3
        tab._accept_panel_result(token, revision, "npc_b", 3, copy.deepcopy(second))
        assert tab.copyTablePrepBtn.text() == "Copy GM Table Prep (2 NPCs)"

        tab.copyTablePrepBtn.click()
        copied = qapp.clipboard().text()
        assert copied.count("\nNPC Panel\n") == 2
        assert tab.copyTablePrepBtn.text() == "Copy GM Table Prep (2 NPCs)"

        tab._event_generation_token += 1
        replacement_status = "Club event ready."
        tab._on_event_ready(_ui_build_result("event2"))
        assert tab.copyTablePrepBtn.text() == "Copy GM Table Prep (0 NPCs)"
        assert tab.statusLabel.text() == replacement_status

        tab._accept_panel_result(token, revision, "npc_a", 1, first)
        assert tab.copyTablePrepBtn.text() == "Copy GM Table Prep (0 NPCs)"
        assert tab.statusLabel.text() == replacement_status
    finally:
        tab.close()


def test_guest_search_filters_only_visible_roster_in_canonical_order(qapp, qtbot) -> None:
    tab = ClubTab(FakeService())
    qtbot.addWidget(tab)
    tab.resize(1100, 720)
    tab.show()
    try:
        result = _many_ui_build_result()
        attendee_ids = result.event.attendee_ids
        identity_ids = tuple(identity.npc_id for identity in result.identities)
        tab._on_event_ready(result)

        tab.guestSearchEdit.setFocus()
        qtbot.keyClicks(tab.guestSearchEdit, "  E  ")

        assert _list_ids(tab.guestList) == ["npc_1", "npc_3", "npc_4", "npc_5"]
        assert result.event.attendee_ids == attendee_ids
        assert tuple(identity.npc_id for identity in result.identities) == identity_ids

        tab.guestSearchEdit.selectAll()
        qtbot.keyClick(tab.guestSearchEdit, Qt.Key_Backspace)
        assert _list_ids(tab.guestList) == list(attendee_ids)
        assert tab.guestSearchEdit.accessibleName() == "Search club guests"
        assert tab.guestList.accessibleName() == "Club guest roster"

        tab.guestSearchEdit.setFocus()
        qtbot.waitUntil(tab.guestSearchEdit.hasFocus, timeout=1000)
        qtbot.keyClick(tab.guestSearchEdit, Qt.Key_Tab)
        qtbot.waitUntil(tab.guestList.hasFocus, timeout=1000)
        assert all(size > 0 for size in tab.mainSplitter.sizes())
    finally:
        tab.close()


def test_pin_recent_and_prepared_reopening_reuse_retained_panel(qapp, qtbot) -> None:
    service = PanelService()
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    tab.resize(1100, 720)
    tab.show()
    try:
        tab._on_event_ready(_ui_build_result())

        _click_list_row(qtbot, tab.guestList, 0)
        qtbot.waitUntil(lambda: not tab._workers, timeout=3000)
        assert tab.guestList.item(0).text() == "Damien — Prepared"
        assert tab.guestList.item(0).font().bold()
        assert _list_ids(tab.recentNpcList) == ["npc_a"]

        tab.pinNpcBtn.setFocus()
        qtbot.keyClick(tab.pinNpcBtn, Qt.Key_Space)
        assert _list_ids(tab.pinnedNpcList) == ["npc_a"]
        assert tab.pinNpcBtn.text() == "Unpin NPC"

        _click_list_row(qtbot, tab.guestList, 1)
        qtbot.waitUntil(lambda: not tab._workers, timeout=3000)
        assert _list_ids(tab.recentNpcList) == ["npc_b", "npc_a"]
        assert len(service.calls) == 2
        qtbot.mouseClick(tab.pinNpcBtn, Qt.MouseButton.LeftButton)
        assert _list_ids(tab.pinnedNpcList) == ["npc_a", "npc_b"]

        _click_list_row(qtbot, tab.pinnedNpcList, 0)
        assert tab._current_npc_id == "npc_a"
        assert _list_ids(tab.recentNpcList) == ["npc_a", "npc_b"]
        assert len(service.calls) == 2
        assert not tab._workers

        tab.recentNpcList.setCurrentRow(1)
        tab.recentNpcList.setFocus()
        qtbot.keyClick(tab.recentNpcList, Qt.Key_Return)
        assert tab._current_npc_id == "npc_b"
        assert _list_ids(tab.recentNpcList) == ["npc_b", "npc_a"]
        assert len(service.calls) == 2

        _click_list_row(qtbot, tab.pinnedNpcList, 0)
        qtbot.keyClick(tab.pinNpcBtn, Qt.Key_Space)
        assert _list_ids(tab.pinnedNpcList) == ["npc_b"]
        assert tab.pinNpcBtn.text() == "Pin NPC"
    finally:
        tab.close()


def test_recent_list_is_unique_five_entry_mru(qapp, qtbot) -> None:
    service = PanelService()
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    tab.resize(1100, 720)
    tab.show()
    try:
        result = _many_ui_build_result()
        tab._on_event_ready(result)
        for row, npc_id in enumerate(result.event.attendee_ids):
            _click_list_row(qtbot, tab.guestList, row)
            qtbot.waitUntil(
                lambda npc_id=npc_id: tab._current_npc_id == npc_id and tab._current_panel is not None,
                timeout=3000,
            )

        assert _list_ids(tab.recentNpcList) == ["npc_5", "npc_4", "npc_3", "npc_2", "npc_1"]
        assert len(service.calls) == 6

        _click_list_row(qtbot, tab.guestList, 2)
        assert _list_ids(tab.recentNpcList) == ["npc_2", "npc_5", "npc_4", "npc_3", "npc_1"]
        assert len(service.calls) == 6
        assert len(set(_list_ids(tab.recentNpcList))) == 5
    finally:
        tab.close()


def test_new_event_clears_navigation_and_search_state(qapp, qtbot) -> None:
    service = PanelService()
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    tab.show()
    try:
        tab._on_event_ready(_ui_build_result())
        _click_list_row(qtbot, tab.guestList, 0)
        qtbot.waitUntil(lambda: not tab._workers, timeout=3000)
        qtbot.mouseClick(tab.pinNpcBtn, Qt.MouseButton.LeftButton)
        qtbot.keyClicks(tab.guestSearchEdit, "Dam")

        tab._event_generation_token += 1
        tab._on_event_ready(_ui_build_result("event2"))

        assert tab.guestSearchEdit.text() == ""
        assert tab.pinnedNpcList.count() == 0
        assert tab.recentNpcList.count() == 0
        assert tab._panels_by_npc_id == {}
        assert tab._current_npc_id == ""
        assert tab._current_panel is None
        assert tab.copyTablePrepBtn.text() == "Copy GM Table Prep (0 NPCs)"
        assert all("Prepared" not in tab.guestList.item(row).text() for row in range(tab.guestList.count()))
    finally:
        tab.close()


def test_late_replacement_clears_navigation_and_rejects_obsolete_panel(qapp, qtbot) -> None:
    service = LateReplacementRaceService()
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    tab.resize(1100, 720)
    tab.show()
    try:
        tab._on_event_ready(_ui_build_result())
        _click_list_row(qtbot, tab.guestList, 1)
        qtbot.waitUntil(lambda: not tab._workers, timeout=3000)
        qtbot.mouseClick(tab.pinNpcBtn, Qt.MouseButton.LeftButton)
        qtbot.keyClicks(tab.guestSearchEdit, "Dam")

        _click_list_row(qtbot, tab.guestList, 0)
        qtbot.waitUntil(lambda: service.panel_started.is_set(), timeout=1000)
        old_token = tab._event_generation_token

        qtbot.mouseClick(tab.pickLateBtn, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: not tab._event_busy, timeout=3000)
        assert tab._event_generation_token == old_token + 1
        assert tab.guestSearchEdit.text() == "Dam"
        assert tab.pinnedNpcList.count() == 0
        assert tab.recentNpcList.count() == 0
        assert tab._panels_by_npc_id == {}

        service.release_panel.set()
        qtbot.waitUntil(lambda: not tab._workers, timeout=3000)
        assert tab._panels_by_npc_id == {}
        assert tab.pinnedNpcList.count() == 0
        assert tab.recentNpcList.count() == 0
        assert "Prepared" not in tab.guestList.item(0).text()
        assert "npc_a panel" not in tab.drawer.toPlainText()
    finally:
        service.release_panel.set()
        tab.close()


def test_stir_room_real_buttons_cycle_visible_material_without_io(qapp, qtbot) -> None:
    service = StirTrapService()
    result = _stir_build_result()
    event_before = copy.deepcopy(result.event.to_dict())
    debug_before = copy.deepcopy(result.to_debug_dict())
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    tab.show()
    try:
        assert not tab.stirRoomBtn.isEnabled()
        assert not tab.stirSuggestionLabel.isVisible()
        assert not tab.clearStirBtn.isVisible()
        tab._on_event_ready(result)
        status_before = tab.statusLabel.text()
        dashboard_before = tab.summaryBrowser.toPlainText()
        drawer_before = tab.drawer.toPlainText()
        thread_checks: list[bool] = []
        tab.stirRoomBtn.clicked.connect(lambda _checked=False: thread_checks.append(QThread.currentThread() == qapp.thread()))

        expected = [
            "Optional GM suggestion: You could shift the spotlight to Group 1 (Ada and Bea).",
            "Optional GM suggestion: You could let this selected rumor become audible nearby: The missing ledger may be changing hands.",
            "Optional GM suggestion: You could have Ada ask a player character for a private word.",
            "Optional GM suggestion: You could bring Late into the room now as the late arrival.",
            "Optional GM suggestion: You could let Groups 1 and 2 briefly share the room's attention.",
            "Optional GM suggestion: You could make Evie easy for the players to approach.",
            "Optional GM suggestion: You could shift the spotlight to Group 2 (Cy and Dee).",
        ]
        observed = []
        for suggestion in expected:
            qtbot.mouseClick(tab.stirRoomBtn, Qt.MouseButton.LeftButton)
            observed.append(tab.stirSuggestionLabel.text())
            assert tab.stirSuggestionLabel.isVisible()
            assert tab.clearStirBtn.isVisible()
            assert tab.stirRoomBtn.text() == "Next suggestion"
            assert tab.stirSuggestionLabel.text() == suggestion

        assert observed == expected
        assert thread_checks == [True] * len(expected)
        assert tab.statusLabel.text() == status_before
        assert tab.summaryBrowser.toPlainText() == dashboard_before
        assert tab.drawer.toPlainText() == drawer_before
        assert result.event.to_dict() == event_before
        assert result.to_debug_dict() == debug_before
        assert service.calls == []
        assert service.provider.calls == []
        assert service.cache.writes == 0
        assert tab._threads == []
        assert tab._workers == []
    finally:
        tab.close()


def test_stir_room_clear_continues_and_view_changes_reset(qapp, qtbot) -> None:
    tab = ClubTab(StirTrapService())
    qtbot.addWidget(tab)
    tab.show()
    try:
        result = _stir_build_result()
        tab._on_event_ready(result)
        qtbot.mouseClick(tab.stirRoomBtn, Qt.MouseButton.LeftButton)
        first = tab.stirSuggestionLabel.text()

        qtbot.mouseClick(tab.clearStirBtn, Qt.MouseButton.LeftButton)
        assert not tab.stirSuggestionLabel.isVisible()
        assert not tab.clearStirBtn.isVisible()
        assert tab.stirRoomBtn.text() == "Stir the Room"
        qtbot.mouseClick(tab.stirRoomBtn, Qt.MouseButton.LeftButton)
        assert tab.stirSuggestionLabel.text() != first
        assert "selected rumor" in tab.stirSuggestionLabel.text()

        current = tab.stirSuggestionLabel.text()
        tab._on_event_ready(result)
        assert tab.stirSuggestionLabel.isVisible()
        assert tab.stirSuggestionLabel.text() == current
        assert tab.stirRoomBtn.text() == "Next suggestion"

        tab._on_event_ready(_stir_build_result(event_id="new-event"))
        assert not tab.stirSuggestionLabel.isVisible()
        assert not tab.clearStirBtn.isVisible()
        assert tab.stirRoomBtn.text() == "Stir the Room"
        qtbot.mouseClick(tab.stirRoomBtn, Qt.MouseButton.LeftButton)
        assert tab.stirSuggestionLabel.text() == first

        replacement = _stir_build_result(event_id="new-event", late_arrival_id="npc_e")
        tab._on_event_ready(replacement)
        assert not tab.stirSuggestionLabel.isVisible()
        assert tab.stirRoomBtn.text() == "Stir the Room"

        qtbot.mouseClick(tab.stirRoomBtn, Qt.MouseButton.LeftButton)
        tab._on_event_ready(_stir_build_result(event_id="new-event", late_arrival_id="npc_e", rumor="A different admitted rumor."))
        assert not tab.stirSuggestionLabel.isVisible()
        assert tab.stirRoomBtn.text() == "Stir the Room"
    finally:
        tab.close()


def test_stir_room_uses_plain_text_and_disables_without_admitted_material(qapp, qtbot) -> None:
    service = StirTrapService()
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    tab.show()
    try:
        identities = [_identity("npc_late", "Late <b>Guest</b>")]
        event = ClubEvent(
            event_id="plain-text",
            attendee_ids=("npc_late",),
            late_arrival_id="npc_late",
            cache_key="plain-cache",
            seed=0,
            dashboard={
                "event": {"venue": "The Lantern Room"},
                "rumors_in_circulation": ["<script>Not markup</script> & still only a rumor."],
            },
        )
        tab._on_event_ready(_build_result(event, identities, [_index(identities[0])]))
        qtbot.mouseClick(tab.stirRoomBtn, Qt.MouseButton.LeftButton)
        assert tab.stirSuggestionLabel.textFormat() == Qt.TextFormat.PlainText
        assert tab.stirSuggestionLabel.text() == (
            "Optional GM suggestion: You could let this selected rumor become audible nearby: "
            "<script>Not markup</script> & still only a rumor."
        )

        empty = ClubEvent(
            event_id="empty",
            attendee_ids=(),
            late_arrival_id="",
            cache_key="empty-cache",
            seed=0,
            dashboard={"event": {"venue": "The Lantern Room"}},
        )
        tab._on_event_ready(_build_result(empty, [], []))
        assert not tab.stirRoomBtn.isEnabled()
        assert not tab.stirSuggestionLabel.isVisible()
        assert not tab.clearStirBtn.isVisible()
        assert service.calls == []
        assert service.provider.calls == []
        assert service.cache.writes == 0
    finally:
        tab.close()


def test_stir_room_rejects_nonmembers_and_late_arrival_from_present_rows(qapp, qtbot) -> None:
    service = StirTrapService()
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    tab.show()
    try:
        identities = [
            _identity("admitted", "Ada"),
            _identity("late", "Late"),
            _identity("rogue", "Rogue"),
        ]
        event = ClubEvent(
            event_id="membership-mismatch",
            attendee_ids=("admitted", "late"),
            late_arrival_id="late",
            cache_key="membership-cache",
            seed=0,
            dashboard={
                "event": {"venue": "The Lantern Room"},
                "rumors_in_circulation": [],
                "scene_prep": {
                    "encounters": [
                        {"number": 1, "members": ["rogue"], "availability": "present"},
                        {"number": 2, "members": ["late"], "availability": "present"},
                        {"number": 3, "members": ["admitted"], "availability": "present"},
                    ],
                },
            },
        )
        result = _build_result(event, identities, [_index(identity) for identity in identities])
        before = copy.deepcopy(result.event.to_dict())
        tab._on_event_ready(result)

        expected = [
            "Optional GM suggestion: You could have Ada ask a player character for a private word.",
            "Optional GM suggestion: You could bring Late into the room now as the late arrival.",
            "Optional GM suggestion: You could make Ada easy for the players to approach.",
        ]
        observed = []
        for _index_value in range(6):
            qtbot.mouseClick(tab.stirRoomBtn, Qt.MouseButton.LeftButton)
            observed.append(tab.stirSuggestionLabel.text())

        assert observed == expected * 2
        assert all("Rogue" not in suggestion for suggestion in observed)
        assert all("have Late ask" not in suggestion for suggestion in observed)
        assert all("make Late easy" not in suggestion for suggestion in observed)
        assert result.event.to_dict() == before
        assert service.calls == []
        assert service.provider.calls == []
        assert service.cache.writes == 0
    finally:
        tab.close()


def test_non_current_panel_error_does_not_replace_visible_status_or_count(qapp) -> None:
    tab = ClubTab(FakeService())
    try:
        tab._on_event_ready(_ui_build_result())
        tab._current_npc_id = "npc_b"
        tab._panel_request_counter = 2
        tab._panel_requests_by_npc = {"npc_a": 1, "npc_b": 2}
        tab.statusLabel.setText("Building Annabelle")

        tab._panel_error(
            tab._event_generation_token,
            tab._prep_revision,
            "npc_a",
            1,
            "DeepSeek leaked api_key=sk-test-secret",
        )

        assert tab.statusLabel.text() == "Building Annabelle"
        assert tab.copyTablePrepBtn.text() == "Copy GM Table Prep (0 NPCs)"
        assert "npc_a" not in tab._panel_requests_by_npc
        assert tab._panel_requests_by_npc["npc_b"] == 2
    finally:
        tab.close()


def test_current_panel_worker_error_uses_stable_visible_message(qapp, qtbot) -> None:
    tab = ClubTab(FailingPanelService())
    qtbot.addWidget(tab)
    try:
        tab._on_event_ready(_ui_build_result())

        tab.guestList.itemClicked.emit(tab.guestList.item(0))

        qtbot.waitUntil(lambda: not tab._workers, timeout=3000)
        assert tab.statusLabel.text() == "NPC panel unavailable."
        assert tab.copyTablePrepBtn.text() == "Copy GM Table Prep (0 NPCs)"
        assert "DeepSeek" not in tab.statusLabel.text()
        assert "sk-test-secret" not in tab.statusLabel.text()
    finally:
        tab.close()


def test_club_tab_click_uses_ai_panel_worker_by_default(qapp, qtbot) -> None:
    service = PanelService()
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    try:
        event = ClubEvent(
            event_id="event1",
            attendee_ids=("npc_a", "npc_b"),
            late_arrival_id="npc_b",
            cache_key="cache",
            seed=1,
            dashboard={
                "event": {"venue": "The Lantern Room"},
                "first_impression": "",
                "social_map": [],
                "notable_details": [],
                "rumors": [],
                "possible_drama": [],
                "interesting_connections": [],
                "unresolved_business": [],
                "opportunities": [],
                "top_connections": [],
                "background_details": [],
            },
        )
        identities = [_identity("npc_a", "Damien"), _identity("npc_b", "Annabelle")]
        tab._on_event_ready(_build_result(event, identities, [_index(identity) for identity in identities], [_relationship()]))

        tab._run_panel_worker("npc_a")

        qtbot.waitUntil(lambda: not tab._workers, timeout=3000)
        assert service.calls == [{"npc_id": "npc_a", "use_ai": True, "force": False}]
        assert "Damien distrusts Annabelle." in tab.drawer.toPlainText()
        assert tab.statusLabel.text() == "NPC panel ready from AI."

        tab.regenerate_current_npc_panel()

        qtbot.waitUntil(lambda: len(service.calls) == 2 and not tab._workers, timeout=3000)
        assert service.calls[-1] == {"npc_id": "npc_a", "use_ai": True, "force": True}
    finally:
        tab.close()


def test_club_tab_panel_renders_balanced_play_card_without_visible_why(qapp) -> None:
    tab = ClubTab(FakeService())
    try:
        event = ClubEvent(
            event_id="event1",
            attendee_ids=("npc_a", "npc_b"),
            late_arrival_id="npc_b",
            cache_key="cache",
            seed=1,
            dashboard={"event": {"venue": "The Lantern Room"}},
        )
        identities = [_identity("npc_a", "Damien <script>"), _identity("npc_b", "Annabelle")]
        tab._on_event_ready(_build_result(event, identities, [_index(identity) for identity in identities], [_relationship()]))
        item = _relationship("Immutable <summary> from sheet.")
        item["prep_text"] = "AI table phrase <b>escaped</b>."
        item["sources"] = [
            {
                "source_id": "source<script>",
                "character_id": "npc_a",
                "path": "C:/vault/Damien <raw>.md",
                "section": "Character <Relationships>",
                "excerpt": "Raw <excerpt> from vault.",
            }
        ]
        panel = {
            "npc_id": "npc_a",
            "name": "Damien <script>",
            "identity": {"affiliation": "Dockworkers <b>"},
            "personality": [],
            "current_read": "Watchful <now>.",
            "tonight": {"current_demeanor": "Keep the delivery <quiet> and controlled.", "current_desire": item},
            "people_here": [item],
            "likely_conversation": ["Ask about <ledger>."],
            "conversation": {"likely_subjects": [item], "sensitive": []},
            "sensitive_subjects": [],
            "interesting_detail": item,
            "useful_hook": "Press the <hook>.",
            "presentation": {
                "play_cue": "Keep the delivery <quiet> and controlled.",
                "agenda": {"item_id": item["item_id"], "text": "Advance the <ledger> plan."},
                "people_here": [{"item_id": item["item_id"], "text": "Treat Annabelle as useful but dangerous."}],
                "if_approached": [{"item_id": item["item_id"], "text": "Deflect questions about the <ledger>."}],
                "keep_guarded": [],
                "hook": {"item_id": item["item_id"], "text": "Press the <hook>."},
            },
            "metadata": {"generation_mode": "ai", "from_cache": False},
        }
        tab._current_npc_id = "npc_a"
        tab._current_panel = panel

        html_text = tab._panel_html("npc_a", panel)

        assert "Right Now" in html_text
        assert "Play Them" in html_text
        assert "Agenda Tonight" in html_text
        assert "Who Matters Here" in html_text
        assert "If Approached" in html_text
        assert "Keep Guarded" in html_text
        assert "Pressure / Hook" in html_text
        assert "&lt;script&gt;" in html_text
        assert "Keep the delivery &lt;quiet&gt;" in html_text
        assert "Advance the &lt;ledger&gt; plan." in html_text
        assert "Treat Annabelle as useful but dangerous." in html_text
        assert "<script>" not in html_text
        assert "<b>escaped</b>" not in html_text
        assert "Why?" not in html_text
        assert "why:" not in html_text
        assert "Immutable <summary> from sheet." not in html_text
    finally:
        tab.close()


def test_club_tab_has_no_visible_provenance_anchor_or_toggle_state(qapp) -> None:
    service = PanelService()
    tab = ClubTab(service)
    try:
        event = ClubEvent(
            event_id="event1",
            attendee_ids=("npc_a", "npc_b"),
            late_arrival_id="npc_b",
            cache_key="cache",
            seed=1,
            dashboard={"event": {"venue": "The Lantern Room"}},
        )
        identities = [_identity("npc_a", "Damien"), _identity("npc_b", "Annabelle")]
        tab._on_event_ready(_build_result(event, identities, [_index(identity) for identity in identities], [_relationship()]))
        panel = PanelService().build_npc_panel_from_result(tab._build_result, "npc_a", use_ai=True)
        tab._current_npc_id = "npc_a"
        tab._current_panel = panel
        rendered = tab._panel_html("npc_a", panel)
        tab.drawer.setHtml(rendered)
        tab._on_anchor_clicked(QUrl("https://example.invalid"))
        tab._on_anchor_clicked(QUrl("npc:not_an_attendee"))

        assert "Why?" not in rendered
        assert "why:" not in rendered
        assert not hasattr(tab, "_why_targets_by_token")
        assert not hasattr(tab, "_expanded_why_keys")
        assert service.calls == []
        assert not tab._workers
    finally:
        tab.close()


def test_club_tab_dedupes_duplicate_panel_clicks_while_loading(qapp, qtbot) -> None:
    service = BlockingPanelService()
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    try:
        event = ClubEvent(
            event_id="event1",
            attendee_ids=("npc_a", "npc_b"),
            late_arrival_id="npc_b",
            cache_key="cache",
            seed=1,
            dashboard={
                "event": {"venue": "The Lantern Room"},
                "first_impression": "",
                "social_map": [],
                "notable_details": [],
                "rumors": [],
                "possible_drama": [],
                "interesting_connections": [],
                "unresolved_business": [],
                "opportunities": [],
                "top_connections": [],
                "background_details": [],
            },
        )
        identities = [_identity("npc_a", "Damien"), _identity("npc_b", "Annabelle")]
        tab._on_event_ready(_build_result(event, identities, [_index(identity) for identity in identities], [_relationship()]))

        tab._run_panel_worker("npc_a")
        qtbot.waitUntil(lambda: service.started.is_set(), timeout=1000)
        tab._run_panel_worker("npc_a")

        assert service.calls == [{"npc_id": "npc_a", "use_ai": True, "force": False}]
        assert "already building" in tab.statusLabel.text()

        service.release.set()
        qtbot.waitUntil(lambda: not tab._workers, timeout=3000)
    finally:
        service.release.set()
        tab.close()


def test_club_tab_copy_debug_buttons_put_json_on_clipboard(qapp) -> None:
    service = FakeService()
    tab = ClubTab(service)
    try:
        event = ClubEvent(
            event_id="event1",
            attendee_ids=("npc_a", "npc_b"),
            late_arrival_id="npc_b",
            cache_key="cache",
            seed=1,
            dashboard={
                "event": {"venue": "The Lantern Room"},
                "first_impression": "The room hums.",
                "social_map": [],
                "notable_details": [],
                "rumors": [],
                "possible_drama": [],
                "interesting_connections": [],
                "unresolved_business": [],
                "opportunities": [],
                "top_connections": [],
                "background_details": [],
            },
        )
        identities = [_identity("npc_a", "Damien"), _identity("npc_b", "Annabelle")]
        tab._on_event_ready(_build_result(event, identities, [_index(identity) for identity in identities], [_relationship()]))
        assert tab._build_result is not None
        tab._build_result.debug_context["api_key"] = "do-not-copy"

        tab.copy_club_debug_context()
        assert '"attendees"' in qapp.clipboard().text()
        assert "do-not-copy" not in qapp.clipboard().text()

        tab._current_npc_id = "npc_a"
        tab._current_panel = service.build_npc_panel_from_result(tab._build_result, "npc_a", use_ai=True)
        before_panel = copy.deepcopy(tab._current_panel)
        before_build_result = copy.deepcopy(tab._build_result.to_debug_dict())
        before_panel_calls = service.panel_calls

        tab.copy_npc_debug_context()
        first_payload = json.loads(qapp.clipboard().text())
        tab.copy_npc_debug_context()
        second_payload = json.loads(qapp.clipboard().text())

        assert first_payload == second_payload
        assert service.panel_calls == before_panel_calls
        assert tab._current_panel == before_panel
        assert tab._build_result.to_debug_dict() == before_build_result

        assert first_payload["npc_id"] == "npc_a"
        assert {"npc_id", "panel", "panel_skeleton", "sources", "source_map", "event_metadata", "event", "attendees"} <= set(
            first_payload
        )
        skeleton = first_payload["panel_skeleton"]
        assert {"npc_id", "name", "current_read", "people_here", "conversation", "tonight", "interesting_detail", "source_map"} <= set(
            skeleton
        )
        assert skeleton["npc_id"] == "npc_a"
        assert skeleton["name"] == "Damien"
        assert skeleton["source_map"]["s"]["target_npc_id"] == "npc_b"
        assert first_payload["source_map"] == skeleton["source_map"]
        assert first_payload["sources"]["s"]["target_npc_id"] == "npc_b"
        assert first_payload["event"] == {
            "event_id": "event1",
            "cache_key": "cache",
            "seed": 1,
            "attendee_ids": ["npc_a", "npc_b"],
            "late_arrival_id": "npc_b",
        }
        assert first_payload["event_metadata"] == {}
        assert first_payload["panel"]["metadata"]["generation_mode"] == "deterministic"
        assert first_payload["attendees"] == [
            {"npc_id": "npc_a", "name": "Damien", "path": "C:/vault/Damien.md"},
            {"npc_id": "npc_b", "name": "Annabelle", "path": "C:/vault/Annabelle.md"},
        ]
    finally:
        tab.close()


def test_club_tab_agenda_renders_stored_text_without_visible_provenance(qapp) -> None:
    tab = ClubTab(FakeService())
    try:
        shared_summary = "Damien is watching the same visible concern."
        other = _relationship(shared_summary)
        focus = copy.deepcopy(other)
        focus["item_id"] = "focus-item"
        focus["type"] = "unresolved_business"
        focus["section"] = "current_desire"
        focus["summary"] = shared_summary
        focus["prep_text"] = "May be focused on this grounded concern — Damien is watching the same visible concern."
        focus["sources"] = [
            {
                "source_id": "focus-source",
                "character_id": "npc_a",
                "path": "C:/vault/Focus.md",
                "section": "Unresolved Business",
                "excerpt": shared_summary,
            }
        ]
        panel = {
            "npc_id": "npc_a",
            "name": "Damien",
            "identity": {"affiliation": "Dockworkers"},
            "personality": [],
            "current_read": "Damien remains watchful.",
            "tonight": {"current_desire": focus},
            "people_here": [other],
            "likely_conversation": [],
            "conversation": {"likely_subjects": [], "sensitive": []},
            "sensitive_subjects": [],
            "interesting_detail": None,
            "useful_hook": "",
        }
        tab._current_npc_id = "npc_a"
        tab._current_panel = panel

        rendered = tab._panel_html("npc_a", panel)

        assert "Agenda Tonight" in rendered
        assert focus["prep_text"] in rendered
        assert "Why?" not in rendered
        assert "focus-source" not in rendered
    finally:
        tab.close()
