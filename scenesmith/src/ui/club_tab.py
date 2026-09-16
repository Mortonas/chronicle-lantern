from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Sequence

from PySide6.QtCore import QObject, QThread, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from core.club_generation import (
    ClubBuildResult,
    ClubGenerationError,
    ClubGenerationService,
    build_npc_panel_skeleton,
    clean_display_text,
    safe_debug_json,
)
from core.club_prep import (
    conversation_opening_display_rows, encounter_cue_detail_lines, prep_references,
    relevance_display_rows, rumor_guidance_display_rows, scene_display_sections,
)

CLUB_WORKER_TIMEOUT_MS = 45_000
RECENT_NPC_LIMIT = 5


@dataclass(frozen=True)
class RumorPoolRow:
    text: str
    source_name: str = ""
    source_section: str = ""


@dataclass(frozen=True)
class _ClubEventReadiness:
    state: str
    actionable: bool
    incomplete: bool
    status: str
    export_status: str = ""
    copy_status: str = "GM table prep copied."


_BASELINE_PENDING_STATUS = (
    "Roster and groups are ready. Full event prep is still building; "
    "NPC prep and GM export will unlock when it settles."
)
_BASELINE_FAILED_STATUS = (
    "Roster and groups remain visible, but full event prep did not finish. "
    "NPC prep and GM export remain unavailable; use Retry Incomplete Prep."
)


def _club_event_readiness(
    event: Any,
    *,
    pending: bool = False,
    baseline_failed: bool = False,
) -> _ClubEventReadiness:
    if pending:
        return _ClubEventReadiness(
            state="pending",
            actionable=False,
            incomplete=True,
            status=_BASELINE_FAILED_STATUS if baseline_failed else _BASELINE_PENDING_STATUS,
        )
    if event is None:
        return _ClubEventReadiness("empty", False, False, "Generate a guest list, then create a club event.")

    metadata = event.metadata if isinstance(getattr(event, "metadata", None), dict) else {}
    mode = str(metadata.get("generation_mode") or "")
    failure = str(metadata.get("ai_error_type") or "")
    if mode == "partial_ai":
        return _ClubEventReadiness(
            state="partial_ai",
            actionable=True,
            incomplete=True,
            status=(
                "Club event available with incomplete partial AI prep. "
                "Rumors and completed sections are available; use Retry Incomplete Prep."
            ),
            export_status="Prep Status: INCOMPLETE — partial AI prep; some analysis was unavailable.",
            copy_status="Incomplete partial-AI GM table prep copied; unavailable sections remain marked.",
        )
    if bool(metadata.get("fallback_used")) or mode == "deterministic_fallback":
        suffix = f" AI failed: {_human_failure_label(failure)}." if failure else ""
        return _ClubEventReadiness(
            state="fallback",
            actionable=True,
            incomplete=True,
            status=(
                "Club event available with incomplete deterministic fallback."
                f"{suffix} Use Retry Incomplete Prep."
            ),
            export_status="Prep Status: INCOMPLETE — deterministic fallback; AI event prep did not complete.",
            copy_status="Incomplete deterministic-fallback GM table prep copied; unavailable sections remain marked.",
        )
    if _event_prep_incomplete(event):
        return _ClubEventReadiness(
            state="incomplete",
            actionable=True,
            incomplete=True,
            status="Club event available with incomplete prep; use Retry Incomplete Prep.",
            export_status="Prep Status: INCOMPLETE — some event analysis was unavailable.",
            copy_status="Incomplete GM table prep copied; unavailable sections remain marked.",
        )
    return _ClubEventReadiness(
        state="complete",
        actionable=True,
        incomplete=False,
        status=_status_for_metadata("Club event", metadata),
    )


def _stir_room_name(attendees: dict[str, dict[str, str]], npc_id: str) -> str:
    attendee = attendees.get(npc_id)
    if not isinstance(attendee, dict):
        return ""
    name = attendee.get("name")
    return name.strip() if isinstance(name, str) else ""


