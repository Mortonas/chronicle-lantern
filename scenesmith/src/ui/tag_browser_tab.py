from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6 import QtCore
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.obsidian_open import open_in_obsidian
from core.tag_index import TagSummary, build_tag_index


_GLOBAL_ACTIVE_WORKERS: list["TagScanWorker"] = []


class TagScanSignals(QObject):
    done = Signal(list)
    error = Signal(str)


class TagScanWorker(QtCore.QRunnable):
    def __init__(self, vault_path: str) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.vault_path = vault_path
        self.signals = TagScanSignals()

    def run(self) -> None:  # type: ignore[override]
        try:
            summaries = build_tag_index(self.vault_path)
            self.signals.done.emit(summaries)
        except Exception as exc:  # pylint: disable=broad-except
            self.signals.error.emit(str(exc))


class TagBrowserTab(QWidget):
    addSceneGroupRequested = Signal(str)
    addGuestTagsRequested = Signal(str)
    addGuestAnchorsRequested = Signal(str)
    tagIndexUpdated = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._vault_path: Optional[str] = None
        self._vault_name: Optional[str] = None
        self._summaries: list[TagSummary] = []
        self._visible_summaries: list[TagSummary] = []
        self._selected_summary: Optional[TagSummary] = None
        self._autocomplete_scan_running = False
        self._active_workers: list[TagScanWorker] = []
        self._pool = QtCore.QThreadPool.globalInstance()
        self._build_ui()
        self._wire_signals()
        self._set_status("Idle.")

    def set_vault(self, vault_path: Optional[str], vault_name: Optional[str] = None) -> None:
        self._vault_path = vault_path
        self._vault_name = vault_name

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("tag name")
        top_row.addWidget(self.filter_edit, 1)
        self.refresh_btn = QPushButton("Refresh")
        top_row.addWidget(self.refresh_btn)
        self.status_label = QLabel()
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top_row.addWidget(self.status_label, 1)
        root.addLayout(top_row)

        action_row = QHBoxLayout()
        self.add_scene_btn = QPushButton("Add to Scene Group")
        self.add_guest_tags_btn = QPushButton("Add to Guest Tags")
        self.add_guest_anchors_btn = QPushButton("Add to Guest Anchors")
        action_row.addWidget(self.add_scene_btn)
        action_row.addWidget(self.add_guest_tags_btn)
        action_row.addWidget(self.add_guest_anchors_btn)
        action_row.addStretch(1)
        root.addLayout(action_row)

        tables_row = QHBoxLayout()
        self.tag_table = QTableWidget(0, 2)
        self.tag_table.setHorizontalHeaderLabels(["Tag", "Files"])
        self.tag_table.horizontalHeader().setStretchLastSection(False)
        self.tag_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        tables_row.addWidget(self.tag_table, 1)

        self.file_table = QTableWidget(0, 2)
        self.file_table.setHorizontalHeaderLabels(["File", "Path"])
        self.file_table.horizontalHeader().setStretchLastSection(True)
        self.file_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        tables_row.addWidget(self.file_table, 2)
        root.addLayout(tables_row, 1)
        self._update_action_state()

    def _wire_signals(self) -> None:
        self.refresh_btn.clicked.connect(self.refresh)
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.tag_table.itemSelectionChanged.connect(self._on_tag_selection_changed)
        self.file_table.itemActivated.connect(self._on_file_activated)
        self.file_table.itemDoubleClicked.connect(self._on_file_activated)
        self.add_scene_btn.clicked.connect(lambda: self._emit_selected(self.addSceneGroupRequested))
        self.add_guest_tags_btn.clicked.connect(lambda: self._emit_selected(self.addGuestTagsRequested))
        self.add_guest_anchors_btn.clicked.connect(lambda: self._emit_selected(self.addGuestAnchorsRequested))

    def refresh(self) -> None:
        if not self._vault_path:
            self._set_status("No vault configured.")
            return
        vault = Path(self._vault_path).expanduser()
        if not vault.exists() or not vault.is_dir():
            self._set_status(f"Vault not found: {vault}")
            return
        self.refresh_btn.setEnabled(False)
        self._set_status("Scanning...")
        worker = TagScanWorker(str(vault))
        worker.signals.done.connect(lambda summaries, scan_worker=worker: self._finish_scan_worker(scan_worker, summaries))
        worker.signals.error.connect(lambda message, scan_worker=worker: self._finish_scan_worker(scan_worker, message, error=True))
        self._start_scan_worker(worker)

    def refresh_autocomplete(self, limit: int = 20) -> None:
        if self._autocomplete_scan_running or not self._vault_path:
            return
        vault = Path(self._vault_path).expanduser()
        if not vault.exists() or not vault.is_dir():
            return
        self._autocomplete_scan_running = True
        worker = TagScanWorker(str(vault))
        worker.signals.done.connect(
            lambda summaries, max_tags=limit, scan_worker=worker: self._finish_autocomplete_worker(
                scan_worker, summaries, max_tags
            )
        )
        worker.signals.error.connect(
            lambda message, scan_worker=worker: self._finish_autocomplete_worker(scan_worker, message, 0, error=True)
        )
        self._start_scan_worker(worker)

    def _start_scan_worker(self, worker: TagScanWorker) -> None:
        self._active_workers.append(worker)
        _GLOBAL_ACTIVE_WORKERS.append(worker)
        self._pool.start(worker)

    def _release_scan_worker(self, worker: TagScanWorker) -> None:
        if worker in self._active_workers:
            self._active_workers.remove(worker)
        if worker in _GLOBAL_ACTIVE_WORKERS:
            _GLOBAL_ACTIVE_WORKERS.remove(worker)

    def _finish_scan_worker(self, worker: TagScanWorker, payload, error: bool = False) -> None:
        self._release_scan_worker(worker)
        if error:
            self._on_scan_error(str(payload))
        else:
            self._on_scan_done(payload)

    def _finish_autocomplete_worker(self, worker: TagScanWorker, payload, limit: int, error: bool = False) -> None:
        self._release_scan_worker(worker)
        if error:
            self._on_autocomplete_scan_error(str(payload))
        else:
            self._on_autocomplete_scan_done(payload, limit)

    def _on_scan_done(self, summaries: list[TagSummary]) -> None:
        self.refresh_btn.setEnabled(True)
        self._summaries = summaries
        self._apply_filter()
        self.tagIndexUpdated.emit([f"#{summary.tag}" for summary in summaries])
        total_files = len({str(path) for summary in summaries for path in summary.files})
        self._set_status(f"{len(summaries)} tags in {total_files} files.")

    def _on_scan_error(self, message: str) -> None:
        self.refresh_btn.setEnabled(True)
        self._set_status(f"Scan error: {message}")

    def _on_autocomplete_scan_done(self, summaries: list[TagSummary], limit: int) -> None:
        self._autocomplete_scan_running = False
        self.tagIndexUpdated.emit([f"#{summary.tag}" for summary in summaries[: max(0, limit)]])
        if not self._summaries:
            self._set_status(f"Autocomplete ready: top {min(len(summaries), max(0, limit))} tags.")

    def _on_autocomplete_scan_error(self, _message: str) -> None:
        self._autocomplete_scan_running = False

    def _apply_filter(self) -> None:
        query = self.filter_edit.text().strip().lower()
        if query.startswith("#"):
            query = query[1:]
        if query:
            self._visible_summaries = [summary for summary in self._summaries if query in summary.tag]
        else:
            self._visible_summaries = list(self._summaries)
        self._populate_tags()

    def _populate_tags(self) -> None:
        self.tag_table.setRowCount(0)
        for summary in self._visible_summaries:
            row = self.tag_table.rowCount()
            self.tag_table.insertRow(row)
            tag_item = QTableWidgetItem(f"#{summary.tag}")
            tag_item.setData(Qt.UserRole, summary)
            count_item = QTableWidgetItem(str(summary.file_count))
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.tag_table.setItem(row, 0, tag_item)
            self.tag_table.setItem(row, 1, count_item)
        self._selected_summary = None
        self._populate_files(None)
        self._update_action_state()

    def _on_tag_selection_changed(self) -> None:
        items = self.tag_table.selectedItems()
        summary = None
        if items:
            row = items[0].row()
            item = self.tag_table.item(row, 0)
            if item is not None:
                data = item.data(Qt.UserRole)
                if isinstance(data, TagSummary):
                    summary = data
        self._selected_summary = summary
        self._populate_files(summary)
        self._update_action_state()

    def _populate_files(self, summary: Optional[TagSummary]) -> None:
        self.file_table.setRowCount(0)
        if summary is None:
            return
        for path in summary.files:
            row = self.file_table.rowCount()
            self.file_table.insertRow(row)
            name_item = QTableWidgetItem(path.name)
            path_item = QTableWidgetItem(str(path))
            path_item.setData(Qt.UserRole, str(path))
            self.file_table.setItem(row, 0, name_item)
            self.file_table.setItem(row, 1, path_item)

    def _on_file_activated(self, item: QTableWidgetItem) -> None:
        row = item.row()
        path_item = self.file_table.item(row, 1)
        if path_item is None:
            return
        file_path = path_item.text()
        if not open_in_obsidian(file_path, vault_path=self._vault_path, vault_name=self._vault_name):
            print(f"[WARN] TagBrowserTab: failed to open in Obsidian: {file_path}")

    def _emit_selected(self, signal: Signal) -> None:
        if self._selected_summary is None:
            return
        signal.emit(f"#{self._selected_summary.tag}")

    def _update_action_state(self) -> None:
        enabled = self._selected_summary is not None
        self.add_scene_btn.setEnabled(enabled)
        self.add_guest_tags_btn.setEnabled(enabled)
        self.add_guest_anchors_btn.setEnabled(enabled)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)


__all__ = ["TagBrowserTab", "TagScanWorker"]
