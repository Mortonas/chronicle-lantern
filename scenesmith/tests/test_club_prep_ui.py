from __future__ import annotations

from PySide6.QtCore import QThread, QUrl
from PySide6.QtWidgets import QApplication

from core.club_generation import ClubGenerationService
from test_club_prep import SceneProvider
from ui.club_tab import ClubTab


def prepared_event(tmp_path):
    paths = []
    for name in ("Ada", "Bea", "Cy"):
        p = tmp_path / (name + ".md")
        p.write_text(f"# {name}\n\nStatus: Influential\n\nPublicly supports the shelter.\n", encoding="utf-8")
        paths.append(str(p))
    provider = SceneProvider()
    service = ClubGenerationService({}, cache_root=tmp_path / "cache", vault_root=tmp_path, provider=provider)
    return service, provider, service.build_event_result(paths, seed=4)


def test_real_late_arrival_and_copy_buttons_swap_consistent_snapshot(tmp_path, qapp, qtbot):
    service, provider, result = prepared_event(tmp_path)
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    tab._on_event_ready(result)
    old_late = result.event.late_arrival_id
    tab.drawer.setHtml("<p>Previous arrangement</p>")
    rumor_snapshot = result.event.dashboard["rumors_in_circulation"]
    calls = len(provider.calls)
    thread_checks = []
    original = tab._on_event_ready
    def observe(value):
        thread_checks.append(QThread.currentThread() == qapp.thread())
        original(value)
    tab._on_event_ready = observe
    tab.pickLateBtn.click()
    assert tab._event.late_arrival_id == old_late
    assert "Previous arrangement" in tab.drawer.toPlainText()
    assert not tab.pickLateBtn.isEnabled()
    qtbot.waitUntil(lambda: not tab._event_busy, timeout=5000)
    assert thread_checks == [True]
    assert tab._event.late_arrival_id != old_late
    assert "Previous arrangement" not in tab.drawer.toPlainText()
    assert tab._event.dashboard["rumors_in_circulation"] == rumor_snapshot
    assert tab._event.seed == result.event.seed
    assert tab._event.dashboard["scene_prep"]["encounters"][-1]["members"] == [tab._event.late_arrival_id]
    assert len(provider.calls) == calls + 2
    tab.copyTablePrepBtn.click()
    copied = QApplication.clipboard().text()
    assert "Social Groups and Loners" in copied
    assert "3. " in copied and "Not here yet" in copied
    assert "source_id" not in copied and "evidence" not in copied
    qtbot.waitUntil(lambda: not tab._threads, timeout=5000)


def test_real_guest_link_uses_cached_panel_and_stale_result_keeps_new_request(tmp_path, qapp, qtbot):
    service, provider, result = prepared_event(tmp_path)
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    tab._on_event_ready(result)
    npc = result.event.attendee_ids[0]
    tab.summaryBrowser.anchorClicked.emit(QUrl("npc:" + npc))
    qtbot.waitUntil(lambda: tab._current_panel is not None, timeout=5000)
    assert "Who Matters Here" in tab.drawer.toPlainText()
    assert tab._current_panel["who_matters"]
    assert tab.copyTablePrepBtn.text() == "Copy GM Table Prep (1 NPC)"
    request = next(p for p in reversed(provider.calls) if "arrangement" in p)
    assert "gm_notes" not in request["arrangement"]
    assert "opening" not in request["arrangement"]
    count = len(provider.calls)
    qtbot.waitUntil(lambda: not tab._threads, timeout=5000)
    tab.summaryBrowser.anchorClicked.emit(QUrl("npc:" + npc))
    qtbot.waitUntil(lambda: not tab._threads, timeout=5000)
    assert len(provider.calls) == count
    ready_status = tab.statusLabel.text()
    tab._panel_requests_by_npc[npc] = 777
    panel = tab._current_panel
    tab._accept_panel_result(tab._event_generation_token - 1, tab._prep_revision, npc, 1, {"name": "STALE"})
    tab._accept_panel_result(tab._event_generation_token, "old revision", npc, 1, {"name": "STALE"})
    tab._on_panel_ready(npc, 1, {"name": "STALE"})
    assert tab._panel_requests_by_npc[npc] == 777
    assert tab._current_panel == panel
    assert "STALE" not in tab.drawer.toPlainText()
    assert tab.copyTablePrepBtn.text() == "Copy GM Table Prep (1 NPC)"
    assert tab.statusLabel.text() == ready_status


def test_retry_button_reuses_completed_readings_and_keeps_rumors(tmp_path, qapp, qtbot):
    service, provider, result = prepared_event(tmp_path)
    # Corrupt only an interpretation section: successful readings remain reusable.
    for path in (tmp_path / "cache" / "prep_sections").glob("*_opening.json"):
        path.write_text("{}", encoding="utf-8")
    result.event.dashboard["scene_prep"]["incomplete"] = True
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    tab._on_event_ready(result)
    count = len(provider.calls)
    assert tab.retryPrepBtn.isEnabled()
    tab.retryPrepBtn.click()
    qtbot.waitUntil(lambda: not tab._event_busy, timeout=5000)
    assert len(provider.calls) == count + 2
    assert provider.calls[-1]["sections"] == ["opening"]
    assert provider.calls[-1]["accepted_sections"]["gm_notes"] == result.event.dashboard["scene_prep"]["gm_notes"]
    assert tab._event.dashboard["scene_prep"]["opening"] == result.event.dashboard["scene_prep"]["opening"]
    assert "Social Groups and Loners" in tab.summaryBrowser.toPlainText()
    assert tab._event.seed == result.event.seed
    assert not tab.retryPrepBtn.isEnabled()
    qtbot.waitUntil(lambda: not tab._threads, timeout=5000)