def _stir_room_names(names: Sequence[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _stir_room_candidate_pools(event: Any, attendees: dict[str, dict[str, str]]) -> tuple[tuple[str, ...], ...]:
    dashboard = event.dashboard if hasattr(event, "dashboard") and isinstance(event.dashboard, dict) else {}
    scene = dashboard.get("scene_prep") if isinstance(dashboard.get("scene_prep"), dict) else {}
    raw_encounters = scene.get("encounters") if isinstance(scene.get("encounters"), list) else []
    raw_attendee_order = getattr(event, "attendee_ids", ())
    attendee_order = tuple(
        npc_id for npc_id in raw_attendee_order if isinstance(npc_id, str) and npc_id
    ) if isinstance(raw_attendee_order, (list, tuple)) else ()
    admitted_ids = set(attendee_order)
    raw_late_arrival_id = getattr(event, "late_arrival_id", "")
    late_arrival_id = raw_late_arrival_id if isinstance(raw_late_arrival_id, str) else ""
    present_groups: list[tuple[int, tuple[str, ...], tuple[str, ...]]] = []
    present_singletons: list[tuple[str, str]] = []
    present_ids: set[str] = set()
    for row in raw_encounters:
        if not isinstance(row, dict) or row.get("availability") != "present":
            continue
        number = row.get("number")
        members = row.get("members")
        if type(number) is not int or number < 1 or not isinstance(members, list) or not members:
            continue
        if any(not isinstance(npc_id, str) or not npc_id for npc_id in members) or len(set(members)) != len(members):
            continue
        if any(npc_id not in admitted_ids for npc_id in members) or late_arrival_id in members:
            continue
        names = tuple(_stir_room_name(attendees, npc_id) for npc_id in members)
        if any(not name for name in names):
            continue
        present_ids.update(members)
        if len(members) > 1:
            present_groups.append((number, tuple(members), names))
        else:
            present_singletons.append((members[0], names[0]))

    spotlight = tuple(
        f"You could shift the spotlight to Group {number} ({_stir_room_names(names)})."
        for number, _members, names in present_groups
    )
    raw_rumors = dashboard.get("rumors_in_circulation")
    rumors = tuple(
        f"You could let this selected rumor become audible nearby: {text.strip()}"
        for text in raw_rumors if isinstance(text, str) and text.strip()
    ) if isinstance(raw_rumors, list) else ()
    private_words = tuple(
        f"You could have {name} ask a player character for a private word."
        for npc_id in attendee_order
        if isinstance(npc_id, str) and npc_id in present_ids and (name := _stir_room_name(attendees, npc_id))
    )
    late_name = _stir_room_name(attendees, late_arrival_id) if late_arrival_id in admitted_ids else ""
    late_arrival = (f"You could bring {late_name} into the room now as the late arrival.",) if late_name else ()
    shared_attention = tuple(
        f"You could let Groups {left[0]} and {right[0]} briefly share the room's attention."
        for index, left in enumerate(present_groups)
        for right in present_groups[index + 1:]
    )
    approaches = tuple(
        f"You could make {name} easy for the players to approach."
        for _npc_id, name in present_singletons
    )
    return spotlight, rumors, private_words, late_arrival, shared_attention, approaches


def _stir_room_seed(event: Any) -> int:
    try:
        return int(event.seed)
    except (AttributeError, TypeError, ValueError):
        return 0


def _stir_room_view_key(
    event: Any,
    attendees: dict[str, dict[str, str]],
    pools: tuple[tuple[str, ...], ...],
) -> tuple[Any, ...]:
    attendee_order = getattr(event, "attendee_ids", ())
    attendee_names = tuple(
        (npc_id, _stir_room_name(attendees, npc_id))
        for npc_id in attendee_order
        if isinstance(npc_id, str)
    )
    return (
        str(getattr(event, "event_id", "") or ""),
        _stir_room_seed(event),
        str(getattr(event, "late_arrival_id", "") or ""),
        attendee_names,
        pools,
    )


def _stir_room_suggestion(pools: tuple[tuple[str, ...], ...], *, seed: int, counter: int) -> str:
    available = tuple(pool for pool in pools if pool)
    if not available:
        return ""
    category_count = len(available)
    pool = available[(seed % category_count + counter) % category_count]
    return pool[(seed + counter // category_count) % len(pool)]


class ClubEventWorker(QObject):
    finished = Signal(int, object)
    error = Signal(int, str)
    progress = Signal(int, str)
    baseline = Signal(int, object)

    def __init__(self, service: ClubGenerationService, guest_paths: Sequence[str], host_path: str | None, rumor_limit: int) -> None:
        super().__init__()
        self.service = service
        self.guest_paths = list(guest_paths)
        self.host_path = host_path
        self.rumor_limit = rumor_limit
        self.token = 0

    @Slot()
    def run(self) -> None:
        try:
            self.progress.emit(self.token, "Resolving attendees and building club indexes...")
            result = self.service.build_event_result(self.guest_paths, host_path=self.host_path, rumor_limit=self.rumor_limit, use_ai=True,
                                                     progress=lambda text: self.progress.emit(self.token, text),
                                                     baseline_ready=lambda result: self.baseline.emit(self.token, result))
            self.finished.emit(self.token, result)
        except Exception as exc:  # pylint: disable=broad-except
            self.error.emit(self.token, str(exc))


class ClubPrepWorker(QObject):
    finished = Signal(int, object)
    error = Signal(int, str)
    progress = Signal(int, str)

    def __init__(self, service, result, host_path, *, change_late=False):
        super().__init__()
        self.service, self.result, self.host_path = service, result, host_path
        self.change_late = change_late
        self.token = 0

    @Slot()
    def run(self):
        try:
            progress = lambda text: self.progress.emit(self.token, text)
            if self.change_late:
                result = self.service.change_late_arrival_prep(self.result, host_path=self.host_path, progress=progress)
            else:
                result = self.service.complete_event_prep(self.result, progress=progress)
            self.finished.emit(self.token, result)
        except Exception:
            self.error.emit(self.token, "Club prep could not finish. The previous event remains available.")


class ClubPanelWorker(QObject):
    finished = Signal(int, str, str, int, object)
    error = Signal(int, str, str, int, str)
    progress = Signal(int, str, str)

    def __init__(
        self,
        service: ClubGenerationService,
        build_result: ClubBuildResult,
        npc_id: str,
        request_id: int,
        *,
        force: bool = False,
    ) -> None:
        super().__init__()
        self.service = service
        self.build_result = build_result
        self.npc_id = npc_id
        self.request_id = request_id
        self.force = force
        self.token = 0
        self.revision = ""

    @Slot()
    def run(self) -> None:
        try:
            self.progress.emit(self.token, self.revision, "Building grounded NPC panel...")
            self.finished.emit(
                self.token,
                self.revision,
                self.npc_id,
                self.request_id,
                self.service.build_npc_panel_from_result(
                    self.build_result,
                    self.npc_id,
                    use_ai=True,
                    force=self.force,
                ),
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.error.emit(self.token, self.revision, self.npc_id, self.request_id, str(exc))


class _ClubWorkerThread(QThread):
    """Run one retained worker and finish naturally when its blocking call returns."""

    def __init__(self, worker: QObject, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.worker = worker

    def run(self) -> None:
        self.worker.run()


class ClubTab(QWidget):
    def __init__(self, service: ClubGenerationService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self._event: ClubEvent | None = None
        self._build_result: ClubBuildResult | None = None
        self._guest_paths: list[str] = []
        self._host_path: str | None = None
        self._attendees: dict[str, dict[str, str]] = {}
        self._current_npc_id = ""
        self._current_panel: dict[str, Any] | None = None
        self._panels_by_npc_id: dict[str, dict[str, Any]] = {}
        self._pinned_npc_ids: list[str] = []
        self._recent_npc_ids: list[str] = []
        self._active_panel_request_id: int | None = None
        self._roster_identity: tuple[str, tuple[str, ...]] | None = None
        self._threads: list[QThread] = []
        self._workers: list[QObject] = []
        self._workers_by_thread: dict[QThread, QObject] = {}
        self._panel_request_counter = 0
        self._panel_requests_by_npc: dict[str, int] = {}
        self._last_generated_rumor_limit = 5
        self._event_generation_token = 0
        self._prep_revision = ""
        self._event_busy = False
        self._pending_event_token: int | None = None
        self._baseline_event_token: int | None = None
        self._failed_baseline_token: int | None = None
        self._settled_event_token: int | None = None
        self._stir_counter = 0
        self._stir_view_key: tuple[Any, ...] | None = None
        self._stir_pools: tuple[tuple[str, ...], ...] = ()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        header = QHBoxLayout()
        actions = QGridLayout()
        self.titleLabel = QLabel("Club Dashboard")
        self.statusLabel = QLabel("Generate a guest list, then create a club event.")
        header.addWidget(self.titleLabel, 1)
        header.addWidget(self.statusLabel, 2)
        header.addWidget(QLabel("Rumors shown:"))
        self.rumorLimitCombo = QComboBox()
        self.rumorLimitCombo.addItems(["3", "5", "7"])
        self.rumorLimitCombo.setCurrentText("5")
        header.addWidget(self.rumorLimitCombo)
        self.pickLateBtn = QPushButton("Pick Different Late Arrival")
        self.pickLateBtn.setEnabled(False)
        actions.addWidget(self.pickLateBtn, 0, 0)
        self.browseRumorsBtn = QPushButton("Browse Rumor Pool")
        self.browseRumorsBtn.setEnabled(False)
        actions.addWidget(self.browseRumorsBtn, 0, 1)
        self.copyTablePrepBtn = QPushButton()
        self._update_table_prep_button()
        self.copyTablePrepBtn.setEnabled(False)
        actions.addWidget(self.copyTablePrepBtn, 0, 2)
        self.copyClubDebugBtn = QPushButton("Copy Club Debug Context")
        self.copyClubDebugBtn.setEnabled(False)
        actions.addWidget(self.copyClubDebugBtn, 1, 0)
        self.copyNpcDebugBtn = QPushButton("Copy NPC Debug Context")
        self.copyNpcDebugBtn.setEnabled(False)
        actions.addWidget(self.copyNpcDebugBtn, 1, 1)
        self.regenerateNpcBtn = QPushButton("Regenerate NPC Panel")
        self.regenerateNpcBtn.setEnabled(False)
        actions.addWidget(self.regenerateNpcBtn, 1, 2)
        self.retryPrepBtn = QPushButton("Retry Incomplete Prep")
        self.retryPrepBtn.setEnabled(False)
        actions.addWidget(self.retryPrepBtn, 0, 3)
        root.addLayout(header)
        root.addLayout(actions)

        stir_row = QHBoxLayout()
        self.stirRoomBtn = QPushButton("Stir the Room")
        self.stirRoomBtn.setEnabled(False)
        stir_row.addWidget(self.stirRoomBtn)
        self.stirSuggestionLabel = QLabel()
        self.stirSuggestionLabel.setAccessibleName("Optional GM suggestion")
        self.stirSuggestionLabel.setTextFormat(Qt.TextFormat.PlainText)
        self.stirSuggestionLabel.setWordWrap(True)
        self.stirSuggestionLabel.setVisible(False)
        stir_row.addWidget(self.stirSuggestionLabel, 1)
        self.clearStirBtn = QPushButton("Clear")
        self.clearStirBtn.setVisible(False)
        stir_row.addWidget(self.clearStirBtn)
        root.addLayout(stir_row)

        self.mainSplitter = QSplitter()
        left = QWidget()
        left.setMinimumWidth(320)
        left_layout = QVBoxLayout(left)
        self.summaryBrowser = QTextBrowser()
        self.summaryBrowser.setOpenLinks(False)
        self.summaryBrowser.setOpenExternalLinks(False)
        left_layout.addWidget(self.summaryBrowser, 2)

        quick_lists = QHBoxLayout()
        pinned_layout = QVBoxLayout()
        pinned_label = QLabel("Pinned")
        self.pinnedNpcList = QListWidget()
        self.pinnedNpcList.setAccessibleName("Pinned club NPCs")
        self.pinnedNpcList.setToolTip("Prepared NPC panels kept close at hand for this event.")
        self.pinnedNpcList.setMaximumHeight(112)
        pinned_label.setBuddy(self.pinnedNpcList)
        pinned_layout.addWidget(pinned_label)
        pinned_layout.addWidget(self.pinnedNpcList)
        quick_lists.addLayout(pinned_layout, 1)

        recent_layout = QVBoxLayout()
        recent_label = QLabel("Recent")
        self.recentNpcList = QListWidget()
        self.recentNpcList.setAccessibleName("Recently viewed club NPCs")
        self.recentNpcList.setToolTip("The five most recently viewed prepared NPC panels.")
        self.recentNpcList.setMaximumHeight(112)
        recent_label.setBuddy(self.recentNpcList)
        recent_layout.addWidget(recent_label)
        recent_layout.addWidget(self.recentNpcList)
        quick_lists.addLayout(recent_layout, 1)
        left_layout.addLayout(quick_lists)

        guest_label = QLabel("Guests")
        left_layout.addWidget(guest_label)
        self.guestSearchEdit = QLineEdit()
        self.guestSearchEdit.setAccessibleName("Search club guests")
        self.guestSearchEdit.setPlaceholderText("Filter guests by name")
        self.guestSearchEdit.setToolTip("Filter the visible roster by NPC name without changing the event.")
        guest_label.setBuddy(self.guestSearchEdit)
        left_layout.addWidget(self.guestSearchEdit)
        self.guestList = QListWidget()
        self.guestList.setAccessibleName("Club guest roster")
        left_layout.addWidget(self.guestList, 1)
        self.mainSplitter.addWidget(left)

        right = QWidget()
        right.setMinimumWidth(280)
        right_layout = QVBoxLayout(right)
        self.pinNpcBtn = QPushButton("Pin NPC")
        self.pinNpcBtn.setAccessibleName("Pin current NPC")
        self.pinNpcBtn.setToolTip("Pin or unpin the prepared NPC currently shown in the drawer.")
        self.pinNpcBtn.setEnabled(False)
        right_layout.addWidget(self.pinNpcBtn)
        self.drawer = QTextBrowser()
        self.drawer.setOpenLinks(False)
        self.drawer.setOpenExternalLinks(False)
        self.drawer.setHtml("<h2>NPC</h2><p>Select an NPC to generate an AI prep panel.</p>")
        right_layout.addWidget(self.drawer, 1)
        self.mainSplitter.addWidget(right)
        self.mainSplitter.setChildrenCollapsible(False)
        self.mainSplitter.setStretchFactor(0, 2)
        self.mainSplitter.setStretchFactor(1, 1)
        self.mainSplitter.setSizes([760, 340])
        root.addWidget(self.mainSplitter, 1)

        self.summaryBrowser.anchorClicked.connect(self._on_anchor_clicked)
        self.summaryBrowser.highlighted.connect(self._on_group_hovered)
        self.drawer.anchorClicked.connect(self._on_anchor_clicked)
        self.guestList.itemActivated.connect(self._on_guest_activated)
        self.guestList.itemClicked.connect(self._on_guest_activated)
        self.pinnedNpcList.itemActivated.connect(self._on_guest_activated)
        self.pinnedNpcList.itemClicked.connect(self._on_guest_activated)
        self.recentNpcList.itemActivated.connect(self._on_guest_activated)
        self.recentNpcList.itemClicked.connect(self._on_guest_activated)
        self.guestSearchEdit.textChanged.connect(self._render_guest_list)
        self.pinNpcBtn.clicked.connect(self.toggle_current_npc_pin)
        self.pickLateBtn.clicked.connect(self.pick_different_late_arrival)
        self.browseRumorsBtn.clicked.connect(self.browse_rumor_pool)
        self.copyTablePrepBtn.clicked.connect(self.copy_table_prep)
        self.copyClubDebugBtn.clicked.connect(self.copy_club_debug_context)
        self.copyNpcDebugBtn.clicked.connect(self.copy_npc_debug_context)
        self.regenerateNpcBtn.clicked.connect(self.regenerate_current_npc_panel)
        self.retryPrepBtn.clicked.connect(self.retry_incomplete_prep)
        self.stirRoomBtn.clicked.connect(self.show_next_stir_suggestion)
        self.clearStirBtn.clicked.connect(self.clear_stir_suggestion)

        self.setTabOrder(self.pinnedNpcList, self.recentNpcList)
        self.setTabOrder(self.recentNpcList, self.guestSearchEdit)
        self.setTabOrder(self.guestSearchEdit, self.guestList)
        self.setTabOrder(self.guestList, self.pinNpcBtn)

    def create_event_from_picks(self, picks: Sequence[Any], *, host_path: str | None = None) -> None:
        guest_paths = [str(getattr(pick, "file_path", pick)) for pick in picks if str(getattr(pick, "file_path", pick)).strip()]
        if not guest_paths:
            self._set_status("No guest results are available.")
            return
        self._guest_paths = guest_paths
        self._host_path = host_path
        self._last_generated_rumor_limit = self._selected_rumor_limit()
        self._set_status("Building AI club prep from grounded indexes...")
        self._run_event_worker(guest_paths, host_path)

    def pick_different_late_arrival(self) -> None:
        if self._build_result is None or self._event_busy:
            return
        self._run_prep_worker(change_late=True)

    def retry_incomplete_prep(self) -> None:
        if self._build_result is not None and not self._event_busy and not self._event_worker_active():
            self._run_prep_worker()

    def _run_prep_worker(self, *, change_late=False) -> None:
        self._start_event_worker(ClubPrepWorker(self.service, self._build_result, self._host_path, change_late=change_late))

    def _run_event_worker(self, guest_paths: Sequence[str], host_path: str | None) -> None:
        worker = ClubEventWorker(self.service, guest_paths, host_path, self._last_generated_rumor_limit)
        self._start_event_worker(worker)

    def _start_event_worker(self, worker) -> None:
        retrying_unsettled_baseline = not isinstance(worker, ClubEventWorker) and self._event_actions_pending()
        self._event_generation_token += 1
        self._panel_requests_by_npc.clear()
        token = self._event_generation_token
        worker.token = token
        self._event_busy = True
        self._settled_event_token = None
        if isinstance(worker, ClubEventWorker):
            self._pending_event_token = token
            self._baseline_event_token = None
            self._failed_baseline_token = None
            self._sync_event_readiness_controls()
            if self._event is not None:
                self._render_event()
        elif retrying_unsettled_baseline:
            self._pending_event_token = token
            self._baseline_event_token = token
            self._failed_baseline_token = None
            self._sync_event_readiness_controls()
            self._render_event()
        self.pickLateBtn.setEnabled(False)
        self.retryPrepBtn.setEnabled(False)
        thread = _ClubWorkerThread(worker, self)
        self._workers.append(worker)
        self._workers_by_thread[thread] = worker
        worker.progress.connect(self._event_progress)
        worker.finished.connect(self._accept_event_result)
        worker.error.connect(self._event_error)
        if isinstance(worker, ClubEventWorker):
            worker.baseline.connect(self._accept_baseline_result)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._threads.append(thread)
        thread.start()
        QTimer.singleShot(CLUB_WORKER_TIMEOUT_MS, lambda: self._on_worker_timeout(thread, "Club event build is still running. It may be waiting on vault files or the model provider."))

    @Slot(int, str)
    def _event_progress(self, token, message):
        if token == self._event_generation_token:
            self._set_status(message)

    @Slot(int, object)
    def _accept_baseline_result(self, token, result):
        if (
            token != self._event_generation_token
            or self._pending_event_token != token
            or self._baseline_event_token == token
        ):
            return
        self._baseline_event_token = token
        self._failed_baseline_token = None
        self._install_event_result(token, result, baseline=True)

    @Slot(int, object)
    def _accept_event_result(self, token: int, result: ClubBuildResult) -> None:
        if token != self._event_generation_token or self._settled_event_token == token:
            return
        self._install_event_result(token, result)
        self._settled_event_token = token

    def _install_event_result(self, token, result, *, baseline=False):
        if token != self._event_generation_token:
            return
        self._event_busy = baseline
        if not baseline:
            self._pending_event_token = None
            self._baseline_event_token = None
            self._failed_baseline_token = None
        self._on_event_ready(result)
        readiness = self._current_event_readiness()
        transition = {
            "partial_ai": "prep_settled_incomplete_ai",
            "incomplete": "prep_settled_incomplete_ai",
            "fallback": "prep_settled_fallback",
        }.get(readiness.state, "baseline_visible" if baseline else "prep_settled_final")
        self._log_readiness_transition(transition, token=token, revision=self._prep_revision)

    @Slot(int, str)
    def _event_error(self, token: int, message: str) -> None:
        if token != self._event_generation_token or self._settled_event_token == token:
            return
        self._event_busy = False
        if self._pending_event_token == token and self._baseline_event_token == token:
            self._failed_baseline_token = token
            self._sync_event_readiness_controls()
            self._set_status(self._current_event_readiness().status)
            self._log_readiness_transition("prep_failed_with_baseline", token=token, revision=self._prep_revision)
            return
        if self._pending_event_token == token:
            self._pending_event_token = None
            self._baseline_event_token = None
            self._failed_baseline_token = None
        self._sync_event_readiness_controls()
        self.retryPrepBtn.setEnabled(self._build_result is not None and _event_prep_incomplete(self._event))
        self._set_status(message)

    def _run_panel_worker(self, npc_id: str, *, force: bool = False) -> None:
        if self._build_result is None or not self._current_event_readiness().actionable:
            return
        if npc_id in self._panel_requests_by_npc:
            self._current_npc_id = npc_id
            self._active_panel_request_id = self._panel_requests_by_npc[npc_id]
            if npc_id not in self._panels_by_npc_id:
                self._show_panel_loading(npc_id)
            self._set_status(f"NPC panel is already building for {self._name_for(npc_id)}.")
            return
        self._panel_request_counter += 1
        request_id = self._panel_request_counter
        self._panel_requests_by_npc[npc_id] = request_id
        self._current_npc_id = npc_id
        self._active_panel_request_id = request_id
        if npc_id not in self._panels_by_npc_id:
            self._show_panel_loading(npc_id)
        self.regenerateNpcBtn.setEnabled(False)
        worker = ClubPanelWorker(self.service, self._build_result, npc_id, request_id, force=force)
        thread = _ClubWorkerThread(worker, self)
        worker.token, worker.revision = self._event_generation_token, self._prep_revision
        self._workers.append(worker)
        self._workers_by_thread[thread] = worker
        worker.progress.connect(self._panel_progress)
        worker.finished.connect(self._accept_panel_result)
        worker.error.connect(self._panel_error)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._threads.append(thread)
        thread.start()
        QTimer.singleShot(CLUB_WORKER_TIMEOUT_MS, lambda: self._on_worker_timeout(thread, "NPC panel build is still running. It may be waiting on the model provider."))

    @Slot(object)
    def _on_event_ready(self, result: ClubBuildResult) -> None:
        roster_identity = (result.event.event_id, tuple(result.event.attendee_ids))
        if roster_identity != self._roster_identity:
            blocked = self.guestSearchEdit.blockSignals(True)
            self.guestSearchEdit.clear()
            self.guestSearchEdit.blockSignals(blocked)
        self._roster_identity = roster_identity
        self._build_result = result
        self._event = result.event
        self._prep_revision = result.event.dashboard.get("scene_prep", {}).get("revision", "")
        self._attendees = {attendee["npc_id"]: attendee for attendee in result.attendee_summaries()}
        self._sync_stir_room_view()
        self.copyNpcDebugBtn.setEnabled(False)
        self.regenerateNpcBtn.setEnabled(False)
        self.pinNpcBtn.setEnabled(False)
        self.pinNpcBtn.setText("Pin NPC")
        self.pinNpcBtn.setAccessibleName("Pin current NPC")
        self._current_npc_id = ""
        self._current_panel = None
        self._active_panel_request_id = None
        self._panels_by_npc_id.clear()
        self._pinned_npc_ids.clear()
        self._recent_npc_ids.clear()
        self._update_table_prep_button()
        self._refresh_navigation_lists()
        if self._event_actions_pending():
            self.drawer.setHtml(
                "<h2>NPC prep is still building</h2>"
                "<p>NPC preparation will unlock when full event prep settles.</p>"
            )
        else:
            self.drawer.setHtml("<h2>NPC</h2><p>Select an NPC to prepare them for this arrangement.</p>")
        self._panel_requests_by_npc.clear()
        self._render_event()
        self._sync_event_readiness_controls()
        self._set_status(self._current_event_readiness().status)

    @Slot()
    def _on_thread_finished(self):
        thread = self.sender()
        worker = self._workers_by_thread.pop(thread, None)
        if worker in self._workers:
            self._workers.remove(worker)
        if thread in self._threads:
            self._threads.remove(thread)
        if (
            isinstance(worker, (ClubEventWorker, ClubPrepWorker))
            and worker.token == self._event_generation_token
            and self._failed_baseline_token == self._event_generation_token
        ):
            self.retryPrepBtn.setEnabled(self._build_result is not None)

    @Slot(int, str, str)
    def _panel_progress(self, token, revision, message):
        if token == self._event_generation_token and revision == self._prep_revision:
            self._set_status(message)

    @Slot(int, str, str, int, object)
    def _accept_panel_result(self, token, revision, npc_id, request_id, panel) -> None:
        if token != self._event_generation_token or revision != self._prep_revision:
            return
        self._on_panel_ready(npc_id, request_id, panel)

    @Slot(int, str, str, int, str)
    def _panel_error(self, token, revision, npc_id, request_id, _message) -> None:
        if token != self._event_generation_token or revision != self._prep_revision or self._panel_requests_by_npc.get(npc_id) != request_id:
            return
        self._panel_requests_by_npc.pop(npc_id, None)
        if self._current_npc_id != npc_id or request_id != self._active_panel_request_id:
            return
        self._active_panel_request_id = None
        self.regenerateNpcBtn.setEnabled(bool(self._current_npc_id))
        self._update_pin_button()
        self._set_status("NPC panel unavailable.")

    @Slot(str, int, object)
    def _on_panel_ready(self, npc_id: str, request_id: int, panel: dict[str, Any]) -> None:
        if self._panel_requests_by_npc.get(npc_id) != request_id:
            return
        self._panel_requests_by_npc.pop(npc_id, None)
        self._panels_by_npc_id[npc_id] = panel
        self._update_table_prep_button()
        self._render_guest_list()
        self._refresh_navigation_lists()
        if self._current_npc_id != npc_id or request_id != self._active_panel_request_id:
            return
        self._display_prepared_panel(npc_id, panel)

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self._panel_requests_by_npc.clear()
        self._active_panel_request_id = None
        self.regenerateNpcBtn.setEnabled(bool(self._current_npc_id))
        self._update_pin_button()
        self._set_status(message)

    def _on_worker_timeout(self, thread: QThread, message: str) -> None:
        if thread in self._threads and thread.isRunning():
            self._set_status(message)

    def _set_status(self, message: str) -> None:
        self.statusLabel.setText(message)
        print(f"[CLUB] {message}")

    def _event_actions_pending(self) -> bool:
        return self._pending_event_token == self._event_generation_token

    def _event_worker_active(self) -> bool:
        return any(isinstance(worker, (ClubEventWorker, ClubPrepWorker)) for worker in self._workers)

    def _current_event_readiness(self) -> _ClubEventReadiness:
        return _club_event_readiness(
            self._event,
            pending=self._event_actions_pending(),
            baseline_failed=self._failed_baseline_token == self._event_generation_token,
        )

    def _sync_event_readiness_controls(self) -> None:
        readiness = self._current_event_readiness()
        has_event = self._event is not None
        actionable = has_event and readiness.actionable
        self.guestList.setEnabled(actionable)
        self.pinnedNpcList.setEnabled(actionable)
        self.recentNpcList.setEnabled(actionable)
        self.pickLateBtn.setEnabled(actionable and not self._event_busy)
        self.browseRumorsBtn.setEnabled(self._build_result is not None)
        self.copyTablePrepBtn.setEnabled(actionable)
        self.copyClubDebugBtn.setEnabled(self._build_result is not None)
        self.retryPrepBtn.setEnabled(actionable and not self._event_busy and readiness.incomplete)
        if not actionable:
            self.regenerateNpcBtn.setEnabled(False)
            self.copyNpcDebugBtn.setEnabled(False)
            self.pinNpcBtn.setEnabled(False)
        self._update_table_prep_button()

    @staticmethod
    def _log_readiness_transition(transition: str, *, token: int, revision: str) -> None:
        safe_revision = revision if revision else "none"
        print(
            f"[CLUB] readiness_transition={transition} "
            f"generation_token={token} prep_revision={safe_revision}"
        )

    def _update_table_prep_button(self) -> None:
        count = len(_table_prep_panel_ids(self._event, self._panels_by_npc_id))
        noun = "NPC" if count == 1 else "NPCs"
        prefix = "Copy Incomplete GM Table Prep" if self._current_event_readiness().incomplete and not self._event_actions_pending() else "Copy GM Table Prep"
        self.copyTablePrepBtn.setText(f"{prefix} ({count} {noun})")

    def _sync_stir_room_view(self) -> None:
        if self._event is None:
            pools: tuple[tuple[str, ...], ...] = ()
            view_key: tuple[Any, ...] | None = None
        else:
            pools = _stir_room_candidate_pools(self._event, self._attendees)
            view_key = _stir_room_view_key(self._event, self._attendees, pools)
        if view_key != self._stir_view_key:
            self._stir_counter = 0
            self._stir_view_key = view_key
            self.clear_stir_suggestion()
        self._stir_pools = pools
        self.stirRoomBtn.setEnabled(any(pools))
        if not any(pools):
            self.clear_stir_suggestion()

    @Slot()
    def show_next_stir_suggestion(self) -> None:
        if self._event is None:
            return
        suggestion = _stir_room_suggestion(
            self._stir_pools,
            seed=_stir_room_seed(self._event),
            counter=self._stir_counter,
        )
        if not suggestion:
            self.stirRoomBtn.setEnabled(False)
            self.clear_stir_suggestion()
            return
        self.stirSuggestionLabel.setText(f"Optional GM suggestion: {suggestion}")
        self.stirSuggestionLabel.setVisible(True)
        self.clearStirBtn.setVisible(True)
        self.stirRoomBtn.setText("Next suggestion")
        self._stir_counter += 1

    @Slot()
    def clear_stir_suggestion(self) -> None:
        self.stirSuggestionLabel.clear()
        self.stirSuggestionLabel.setVisible(False)
        self.clearStirBtn.setVisible(False)
        self.stirRoomBtn.setText("Stir the Room")

    def _show_panel_loading(self, npc_id: str) -> None:
        self._current_panel = None
        self.copyNpcDebugBtn.setEnabled(False)
        self.regenerateNpcBtn.setEnabled(False)
        self._update_pin_button()
        self.drawer.setHtml(
            f"<h2>{html.escape(self._name_for(npc_id))}</h2>"
            "<p>Preparing this NPC for the current arrangement...</p>"
        )

    def _open_npc(self, npc_id: str) -> None:
        if npc_id not in self._attendees or not self._current_event_readiness().actionable:
            return
        panel = self._panels_by_npc_id.get(npc_id)
        if isinstance(panel, dict):
            self._display_prepared_panel(npc_id, panel)
            return
        self._run_panel_worker(npc_id)

    def _display_prepared_panel(self, npc_id: str, panel: dict[str, Any]) -> None:
        if npc_id not in self._attendees or self._panels_by_npc_id.get(npc_id) is not panel:
            return
        self._current_npc_id = npc_id
        self._current_panel = panel
        self._active_panel_request_id = None
        self.drawer.setHtml(self._panel_html(npc_id, panel))
        self.copyNpcDebugBtn.setEnabled(True)
        self.regenerateNpcBtn.setEnabled(npc_id not in self._panel_requests_by_npc)
        self._recent_npc_ids = [npc_id, *(item for item in self._recent_npc_ids if item != npc_id)]
        del self._recent_npc_ids[RECENT_NPC_LIMIT:]
        self._refresh_navigation_lists()
        self._render_guest_list()
        self._set_status(_npc_status_for_panel(panel))

    def _navigation_item(self, npc_id: str) -> QListWidgetItem:
        item = QListWidgetItem(self._name_for(npc_id))
        item.setData(Qt.UserRole, npc_id)
        item.setToolTip(f"Open the prepared panel for {self._name_for(npc_id)} without generating it again.")
        return item

    def _refresh_navigation_lists(self) -> None:
        prepared_ids = {
            npc_id for npc_id, panel in self._panels_by_npc_id.items()
            if npc_id in self._attendees and isinstance(panel, dict)
        }
        self._pinned_npc_ids = [npc_id for npc_id in self._pinned_npc_ids if npc_id in prepared_ids]
        self._recent_npc_ids = [npc_id for npc_id in self._recent_npc_ids if npc_id in prepared_ids][:RECENT_NPC_LIMIT]
        self.pinnedNpcList.clear()
        for npc_id in self._pinned_npc_ids:
            self.pinnedNpcList.addItem(self._navigation_item(npc_id))
        self.recentNpcList.clear()
        for npc_id in self._recent_npc_ids:
            self.recentNpcList.addItem(self._navigation_item(npc_id))
        self._update_pin_button()

    def _update_pin_button(self) -> None:
        prepared = (
            bool(self._current_npc_id)
            and isinstance(self._current_panel, dict)
            and self._panels_by_npc_id.get(self._current_npc_id) is self._current_panel
        )
        pinned = prepared and self._current_npc_id in self._pinned_npc_ids
        self.pinNpcBtn.setText("Unpin NPC" if pinned else "Pin NPC")
        self.pinNpcBtn.setAccessibleName("Unpin current NPC" if pinned else "Pin current NPC")
        self.pinNpcBtn.setEnabled(prepared)

    @Slot(bool)
    def toggle_current_npc_pin(self, _checked: bool = False) -> None:
        npc_id = self._current_npc_id
        if not npc_id or not isinstance(self._current_panel, dict) or self._panels_by_npc_id.get(npc_id) is not self._current_panel:
            return
        if npc_id in self._pinned_npc_ids:
            self._pinned_npc_ids.remove(npc_id)
        else:
            self._pinned_npc_ids.append(npc_id)
        self._refresh_navigation_lists()

    @Slot(str)
    def _render_guest_list(self, _text: str = "") -> None:
        self.guestList.clear()
        if self._build_result is None:
            return
        needle = self.guestSearchEdit.text().strip().casefold()
        for identity in self._build_result.identities:
            if needle and needle not in identity.display_name.casefold():
                continue
            prepared = isinstance(self._panels_by_npc_id.get(identity.npc_id), dict)
            label = f"{identity.display_name} — Prepared" if prepared else identity.display_name
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, identity.npc_id)
            if prepared:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setToolTip("Prepared. Open this retained panel without generating it again.")
            elif self._event_actions_pending():
                item.setToolTip("Full event prep is still building. NPC preparation will unlock when it settles.")
            else:
                item.setToolTip("Not prepared. Open this NPC to prepare a panel.")
            self.guestList.addItem(item)
            if identity.npc_id == self._current_npc_id:
                self.guestList.setCurrentItem(item)

    def _render_event(self) -> None:
        if self._event is None or self._build_result is None:
            return
        self._render_guest_list()
        self.summaryBrowser.setHtml(self._dashboard_html(self._event.dashboard, self._event.late_arrival_id))

    def _dashboard_html(self, dashboard: dict[str, Any], late_arrival_id: str) -> str:
        event = dashboard.get("event") if isinstance(dashboard.get("event"), dict) else {}
        rumor_selection = dashboard.get("rumor_selection") if isinstance(dashboard.get("rumor_selection"), dict) else {}
        rumor_heading = "Rumors in Circulation"
        selected_count = rumor_selection.get("selected_count")
        grounded_count = rumor_selection.get("grounded_count")
        if selected_count is not None and grounded_count is not None:
            rumor_heading = f"Rumors in Circulation - {selected_count} selected from {grounded_count} grounded rumors"
        if isinstance(dashboard.get("scene_prep"), dict):
            parts = ["<style>body{font-family:Segoe UI,Arial,sans-serif;font-size:13px;}h2{font-size:17px;margin:14px 0 4px;}p{margin:4px 0;}</style>",
                     f"<h1>{html.escape(str(event.get('venue') or 'Tonight at the Club'))}</h1>"]
            names = {n: self._name_for(n) for n in self._attendees}
            for title, rows in scene_display_sections(dashboard["scene_prep"], names):
                parts.append(f"<h2>{html.escape(title)}</h2>")
                if title == "Social Groups and Loners":
                    parts.append("<p>Suggested opening arrangement. Hover for names; click a group for details.</p>")
                    for row in dashboard["scene_prep"]["encounters"]:
                        if len(row["members"]) > 1:
                            label = html.escape(row.get("label") or "Conversation group")
                            parts.append(f'<p><a href="group:{row["number"]}">{row["number"]}. {label} · {len(row["members"])} guests</a></p>')
                        else:
                            detail = "Not here yet—reroll until arrival" if row["availability"] == "expected" else "Arrangement unavailable" if row.get("basis") == "unavailable" else "Solo"
                            approach = row.get("conversation_cue", {}).get("approach") if isinstance(row.get("conversation_cue"), dict) else ""
                            approach_link = f' · <a href="group:{row["number"]}">Approach</a>' if row["availability"] == "present" and approach else ""
                            parts.append(f'<p>{row["number"]}. {self._npc_link(row["members"][0])} · {detail}{approach_link}</p>')
                    continue
                parts.extend(f"<p>{self._prep_links(row)}</p>" for row in rows)
            parts.extend([f"<h2>{html.escape(rumor_heading)}</h2>",
                          self._rumors_html(dashboard), self._additional_rumor_pool_html(dashboard)])
            return "\n".join(parts)
        parts = [
            "<style>"
            "body{font-family:Segoe UI,Arial,sans-serif;font-size:13px;}"
            "h1{font-size:26px;margin:0 0 6px;}h2{font-size:17px;margin:14px 0 4px;}"
            "p{margin:4px 0;}ul{margin:4px 0 8px 18px;}li{margin:3px 0;}"
            ".meta{color:#444}.empty{color:#777}.why{color:#666;font-size:11px;margin-top:2px;}"
            ".rumor-guidance{margin:3px 0 0 14px;color:#444;}"
            "</style>",
            f"<h1>{html.escape(str(event.get('venue') or 'Tonight at the Club'))}</h1>",
            f"<p class='meta'><b>Event:</b> {html.escape(str(event.get('event_type') or 'social gathering'))} "
            f"<b>Mood:</b> {html.escape(str(event.get('mood') or ''))} "
            f"<b>Late Arrival:</b> {self._npc_link(late_arrival_id)}</p>",
            "<h2>Room Situation</h2>",
            self._room_situation_html(dashboard),
            "<h2>Hot Connections</h2>",
            self._prep_or_grounded_items_html(dashboard, "hot_connections", "top_connections"),
            "<h2>Possible Pressure</h2>",
            self._prep_or_grounded_items_html(dashboard, "possible_pressure", "possible_drama"),
            f"<h2>{html.escape(rumor_heading)}</h2>",
            self._rumors_html(dashboard),
            self._additional_rumor_pool_html(dashboard),
            "<h2>Guests</h2>",
            self._string_items_html(dashboard.get("guest_brief") or self._guest_brief_fallback(late_arrival_id)),
        ]
        return "\n".join(parts)

    def _prep_links(self, text: str) -> str:
        escaped = html.escape(text)
        names = {}
        ambiguous = set()
        for npc_id in self._attendees:
            name = html.escape(self._name_for(npc_id))
            if name in names:
                ambiguous.add(name)
            names[name] = npc_id
        for name in ambiguous:
            names.pop(name, None)
        if not names:
            return escaped
        pattern = r"(?<!\w)(?:" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")(?!\w)"
        return re.sub(pattern, lambda m: self._npc_link(names[m.group(0)]), escaped)

    def _room_situation_html(self, dashboard: dict[str, Any]) -> str:
        rows: list[str] = []
        first_impression = clean_display_text(str(dashboard.get("first_impression") or ""))
        if first_impression:
            rows.append(
                "<p><b>First impression (AI presentation):</b> "
                f"{html.escape(first_impression)}</p>"
            )
        grounded = clean_display_text(str(dashboard.get("room_situation") or ""))
        if grounded:
            rows.append(f"<p><b>Grounded situation:</b> {html.escape(grounded)}</p>")
        else:
            rows.append(
                "<p class='empty'><b>Grounded situation:</b> "
                "No grounded prep surfaced for this section.</p>"
            )
        return "\n".join(rows)

    def _selected_rumor_limit(self) -> int:
        try:
            return int(self.rumorLimitCombo.currentText())
        except (TypeError, ValueError):
            return 5

    def _additional_rumor_pool_html(self, dashboard: dict[str, Any]) -> str:
        selection = dashboard.get("rumor_selection") if isinstance(dashboard.get("rumor_selection"), dict) else {}
        try:
            hidden = max(0, int(selection.get("grounded_count") or 0) - int(selection.get("selected_count") or 0))
        except (TypeError, ValueError):
            hidden = 0
        if hidden <= 0:
            return ""
        return f"<p class='meta'>+{hidden} additional grounded rumors available in Browse Rumor Pool.</p>"

    def _rumor_pool_html(self, skeleton: dict[str, Any]) -> str:
        pool = [item for item in skeleton.get("rumor_pool") or [] if isinstance(item, dict)]
        rows: list[str] = []
        for item in pool:
            row = self._rumor_pool_row(item)
            text = html.escape(row.text)
            source_parts = [part for part in (row.source_name, row.source_section) if part]
            source_label = f"<div class='why'><b>Source:</b> {html.escape(' - '.join(source_parts))}</div>" if source_parts else ""
            rows.append(f"<li>{text}{source_label}</li>")
        body = "<ul>" + "".join(rows) + "</ul>" if rows else "<p class='empty'>No grounded rumors were found.</p>"
        return (
            "<style>"
            "body{font-family:Segoe UI,Arial,sans-serif;font-size:13px;}"
            "h1{font-size:22px;margin:0 0 8px;}ul{margin:4px 0 8px 18px;}li{margin:8px 0;}"
            ".meta{color:#555}.why{color:#666;font-size:11px;margin-top:2px;}.empty{color:#777}"
            "</style>"
            "<h1>Grounded Rumor Pool</h1>"
            f"{body}"
        )

    def _rumor_pool_row(self, item: dict[str, Any]) -> RumorPoolRow:
        sources = item.get("sources") if isinstance(item, dict) else []
        first = sources[0] if sources and isinstance(sources[0], dict) else {}
        source_id = str(first.get("source_id") or "")
        source_section = clean_display_text(str(first.get("section") or ""))
        source_name = ""
        if self._build_result is not None and source_id in self._build_result.source_map:
            mapped = self._build_result.source_map[source_id]
            source_name = clean_display_text(str(mapped.get("name") or ""))
            source = mapped.get("source") if isinstance(mapped.get("source"), dict) else {}
            source_section = clean_display_text(str(source.get("section") or mapped.get("section") or source_section))
        if not source_name:
            source_npc_id = str(item.get("source_npc_id") or first.get("character_id") or "")
            source_name = clean_display_text(str(self._attendees.get(source_npc_id, {}).get("name") or ""))
        return RumorPoolRow(
            text=_display_text_for_item(item),
            source_name=source_name,
            source_section=source_section,
        )

    def _social_map_html(self, items: Sequence[Any], *, compact: bool = False) -> str:
        rows: list[str] = []
        for item in items:
            if isinstance(item, dict):
                location = html.escape(str(item.get("location") or item.get("area") or "Area"))
                npc_ids = item.get("npc_ids") or item.get("characters") or []
                names = ", ".join(self._npc_link(str(npc_id)) for npc_id in npc_ids)
                summary = html.escape(str(item.get("summary") or ""))
                separator = " - " if names and summary else ""
                rows.append(f"<li><b>{location}</b>: {names}{separator}{summary}</li>")
            else:
                rows.append(f"<li>{html.escape(str(item))}</li>")
        if rows:
            return "<ul>" + "".join(rows) + "</ul>"
        return "" if compact else "<p class='empty'>No grounded entries for this section.</p>"

    def _prep_or_grounded_items_html(self, dashboard: dict[str, Any], prep_field: str, grounded_field: str) -> str:
        visible_items = [str(item).strip() for item in dashboard.get(prep_field) or [] if str(item).strip()]
        grounded = dashboard.get(grounded_field) or []
        if visible_items:
            return self._visible_items_html(visible_items, grounded)
        return self._string_items_html([])

    def _rumors_html(self, dashboard: dict[str, Any]) -> str:
        visible = [str(item).strip() for item in dashboard.get("rumors_in_circulation") or [] if str(item).strip()]
        grounded = [item for item in dashboard.get("rumors") or [] if isinstance(item, dict)]
        guidance = rumor_guidance_display_rows(
            dashboard.get("rumor_guidance") or [],
            {npc_id: self._name_for(npc_id) for npc_id in self._attendees},
        )
        by_rumor = {row["rumor_item_id"]: row["lines"] for row in guidance}
        rows = []
        for index, text in enumerate(visible):
            item_id = grounded[index].get("item_id") if index < len(grounded) else None
            details = by_rumor.get(item_id, [])
            detail_html = "".join(
                f"<div class='rumor-guidance'>{self._prep_links(line)}</div>" for line in details
            )
            rows.append(f"<li>{html.escape(text)}{detail_html}</li>")
        return "<ul>" + "".join(rows) + "</ul>" if rows else "<p class='empty'>No grounded prep surfaced for this section.</p>"

    def _visible_items_html(self, visible_items: Sequence[str], grounded_items: Sequence[Any]) -> str:
        rows: list[str] = []
        for text in visible_items:
            rows.append(f"<li>{html.escape(text)}</li>")
        return "<ul>" + "".join(rows) + "</ul>" if rows else "<p class='empty'>No grounded prep surfaced for this section.</p>"

    def _text_block_html(self, value: Any) -> str:
        text = str(value or "").strip()
        if text:
            return f"<p>{html.escape(text)}</p>"
        return "<p class='empty'>No grounded prep surfaced for this section.</p>"

    def _string_items_html(self, items: Sequence[Any]) -> str:
        rows = [f"<li>{html.escape(str(item))}</li>" for item in items if str(item).strip()]
        return "<ul>" + "".join(rows) + "</ul>" if rows else "<p class='empty'>No grounded prep surfaced for this section.</p>"

    def _guest_brief_fallback(self, late_arrival_id: str) -> list[str]:
        rows: list[str] = []
        for npc_id, attendee in self._attendees.items():
            suffix = " Late arrival." if npc_id == late_arrival_id else ""
            rows.append(f"{attendee.get('name') or npc_id}.{suffix}")
        return rows

    def _items_html(self, items: Sequence[Any], *, section: str = "") -> str:
        rows: list[str] = []
        for item in items:
            if isinstance(item, dict):
                has_prep = bool(item.get("prep_text"))
                summary = html.escape(str(item.get("prep_text") or item.get("summary") or item.get("text") or ""))
                chars = "" if has_prep else " ".join(self._npc_link(str(npc_id)) for npc_id in item.get("characters") or [])
                fact_type = html.escape(_display_label_for_section(item, section))
                type_label = f"<b>{fact_type}:</b> " if fact_type and not has_prep else ""
                prefix = " ".join(part for part in [chars, type_label] if part).strip()
                spacer = " " if prefix else ""
                rows.append(f"<li>{prefix}{spacer}{summary}</li>")
            else:
                text = str(item)
                if text.strip():
                    rows.append(f"<li>{html.escape(text)}</li>")
        return "<ul>" + "".join(rows) + "</ul>" if rows else "<p class='empty'>No grounded entries for this section.</p>"

    def _panel_html(self, npc_id: str, panel: dict[str, Any]) -> str:
        identity = panel.get("identity") if isinstance(panel.get("identity"), dict) else {}
        conversation = panel.get("conversation") if isinstance(panel.get("conversation"), dict) else {}
        tonight = panel.get("tonight") if isinstance(panel.get("tonight"), dict) else {}
        presentation = panel.get("presentation") if isinstance(panel.get("presentation"), dict) else {}
        focus = tonight.get("current_desire") if isinstance(tonight.get("current_desire"), dict) else None
        agenda = presentation.get("agenda") if isinstance(presentation.get("agenda"), dict) else None
        agenda_text = str((agenda or {}).get("text") or _display_text_for_item(focus))
        play_cue = str(presentation.get("play_cue") or tonight.get("current_demeanor") or "")
        identity_line = " | ".join(
            item
            for item in [
                str(identity.get("affiliation") or ""),
                str(identity.get("faction") or ""),
                str(identity.get("status") or ""),
            ]
            if item
        )
        parts = [
            "<style>"
            "body{font-family:Segoe UI,Arial,sans-serif;font-size:13px;}"
            "h1{font-size:26px;margin:0 0 4px;}h2{font-size:17px;margin:14px 0 4px;}"
            "p{margin:4px 0;}ul{margin:4px 0 8px 18px;}li{margin:3px 0;}.empty{color:#777}"
            "</style>",
            f"<h1>{html.escape(str(panel.get('name') or self._name_for(npc_id)))}</h1>",
            f"<p>{html.escape(identity_line)}</p>" if identity_line else "",
            "<h2>Right Now</h2>",
            f"<p>{html.escape(str(panel.get('current_read') or _fallback_panel_read(panel)))}</p>",
            "<h2>Play Them</h2>",
            self._text_block_html(play_cue),
            "<h2>Agenda Tonight</h2>",
            self._text_block_html(agenda_text),
            "<h2>Who Matters Here</h2>",
            self._who_matters_html(panel),
            "<h2>Conversation Openings</h2>",
            self._conversation_openings_html(panel),
            "<h2>If Approached</h2>",
            self._panel_cue_items_html(
                presentation.get("if_approached"),
                conversation.get("likely_subjects") or [],
                visible_fallback=panel.get("likely_conversation") or [],
            ),
            "<h2>Keep Guarded</h2>",
            self._panel_cue_items_html(
                presentation.get("keep_guarded"),
                conversation.get("sensitive") or [],
                visible_fallback=panel.get("sensitive_subjects") or [],
            ),
            "<h2>Pressure / Hook</h2>",
            self._panel_hook_html(panel),
        ]
        if panel.get("empty_state"):
            parts.append(f"<p class='empty'>{html.escape(str(panel.get('empty_state')))}</p>")
        return "\n".join(parts)

    def _who_matters_html(self, panel):
        if "who_matters" not in panel:
            return self._panel_cue_items_html(panel.get("presentation", {}).get("people_here"), panel.get("people_here") or [])
        raw_rows = panel.get("who_matters")
        rows = relevance_display_rows(raw_rows, {n: self._name_for(n) for n in self._attendees}) if isinstance(raw_rows, list) else []
        metadata = panel.get("metadata") if isinstance(panel.get("metadata"), dict) else {}
        stage = metadata.get("who_matters_stage")
        complete = isinstance(stage, dict) and stage.get("status") == "complete" and isinstance(raw_rows, list)
        prefix = "<p>Relevance analysis unavailable; existing relationships shown.</p>" if not complete else ""
        return prefix + ("".join(f"<p>{self._prep_links(row)}</p>" for row in rows) or "<p>No supported interest surfaced.</p>")

    def _conversation_openings_html(self, panel: dict[str, Any]) -> str:
        rows = panel.get("conversation_openings")
        metadata = panel.get("metadata") if isinstance(panel.get("metadata"), dict) else {}
        stage = metadata.get("conversation_openings_stage")
        rendered = conversation_opening_display_rows(
            rows if isinstance(rows, list) else [], stage if isinstance(stage, dict) else None,
        )
        return "".join(f"<p>{html.escape(row)}</p>" for row in rendered)

    def _panel_cue_items_html(
        self,
        cue_items: Any,
        grounded_items: Sequence[Any],
        *,
        visible_fallback: Sequence[Any] = (),
    ) -> str:
        grounded_by_id = {
            str(item.get("item_id") or ""): item
            for item in grounded_items
            if isinstance(item, dict) and str(item.get("item_id") or "")
        }
        rows: list[str] = []
        if isinstance(cue_items, list):
            for cue in cue_items:
                if not isinstance(cue, dict):
                    continue
                item_id = str(cue.get("item_id") or "")
                if item_id not in grounded_by_id:
                    continue
                text = str(cue.get("text") or "").strip()
                if text:
                    rows.append(f"<li>{html.escape(text)}</li>")
        if not rows:
            fallback = list(visible_fallback) or [_display_text_for_item(item) for item in grounded_items]
            rows = [f"<li>{html.escape(str(value))}</li>" for value in fallback if str(value).strip()]
        return "<ul>" + "".join(rows) + "</ul>" if rows else "<p class='empty'>No grounded prep surfaced for this section.</p>"

    def _panel_hook_html(self, panel: dict[str, Any]) -> str:
        item = panel.get("interesting_detail")
        presentation = panel.get("presentation") if isinstance(panel.get("presentation"), dict) else {}
        cue = presentation.get("hook") if isinstance(presentation.get("hook"), dict) else {}
        text = str(cue.get("text") or panel.get("useful_hook") or _display_text_for_item(item) or panel.get("empty_state") or "No grounded hook surfaced.")
        return f"<p>{html.escape(text)}</p>"

    def _dashboard_items_for(self, npc_id: str) -> list[dict[str, Any]]:
        if self._event is None:
            return []
        items: list[dict[str, Any]] = []
        dashboard = self._event.dashboard
        for field in (
            "possible_drama",
            "interesting_connections",
            "unresolved_business",
            "opportunities",
            "top_connections",
            "rumors",
        ):
            for item in dashboard.get(field) or []:
                if isinstance(item, dict) and npc_id in set(item.get("characters") or []):
                    items.append(item)
        return items

    def _npc_link(self, npc_id: str) -> str:
        if not npc_id:
            return ""
        name = html.escape(self._name_for(npc_id))
        if self._event_actions_pending():
            return name
        return f'<a href="npc:{html.escape(npc_id)}">{name}</a>'

    def _name_for(self, npc_id: str) -> str:
        return self._attendees.get(npc_id, {}).get("name") or npc_id

    def copy_club_debug_context(self) -> None:
        if self._build_result is None:
            return
        QApplication.clipboard().setText(safe_debug_json(self._build_result.to_debug_dict()))
        self._set_status("Club debug context copied.")

    def copy_npc_debug_context(self) -> None:
        if self._build_result is None or self._current_panel is None:
            return
        panel_skeleton = build_npc_panel_skeleton(self._build_result, self._current_npc_id)
        skeleton_source_map = panel_skeleton.get("source_map")
        if not isinstance(skeleton_source_map, dict):
            skeleton_source_map = {}
        source_ids = sorted(_source_ids_from_panel(self._current_panel))
        payload = {
            "npc_id": self._current_npc_id,
            "panel": self._current_panel,
            "panel_skeleton": panel_skeleton,
            "prep_support": self._build_result.prep_context.debug_support(prep_references([
                self._current_panel.get("who_matters", []),
                self._current_panel.get("conversation_openings", []),
            ])) if self._build_result.prep_context else {},
            "sources": {source_id: self._build_result.source_map.get(source_id) for source_id in source_ids},
            "source_map": skeleton_source_map,
            "event_metadata": self._build_result.event.metadata,
            "event": {
                "event_id": self._build_result.event.event_id,
                "cache_key": self._build_result.event.cache_key,
                "seed": self._build_result.event.seed,
                "attendee_ids": list(self._build_result.event.attendee_ids),
                "late_arrival_id": self._build_result.event.late_arrival_id,
            },
            "attendees": self._build_result.attendee_summaries(),
        }
        QApplication.clipboard().setText(safe_debug_json(payload))
        self._set_status("NPC debug context copied.")

    def copy_table_prep(self) -> None:
        readiness = self._current_event_readiness()
        if self._event is None or not readiness.actionable:
            return
        QApplication.clipboard().setText(
            build_table_prep_text(self._event, list(self._attendees.values()), self._panels_by_npc_id)
        )
        self._set_status(readiness.copy_status)

    def browse_rumor_pool(self) -> None:
        if self._build_result is None:
            return
        skeleton = self._build_result.debug_context.get("dashboard_skeleton")
        if not isinstance(skeleton, dict):
            skeleton = {}
        dialog = QDialog(self)
        dialog.setWindowTitle("Grounded Rumor Pool")
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser(dialog)
        browser.setOpenLinks(False)
        browser.setOpenExternalLinks(False)
        browser.setHtml(self._rumor_pool_html(skeleton))
        layout.addWidget(browser)
        close_btn = QPushButton("Close", dialog)
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        dialog.resize(760, 520)
        dialog.exec()

    def regenerate_current_npc_panel(self) -> None:
        if not self._current_npc_id:
            return
        if self._current_npc_id in self._panel_requests_by_npc:
            self._set_status(f"NPC panel is already building for {self._name_for(self._current_npc_id)}.")
            return
        self._run_panel_worker(self._current_npc_id, force=True)

    @Slot(QUrl)
    def _on_group_hovered(self, url: QUrl) -> None:
        row = self._group_for_url(url)
        if row:
            QToolTip.showText(QCursor.pos(), ', '.join(self._name_for(n) for n in row['members']), self.summaryBrowser)
        else:
            QToolTip.hideText()

    def _group_for_url(self, url: QUrl):
        text = url.toString()
        if not text.startswith('group:') or not text[6:].isdigit() or not self._event:
            return None
        return next((r for r in self._event.dashboard.get('scene_prep', {}).get('encounters', [])
                     if r['number'] == int(text[6:]) and r.get('availability') == 'present'
                     and (len(r['members']) > 1 or (
                         isinstance(r.get('conversation_cue'), dict)
                         and bool(r['conversation_cue'].get('approach'))
                     ))), None)

    @Slot(QUrl)
    def _on_anchor_clicked(self, url: QUrl) -> None:
        group = self._group_for_url(url)
        if group:
            self._current_npc_id = ''
            self._current_panel = None
            self._active_panel_request_id = None
            self.regenerateNpcBtn.setEnabled(False)
            self.copyNpcDebugBtn.setEnabled(False)
            self._update_pin_button()
            members = ''.join(f'<li>{self._npc_link(n)}</li>' for n in group['members'])
            label = self._name_for(group['members'][0]) if len(group['members']) == 1 else group.get("label") or "Conversation group"
            reason = str(group.get("reason") or "").strip()
            details = ''.join(f'<p>{html.escape(line)}</p>' for line in encounter_cue_detail_lines(group))
            reason_html = f'<p>{html.escape(reason)}</p>' if reason else ''
            self.drawer.setHtml(f'<h2>{group["number"]}. {html.escape(label)}</h2>'
                                f'<ul>{members}</ul>{reason_html}{details}'
                                '<p>Suggested arrangement; no friendship or shared knowledge is implied.</p>')
            return
        text = url.toString()
        if text.startswith("npc:"):
            npc_id = text[4:]
            if npc_id in self._attendees:
                self._open_npc(npc_id)
            return

    @Slot(QListWidgetItem)
    def _on_guest_activated(self, item: QListWidgetItem) -> None:
        npc_id = item.data(Qt.UserRole)
        if npc_id:
            self._open_npc(str(npc_id))


def build_table_prep_text(
    event: Any,
    attendees: Sequence[dict[str, str]],
    panels_by_npc_id: dict[str, dict[str, Any]],
) -> str:
    dashboard = event.dashboard if hasattr(event, "dashboard") and isinstance(event.dashboard, dict) else {}
    name_by_id = {str(item.get("npc_id") or ""): str(item.get("name") or item.get("display_name") or "") for item in attendees}
    event_info = dashboard.get("event") if isinstance(dashboard.get("event"), dict) else {}
    late_arrival_id = str(getattr(event, "late_arrival_id", "") or dashboard.get("late_arrival_id") or "")
    lines = [
        str(event_info.get("venue") or "The Lantern Room"),
        "GM TABLE PREP",
        f"Event: {event_info.get('event_type', 'social gathering')} Mood: {event_info.get('mood', '')} Late Arrival: {name_by_id.get(late_arrival_id, late_arrival_id)}",
    ]
    readiness = _club_event_readiness(event)
    if readiness.export_status:
        lines.insert(2, readiness.export_status)
    scene = dashboard.get("scene_prep")
    if isinstance(scene, dict):
        for title, rows in scene_display_sections(scene, name_by_id):
            lines.extend(["", title, *rows])
    else:
        lines.extend(["", "Room Situation"])
        first_impression = _clean_export_line(dashboard.get("first_impression"))
        if first_impression:
            lines.append(f"First impression (AI presentation): {first_impression}")
        grounded_situation = _clean_export_line(dashboard.get("room_situation")) or "No grounded prep surfaced for this section."
        lines.append(f"Grounded situation: {grounded_situation}")
    rumor_title = "Rumors in Circulation"
    rumor_selection = dashboard.get("rumor_selection") if isinstance(dashboard.get("rumor_selection"), dict) else {}
    if rumor_selection.get("selected_count") is not None and rumor_selection.get("grounded_count") is not None:
        rumor_title = (
            f"Rumors in Circulation - {rumor_selection.get('selected_count')} "
            f"selected from {rumor_selection.get('grounded_count')} grounded rumors"
        )
    fields = ((rumor_title, "rumors_in_circulation"),) if isinstance(scene, dict) else (
        ("Hot Connections", "hot_connections"),
        ("Possible Pressure", "possible_pressure"),
        (rumor_title, "rumors_in_circulation"),
        ("Guests", "guest_brief"),
    )
    for title, field in fields:
        lines.extend(["", title])
        if field == "rumors_in_circulation":
            lines.extend(_rumor_export_lines(dashboard, name_by_id))
        else:
            lines.extend(_export_bullets(dashboard.get(field) or []))
    panel_ids = _table_prep_panel_ids(event, panels_by_npc_id)
    for npc_id in panel_ids:
        panel = panels_by_npc_id[npc_id]
        presentation = panel.get("presentation") if isinstance(panel.get("presentation"), dict) else {}
        tonight = panel.get("tonight") if isinstance(panel.get("tonight"), dict) else {}
        conversation = panel.get("conversation") if isinstance(panel.get("conversation"), dict) else {}
        lines.extend(["", "NPC Panel", _clean_export_line(panel.get("name")) or name_by_id.get(npc_id, npc_id)])
        lines.extend(["", "Right Now"])
        lines.extend(_export_bullets([panel.get("current_read") or _fallback_panel_read(panel)]))
        lines.extend(["", "Play Them"])
        lines.extend(_export_bullets([presentation.get("play_cue") or tonight.get("current_demeanor")]))
        focus = tonight.get("current_desire") if isinstance(tonight.get("current_desire"), dict) else None
        agenda = presentation.get("agenda") if isinstance(presentation.get("agenda"), dict) else {}
        lines.extend(["", "Agenda Tonight"])
        lines.extend(_export_bullets([agenda.get("text") or _display_text_for_item(focus)]))
        lines.extend(["", "Who Matters Here"])
        if "who_matters" in panel:
            lines.extend(_export_bullets(relevance_display_rows(panel["who_matters"], name_by_id)))
        else:
            lines.extend(_export_bullets(_presentation_export_texts(presentation.get("people_here"), panel.get("people_here") or [])))
        metadata = panel.get("metadata") if isinstance(panel.get("metadata"), dict) else {}
        lines.extend(["", "Conversation Openings"])
        lines.extend(_export_bullets(conversation_opening_display_rows(
            panel.get("conversation_openings") if isinstance(panel.get("conversation_openings"), list) else [],
            metadata.get("conversation_openings_stage") if isinstance(metadata.get("conversation_openings_stage"), dict) else None,
        )))
        lines.extend(["", "If Approached"])
        lines.extend(
            _export_bullets(
                _presentation_export_texts(
                    presentation.get("if_approached"),
                    conversation.get("likely_subjects") or [],
                    visible_fallback=panel.get("likely_conversation") or [],
                )
            )
        )
        sensitive = _presentation_export_texts(
            presentation.get("keep_guarded"),
            conversation.get("sensitive") or [],
            visible_fallback=panel.get("sensitive_subjects") or [],
        )
        if sensitive:
            lines.extend(["", "Keep Guarded"])
            lines.extend(_export_bullets(sensitive))
        hook = _clean_export_line(panel.get("useful_hook"))
        if hook:
            lines.extend(["", "Pressure / Hook"])
            lines.extend(_export_bullets([hook]))
    return "\n".join(lines).strip() + "\n"


def _table_prep_panel_ids(event: Any, panels_by_npc_id: dict[str, dict[str, Any]]) -> list[str]:
    attendee_order = [str(npc_id) for npc_id in getattr(event, "attendee_ids", ()) or [] if str(npc_id)]
    return [npc_id for npc_id in attendee_order if isinstance(panels_by_npc_id.get(npc_id), dict)]


def _export_bullets(items: Sequence[Any]) -> list[str]:
    rows = [f"- {text}" for item in items if (text := _clean_export_line(item))]
    return rows or ["- No grounded prep surfaced for this section."]


def _rumor_export_lines(dashboard: dict[str, Any], names: dict[str, str]) -> list[str]:
    visible = [str(item).strip() for item in dashboard.get("rumors_in_circulation") or [] if str(item).strip()]
    grounded = [item for item in dashboard.get("rumors") or [] if isinstance(item, dict)]
    guidance = rumor_guidance_display_rows(dashboard.get("rumor_guidance") or [], names)
    by_rumor = {row["rumor_item_id"]: row["lines"] for row in guidance}
    result = []
    for index, text in enumerate(visible):
        result.append(f"- {text}")
        item_id = grounded[index].get("item_id") if index < len(grounded) else None
        result.extend(f"  {line}" for line in by_rumor.get(item_id, []))
    return result or ["- No grounded prep surfaced for this section."]


def _event_prep_incomplete(event: Any) -> bool:
    dashboard = event.dashboard if hasattr(event, "dashboard") and isinstance(event.dashboard, dict) else {}
    if bool(dashboard.get("scene_prep", {}).get("incomplete")):
        return True
    rumors = dashboard.get("rumors") or []
    if not rumors:
        return False
    metadata = event.metadata if hasattr(event, "metadata") and isinstance(event.metadata, dict) else {}
    stages = metadata.get("prep_stages") if isinstance(metadata.get("prep_stages"), dict) else {}
    stage = stages.get("rumor_guidance")
    return not isinstance(stage, dict) or stage.get("status") != "complete"


def _export_grounded_texts(items: Sequence[Any]) -> list[str]:
    rows: list[str] = []
    for item in items:
        if isinstance(item, dict):
            text = _clean_export_line(item.get("prep_text"))
        else:
            text = _clean_export_line(item)
        if text:
            rows.append(text)
    return rows


def _presentation_export_texts(
    cue_items: Any,
    grounded_items: Sequence[Any],
    *,
    visible_fallback: Sequence[Any] = (),
) -> list[str]:
    grounded_ids = {
        str(item.get("item_id") or "")
        for item in grounded_items
        if isinstance(item, dict) and str(item.get("item_id") or "")
    }
    rows: list[str] = []
    if isinstance(cue_items, list):
        rows = [
            text
            for item in cue_items
            if isinstance(item, dict)
            and str(item.get("item_id") or "") in grounded_ids
            and (text := _clean_export_line(item.get("text")))
        ]
    return rows or [_clean_export_line(value) for value in visible_fallback if _clean_export_line(value)] or _export_grounded_texts(grounded_items)


def _clean_export_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _source_ids_from_panel(panel: dict[str, Any]) -> set[str]:
    source_ids: set[str] = set()
    for item in _panel_grounded_items(panel):
        for source in item.get("sources") or []:
            if isinstance(source, dict) and source.get("source_id"):
                source_ids.add(str(source["source_id"]))
    for source in panel.get("interesting_detail_sources", []) or []:
        if isinstance(source, dict) and source.get("source_id"):
            source_ids.add(str(source["source_id"]))
    return source_ids


def _first_source_id(item: dict[str, Any]) -> str:
    sources = item.get("sources") or []
    first = sources[0] if sources and isinstance(sources[0], dict) else {}
    return str(first.get("source_id") or "")


def _visible_item_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    characters = [str(character) for character in item.get("characters") or []]
    subject_id = str(item.get("source_npc_id") or (characters[0] if characters else ""))
    object_id = str(item.get("target_npc_id") or (characters[1] if len(characters) > 1 else ""))
    sources = item.get("sources") or []
    source_id = ""
    if sources and isinstance(sources[0], dict):
        source_id = str(sources[0].get("source_id") or "")
    return (subject_id, object_id, str(item.get("type") or ""), source_id)


def _display_label_for_section(item: dict[str, Any], section: str) -> str:
    label = str(item.get("display_label") or item.get("type") or "")
    if section in {"rumors", "opportunities", "unresolved_business", "background_details"} and label.lower() in {
        "topic",
        "goal",
        "rumor",
        "unresolved business",
    }:
        return ""
    return label


def _display_text_for_item(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("prep_text") or item.get("summary") or "")
    return str(item or "")


def _fallback_panel_read(panel: dict[str, Any]) -> str:
    name = clean_display_text(str(panel.get("name") or panel.get("npc_id") or "This NPC")) or "This NPC"
    return f"{name} has limited attendee-specific prep in the current indexes."


def _panel_grounded_items(panel: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in panel.get("people_here", []) or []:
        if isinstance(item, dict):
            items.append(item)
    conversation = panel.get("conversation") if isinstance(panel.get("conversation"), dict) else {}
    for field in ("likely_subjects", "sensitive"):
        for item in conversation.get(field) or []:
            if isinstance(item, dict):
                items.append(item)
    tonight = panel.get("tonight") if isinstance(panel.get("tonight"), dict) else {}
    if isinstance(tonight.get("current_desire"), dict):
        items.append(tonight["current_desire"])
    if isinstance(panel.get("interesting_detail"), dict):
        items.append(panel["interesting_detail"])
    return items


def _status_for_metadata(label: str, metadata: Any) -> str:
    if not isinstance(metadata, dict):
        return f"{label} ready."
    mode = str(metadata.get("generation_mode") or "")
    mode_label = _mode_label(mode)
    from_cache = bool(metadata.get("from_cache"))
    fallback_used = bool(metadata.get("fallback_used"))
    ai_error_type = str(metadata.get("ai_error_type") or "")
    if mode == "partial_ai":
        return f"{label} ready with partial AI prep. Rumors and completed sections are available; use Retry Incomplete Prep."
    if fallback_used:
        suffix = f" AI failed: {_human_failure_label(ai_error_type)}." if ai_error_type else ""
        return f"{label} ready using deterministic fallback.{suffix}"
    if from_cache and mode == "ai":
        if ai_error_type:
            return f"{label} ready from cached AI. Regeneration failed: {_human_failure_label(ai_error_type)}."
        return f"{label} ready from cached AI."
    if from_cache and mode_label:
        return f"{label} ready from cached {mode_label}."
    if mode_label:
        return f"{label} ready from {mode_label}."
    if ai_error_type:
        return f"{label} ready with warning: {_human_failure_label(ai_error_type)}."
    return f"{label} ready."


def _mode_label(mode: str) -> str:
    if mode == "ai":
        return "AI"
    return mode.replace("_", " ") if mode else ""


def _human_failure_label(error_type: str) -> str:
    return error_type.replace("_", " ") if error_type else "unknown AI error"


def _npc_failure_label(error_type: str) -> str:
    labels = {
        "provider_missing_credentials": "provider credentials unavailable",
        "provider_authentication_failed": "provider authentication failed",
        "provider_timeout": "provider timed out",
        "provider_request_failed": "provider request failed",
        "provider_malformed_response": "provider malformed response",
        "ai_output_validation_failed": "AI output validation failed",
        "prompt_too_large": "prompt too large",
        "unknown_ai_error": "AI generation failed",
    }
    return labels.get(error_type, "AI generation failed")


def _npc_status_for_panel(panel: Any) -> str:
    metadata = panel.get("metadata") if isinstance(panel, dict) and isinstance(panel.get("metadata"), dict) else {}
    if "who_matters_stage" not in metadata:
        if "conversation_openings_stage" in metadata:
            stage = metadata.get("conversation_openings_stage")
            rows = panel.get("conversation_openings") if isinstance(panel, dict) else None
            complete = isinstance(stage, dict) and stage.get("status") == "complete" and isinstance(rows, list)
            return f"{_npc_presentation_status(metadata, partial=not complete)} {_conversation_stage_status(stage, rows)}"
        return _status_for_metadata("NPC panel", metadata)
    stage = metadata.get("who_matters_stage")
    rows = panel.get("who_matters") if isinstance(panel, dict) else None
    relevance_complete = isinstance(stage, dict) and stage.get("status") == "complete" and isinstance(rows, list)
    conversation_stage = metadata.get("conversation_openings_stage")
    has_conversation_stage = "conversation_openings_stage" in metadata
    conversation_rows = panel.get("conversation_openings") if isinstance(panel, dict) else None
    conversation_complete = (
        isinstance(conversation_stage, dict)
        and conversation_stage.get("status") == "complete"
        and isinstance(conversation_rows, list)
    )
    presentation = _npc_presentation_status(
        metadata, partial=not relevance_complete or (has_conversation_stage and not conversation_complete),
    )
    if not relevance_complete:
        reason = stage.get("reason") if isinstance(stage, dict) else None
        relevance = f"Relevance unavailable: {_relevance_failure_label(reason)}."
        if not has_conversation_stage:
            return f"{presentation} {relevance}"
        conversation = _conversation_stage_status(conversation_stage, conversation_rows)
        return f"{presentation} {relevance} {conversation}"

    from_cache = bool(stage.get("from_cache"))
    if rows:
        relevance = "Relevance ready from cached AI." if from_cache else "Relevance ready from AI."
    elif from_cache:
        relevance = "Relevance complete from cached AI; no grounded material surfaced."
    else:
        relevance = "Relevance complete; no grounded material surfaced."
    if stage.get("regeneration_failed"):
        relevance += f" Relevance regeneration failed: {_relevance_failure_label(stage.get('reason'))}."
    if not has_conversation_stage:
        return f"{presentation} {relevance}"
    return f"{presentation} {relevance} {_conversation_stage_status(conversation_stage, conversation_rows)}"


def _conversation_stage_status(stage: Any, rows: Any) -> str:
    complete = isinstance(stage, dict) and stage.get("status") == "complete" and isinstance(rows, list)
    if not complete:
        reason = stage.get("reason") if isinstance(stage, dict) else None
        return f"Conversation openings unavailable: {_conversation_failure_label(reason)}."
    from_cache = bool(stage.get("from_cache"))
    if rows:
        text = "Conversation openings ready from cached AI." if from_cache else "Conversation openings ready from AI."
    elif from_cache:
        text = "Conversation openings complete from cached AI; no grounded material surfaced."
    else:
        text = "Conversation openings complete; no grounded material surfaced."
    if stage.get("regeneration_failed"):
        text += f" Conversation-opening regeneration failed: {_conversation_failure_label(stage.get('reason'))}."
    return text


def _conversation_failure_label(reason: Any) -> str:
    labels = {
        "deterministic": "AI conversation-opening analysis was not run",
        "reading_unavailable": "grounded evidence unavailable",
        "conversation_evidence_unavailable": "grounded evidence unavailable",
        "easy_requires_public_evidence": "grounded evidence unavailable",
        "invalid_conversation_openings": "conversation-opening output did not validate",
        "invalid_conversation_lane": "conversation-opening output did not validate",
        "duplicate_conversation_opening": "conversation-opening output did not validate",
        "scripted_or_pronominal_conversation": "conversation-opening output did not validate",
        "unsupported_attendee_name": "conversation-opening output did not validate",
        "validation": "conversation-opening output did not validate",
        "invalid_proposal": "conversation-opening output did not validate",
    }
    if reason in labels:
        return labels[reason]
    shared = _relevance_failure_label(reason)
    return "conversation-opening analysis failed" if shared == "relevance analysis failed" else shared


def _npc_presentation_status(metadata: dict[str, Any], *, partial: bool) -> str:
    readiness = "partially prepared" if partial else "ready"
    mode = str(metadata.get("generation_mode") or "")
    from_cache = bool(metadata.get("from_cache"))
    fallback_used = bool(metadata.get("fallback_used"))
    error_type = str(metadata.get("ai_error_type") or "")
    if fallback_used or mode == "deterministic_fallback":
        status = f"NPC panel {readiness} using deterministic fallback."
        if error_type:
            status += f" AI presentation failed: {_npc_failure_label(error_type)}."
        return status
    if mode == "ai":
        status = f"NPC panel {readiness} from {'cached AI' if from_cache else 'AI'}."
        if from_cache and error_type:
            status += f" Presentation regeneration failed: {_npc_failure_label(error_type)}."
        elif error_type:
            status += f" AI presentation warning: {_npc_failure_label(error_type)}."
        return status
    if mode == "deterministic":
        return f"NPC panel {readiness} from {'cached ' if from_cache else ''}deterministic preparation."
    status = f"NPC panel {readiness}{' from cache' if from_cache else ''}."
    if error_type:
        status += f" AI presentation warning: {_npc_failure_label(error_type)}."
    return status


def _relevance_failure_label(reason: Any) -> str:
    labels = {
        "missing_credentials": "provider credentials unavailable",
        "provider_missing_credentials": "provider credentials unavailable",
        "authentication": "provider authentication failed",
        "provider_authentication_failed": "provider authentication failed",
        "provider_timeout": "provider timed out",
        "transport": "provider request failed",
        "provider_request_failed": "provider request failed",
        "malformed_json": "provider malformed response",
        "provider_malformed_response": "provider malformed response",
        "prompt_budget": "preparation limit reached",
        "request_budget": "preparation limit reached",
        "card_budget": "preparation limit reached",
        "required_evidence_budget": "preparation limit reached",
        "required_evidence_unavailable": "grounded evidence unavailable",
        "candidate_evidence_unavailable": "grounded evidence unavailable",
        "incomplete_annotation_coverage": "grounded evidence unavailable",
        "empty_evidence": "grounded evidence unavailable",
        "missing_character_support": "grounded evidence unavailable",
        "unknown_passage": "grounded evidence unavailable",
        "deterministic": "AI relevance was not run",
        "provider_stopped": "provider unavailable",
        "validation": "relevance output did not validate",
        "invalid_proposal": "relevance output did not validate",
        "invalid_relevance": "relevance output did not validate",
        "invalid_relevance_target": "relevance output did not validate",
        "unsupported_established_relationship": "relevance output did not validate",
    }
    return labels.get(reason, "relevance analysis failed")


__all__ = ["ClubEventWorker", "ClubPanelWorker", "ClubTab"]
