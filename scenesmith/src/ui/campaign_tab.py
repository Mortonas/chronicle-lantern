from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.campaign_state import (
    ACTIVE_PLOT_STATES,
    OBLIGATION_TIERS,
    CONSEQUENCE_TIMINGS,
    DEFAULT_CAMPAIGN_NOTE_DIR,
    TIMING_SORT_ORDER,
    ActivePlot,
    Obligation,
    CampaignState,
    Crisis,
    CurrentCampaign,
    FactionClock,
    NextConsequence,
    RecentEvent,
    default_campaign_state,
    export_campaign_state_note,
    load_campaign_state,
    save_campaign_state,
)


class CampaignTab(QWidget):
    dirtyChanged = Signal(bool)

    def __init__(
        self,
        state_path: Path | str | None = None,
        parent=None,
        note_dir: Path | str | None = None,
        now_func=None,
    ) -> None:
        super().__init__(parent)
        self._state_path = Path(state_path) if state_path is not None else None
        self._note_dir = Path(note_dir) if note_dir is not None else DEFAULT_CAMPAIGN_NOTE_DIR
        self._now_func = now_func or datetime.now
        self._dirty = False
        self._suppress_dirty = False
        self._build_ui()
        self._wire_signals()
        self.set_state(default_campaign_state(), status="No campaign state saved yet.")
        self.load_from_disk(confirm_dirty=False)

    def is_dirty(self) -> bool:
        return self._dirty

    def set_state(self, state: CampaignState, *, status: str = "") -> None:
        self._suppress_dirty = True
        try:
            self.night_edit.setText(state.current.night)
            self.date_edit.setText(state.current.date)
            self.haven_edit.setText(state.current.haven)
            self.domain_edit.setText(state.current.domain)
            self.status_notes_edit.setPlainText(state.current.status_notes)
            self._populate_table(self.crises_table, state.crises, self._add_crisis_row)
            self._populate_table(self.faction_table, state.faction_clocks, self._add_faction_row)
            self._populate_table(self.plots_table, state.active_plots, self._add_plot_row)
            self._populate_table(self.obligations_table, state.obligations, self._add_obligation_row)
            self._populate_table(self.events_table, state.recent_events, self._add_event_row)
            self._populate_table(self.past_events_table, state.past_events, self._add_past_event_row)
            consequences = sorted(
                state.next_consequences,
                key=lambda item: TIMING_SORT_ORDER.get(item.timing, len(TIMING_SORT_ORDER)),
            )
            self._populate_table(self.consequences_table, consequences, self._add_consequence_row)
            self._apply_visibility_filter()
        finally:
            self._suppress_dirty = False
        self._set_dirty(False)
        if status:
            self._set_status(status)

    def load_from_disk(self, *, confirm_dirty: bool = True) -> bool:
        if self._state_path is None:
            self._set_status("No campaign state path configured.")
            return False
        if confirm_dirty and self._dirty and not self.confirm_reload_changes():
            return False
        existed = self._state_path.exists()
        try:
            state = load_campaign_state(self._state_path)
        except ValueError as exc:
            self._show_error("Campaign State Error", str(exc))
            self._set_status("Campaign state load failed.")
            return False
        status = "Loaded campaign state." if existed else "No campaign state saved yet."
        self.set_state(state, status=status)
        return True

    def save_to_disk(self) -> bool:
        if self._state_path is None:
            self._set_status("No campaign state path configured.")
            return False
        try:
            real_date = self._real_date_text()
            state = self.campaign_state_from_ui(real_date=real_date)
            self._validate_save_ready(state)
            note_path = export_campaign_state_note(self._note_dir, state, real_date=real_date)
            rolled_state = replace(
                state,
                recent_events=[],
                past_events=[*state.past_events, *state.recent_events],
            )
            save_campaign_state(self._state_path, rolled_state)
        except ValueError as exc:
            self._show_error("Campaign State Error", str(exc))
            self._set_status("Campaign state save failed.")
            return False
        self.set_state(rolled_state)
        self._set_dirty(False)
        self._set_status(f"Saved campaign state and created {note_path.name}.")
        return True

    def campaign_state_from_ui(self, *, real_date: str | None = None) -> CampaignState:
        event_date = real_date or self._real_date_text()
        return CampaignState(
            current=CurrentCampaign(
                night=self.night_edit.text().strip(),
                date=self.date_edit.text().strip(),
                haven=self.haven_edit.text().strip(),
                domain=self.domain_edit.text().strip(),
                status_notes=self.status_notes_edit.toPlainText().strip(),
            ),
            crises=[
                Crisis(
                    title=self._text_item(self.crises_table, row, 0),
                    pressure=self._text_item(self.crises_table, row, 1),
                    clock=self._int_item(self.crises_table, row, 2, "crises", "clock"),
                    max_clock=self._int_item(self.crises_table, row, 3, "crises", "max_clock"),
                    next_consequence=self._text_item(self.crises_table, row, 4),
                )
                for row in range(self.crises_table.rowCount())
            ],
            faction_clocks=[
                FactionClock(
                    faction=self._text_item(self.faction_table, row, 0),
                    goal=self._text_item(self.faction_table, row, 1),
                    clock=self._int_item(self.faction_table, row, 2, "faction_clocks", "clock"),
                    max_clock=self._int_item(self.faction_table, row, 3, "faction_clocks", "max_clock"),
                    visible_to_players=self._checkbox_value(self.faction_table, row, 4),
                    omen=self._text_item(self.faction_table, row, 6),
                )
                for row in range(self.faction_table.rowCount())
            ],
            active_plots=[
                ActivePlot(
                    title=self._text_item(self.plots_table, row, 0),
                    state=self._combo_value(self.plots_table, row, 1, "active_plots", "state"),
                    npc=self._text_item(self.plots_table, row, 2),
                    faction=self._text_item(self.plots_table, row, 3),
                    next_step=self._text_item(self.plots_table, row, 4),
                )
                for row in range(self.plots_table.rowCount())
            ],
            obligations=[
                Obligation(
                    creditor=self._text_item(self.obligations_table, row, 0),
                    debtor=self._text_item(self.obligations_table, row, 1),
                    tier=self._combo_value(self.obligations_table, row, 2, "obligations", "tier"),
                    reason=self._text_item(self.obligations_table, row, 3),
                    public=self._checkbox_value(self.obligations_table, row, 4),
                    current_use=self._text_item(self.obligations_table, row, 6),
                )
                for row in range(self.obligations_table.rowCount())
            ],
            recent_events=[
                RecentEvent(
                    date=event_date,
                    summary=self._text_item(self.events_table, row, 1),
                    fallout=self._text_item(self.events_table, row, 2),
                )
                for row in range(self.events_table.rowCount())
            ],
            past_events=[
                RecentEvent(
                    date=self._text_item(self.past_events_table, row, 0),
                    summary=self._text_item(self.past_events_table, row, 1),
                    fallout=self._text_item(self.past_events_table, row, 2),
                )
                for row in range(self.past_events_table.rowCount())
            ],
            next_consequences=[
                NextConsequence(
                    source=self._text_item(self.consequences_table, row, 0),
                    consequence=self._text_item(self.consequences_table, row, 1),
                    timing=self._combo_value(self.consequences_table, row, 2, "next_consequences", "timing"),
                )
                for row in range(self.consequences_table.rowCount())
            ],
        )

    def confirm_reload_changes(self) -> bool:
        result = QMessageBox.question(
            self,
            "Unsaved Campaign Changes",
            "Reload campaign state from disk?\n\nUnsaved edits in this tab will be replaced.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return result == QMessageBox.Yes

    def confirm_leave_with_unsaved_changes(self) -> bool:
        result = QMessageBox.question(
            self,
            "Unsaved Campaign Changes",
            "Leave the Campaign tab?\n\nYour unsaved campaign edits will stay here until you save or reload.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return result == QMessageBox.Yes

    def confirm_close_with_unsaved_changes(self) -> bool:
        result = QMessageBox.question(
            self,
            "Unsaved Campaign Changes",
            "Close SceneSmith?\n\nUnsaved campaign edits will be lost.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return result == QMessageBox.Yes

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        action_row = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.setToolTip("Validate, create an Obsidian note, and save the campaign dashboard state.")
        self.reload_btn = QPushButton("Reload")
        self.reload_btn.setToolTip("Reload campaign state from disk.")
        self.hide_hidden_chk = QCheckBox("Hide player-hidden rows")
        self.hide_hidden_chk.setToolTip("Temporarily hide hidden faction clocks and private obligations.")
        self.status_label = QLabel()
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        action_row.addWidget(self.save_btn)
        action_row.addWidget(self.reload_btn)
        action_row.addWidget(self.hide_hidden_chk)
        action_row.addStretch(1)
        action_row.addWidget(self.status_label, 2)
        root.addLayout(action_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll, 1)
        body = QWidget()
        scroll.setWidget(body)
        body_layout = QVBoxLayout(body)

        current_group = QGroupBox("Current")
        current_form = QFormLayout(current_group)
        self.real_date_label = QLabel(self._real_date_text())
        self.real_date_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.night_edit = QLineEdit()
        self.night_edit.setPlaceholderText("Night 3")
        self.date_edit = QLineEdit()
        self.date_edit.setPlaceholderText("Required game date")
        self.haven_edit = QLineEdit()
        self.haven_edit.setPlaceholderText("Home base")
        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText("Neighborhood or area")
        self.status_notes_edit = QTextEdit()
        self.status_notes_edit.setPlaceholderText("Status, community pressure, or standing notes")
        self.status_notes_edit.setFixedHeight(80)
        current_form.addRow("Real Date:", self.real_date_label)
        current_form.addRow("Night:", self.night_edit)
        current_form.addRow("Game Date:", self.date_edit)
        current_form.addRow("Haven:", self.haven_edit)
        current_form.addRow("Domain:", self.domain_edit)
        current_form.addRow("Status Notes:", self.status_notes_edit)
        body_layout.addWidget(current_group)

        self.crises_table = self._section_table(
            body_layout,
            "Crises",
            ["Title", "Pressure", "Clock", "Max", "Next Consequence"],
            lambda: self._add_crisis_row(Crisis(), dirty=True),
        )
        self.faction_table = self._section_table(
            body_layout,
            "Faction Clocks",
            ["Faction", "Goal", "Clock", "Max", "Visible?", "Marker", "Omen"],
            lambda: self._add_faction_row(FactionClock(), dirty=True),
        )
        self.plots_table = self._section_table(
            body_layout,
            "Active Plots",
            ["Title", "State", "NPC", "Faction", "Next Step"],
            lambda: self._add_plot_row(ActivePlot(), dirty=True),
        )
        self.obligations_table = self._section_table(
            body_layout,
            "Obligations",
            ["Creditor", "Debtor", "Tier", "Reason", "Public?", "Marker", "Current Use"],
            lambda: self._add_obligation_row(Obligation(), dirty=True),
        )
        self.events_table = self._section_table(
            body_layout,
            "Recent Events",
            ["Auto Date", "Summary", "Fallout"],
            lambda: self._add_event_row(RecentEvent(), dirty=True),
        )
        self.past_events_table = self._section_table(
            body_layout,
            "Past Events",
            ["Date", "Summary", "Fallout"],
            lambda: self._add_past_event_row(RecentEvent(date=self._real_date_text()), dirty=True),
        )
        self.consequences_table = self._section_table(
            body_layout,
            "Next Consequences",
            ["Source", "Consequence", "Timing"],
            lambda: self._add_consequence_row(NextConsequence(), dirty=True),
        )
        body_layout.addStretch(1)

    def _wire_signals(self) -> None:
        self.save_btn.clicked.connect(self.save_to_disk)
        self.reload_btn.clicked.connect(lambda: self.load_from_disk(confirm_dirty=True))
        self.hide_hidden_chk.toggled.connect(self._apply_visibility_filter)
        for edit in (self.night_edit, self.date_edit, self.haven_edit, self.domain_edit):
            edit.textChanged.connect(self._mark_dirty)
        self.status_notes_edit.textChanged.connect(self._mark_dirty)

    def _section_table(
        self,
        parent_layout: QVBoxLayout,
        title: str,
        headers: list[str],
        add_row: Callable[[], None],
    ) -> QTableWidget:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        buttons = QHBoxLayout()
        add_btn = QPushButton("Add Row")
        remove_btn = QPushButton("Remove Selected")
        object_prefix = title.lower().replace(" ", "_")
        add_btn.setObjectName(f"{object_prefix}_add_btn")
        remove_btn.setObjectName(f"{object_prefix}_remove_btn")
        buttons.addWidget(add_btn)
        buttons.addWidget(remove_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        for column, header in enumerate(headers):
            table.horizontalHeaderItem(column).setToolTip(self._header_tooltip(header))
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.verticalHeader().setDefaultSectionSize(28)
        self._resize_table_for_rows(table)
        table.itemChanged.connect(self._mark_dirty)
        layout.addWidget(table)

        add_btn.clicked.connect(lambda _checked=False: add_row())
        remove_btn.clicked.connect(lambda _checked=False: self._remove_selected_rows(table))
        parent_layout.addWidget(group)
        return table

    def _populate_table(self, table: QTableWidget, rows: list, add_row: Callable) -> None:
        table.blockSignals(True)
        try:
            table.setRowCount(0)
            for row_data in rows:
                add_row(row_data, dirty=False)
        finally:
            table.blockSignals(False)
        self._resize_table_for_rows(table)

    def _add_crisis_row(self, item: Crisis, *, dirty: bool = False) -> None:
        row = self._append_row(self.crises_table)
        self._set_text_item(self.crises_table, row, 0, item.title)
        self._set_text_item(self.crises_table, row, 1, item.pressure)
        self._set_text_item(self.crises_table, row, 2, str(item.clock))
        self._set_text_item(self.crises_table, row, 3, str(item.max_clock))
        self._set_text_item(self.crises_table, row, 4, item.next_consequence)
        self._finish_added_row(self.crises_table, row, dirty)

    def _add_faction_row(self, item: FactionClock, *, dirty: bool = False) -> None:
        row = self._append_row(self.faction_table)
        self._set_text_item(self.faction_table, row, 0, item.faction)
        self._set_text_item(self.faction_table, row, 1, item.goal)
        self._set_text_item(self.faction_table, row, 2, str(item.clock))
        self._set_text_item(self.faction_table, row, 3, str(item.max_clock))
        self._set_checkbox(self.faction_table, row, 4, item.visible_to_players)
        self._set_marker(self.faction_table, row, 5, item.visible_to_players)
        self._set_text_item(self.faction_table, row, 6, item.omen)
        self._finish_added_row(self.faction_table, row, dirty)

    def _add_plot_row(self, item: ActivePlot, *, dirty: bool = False) -> None:
        row = self._append_row(self.plots_table)
        self._set_text_item(self.plots_table, row, 0, item.title)
        self._set_combo(self.plots_table, row, 1, ACTIVE_PLOT_STATES, item.state)
        self._set_text_item(self.plots_table, row, 2, item.npc)
        self._set_text_item(self.plots_table, row, 3, item.faction)
        self._set_text_item(self.plots_table, row, 4, item.next_step)
        self._finish_added_row(self.plots_table, row, dirty)

    def _add_obligation_row(self, item: Obligation, *, dirty: bool = False) -> None:
        row = self._append_row(self.obligations_table)
        self._set_text_item(self.obligations_table, row, 0, item.creditor)
        self._set_text_item(self.obligations_table, row, 1, item.debtor)
        self._set_combo(self.obligations_table, row, 2, OBLIGATION_TIERS, item.tier)
        self._set_text_item(self.obligations_table, row, 3, item.reason)
        self._set_checkbox(self.obligations_table, row, 4, item.public)
        self._set_marker(self.obligations_table, row, 5, item.public)
        self._set_text_item(self.obligations_table, row, 6, item.current_use)
        self._finish_added_row(self.obligations_table, row, dirty)

    def _add_event_row(self, item: RecentEvent, *, dirty: bool = False) -> None:
        row = self._append_row(self.events_table)
        self._set_text_item(self.events_table, row, 0, item.date or self._real_date_text(), editable=False)
        self._set_text_item(self.events_table, row, 1, item.summary)
        self._set_text_item(self.events_table, row, 2, item.fallout)
        self._finish_added_row(self.events_table, row, dirty)

    def _add_past_event_row(self, item: RecentEvent, *, dirty: bool = False) -> None:
        row = self._append_row(self.past_events_table)
        self._set_text_item(self.past_events_table, row, 0, item.date)
        self._set_text_item(self.past_events_table, row, 1, item.summary)
        self._set_text_item(self.past_events_table, row, 2, item.fallout)
        self._finish_added_row(self.past_events_table, row, dirty)

    def _add_consequence_row(self, item: NextConsequence, *, dirty: bool = False) -> None:
        row = self._append_row(self.consequences_table)
        self._set_text_item(self.consequences_table, row, 0, item.source)
        self._set_text_item(self.consequences_table, row, 1, item.consequence)
        self._set_combo(self.consequences_table, row, 2, CONSEQUENCE_TIMINGS, item.timing)
        self._finish_added_row(self.consequences_table, row, dirty)

    def _append_row(self, table: QTableWidget) -> int:
        row = table.rowCount()
        table.insertRow(row)
        return row

    def _set_text_item(self, table: QTableWidget, row: int, column: int, text: str, *, editable: bool = True) -> None:
        item = QTableWidgetItem(text)
        header = table.horizontalHeaderItem(column).text() if table.horizontalHeaderItem(column) else ""
        item.setToolTip(self._cell_tooltip(header))
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        table.setItem(row, column, item)

    def _set_combo(self, table: QTableWidget, row: int, column: int, choices: tuple[str, ...], value: str) -> None:
        combo = QComboBox()
        combo.addItems(list(choices))
        combo.setToolTip(f"Choose one: {', '.join(choices)}")
        if value in choices:
            combo.setCurrentText(value)
        combo.currentIndexChanged.connect(self._mark_dirty)
        table.setCellWidget(row, column, combo)

    def _set_checkbox(self, table: QTableWidget, row: int, column: int, checked: bool) -> None:
        checkbox = QCheckBox()
        checkbox.setToolTip("Checked means safe to show players; unchecked means hidden GM-only information.")
        checkbox.setChecked(checked)
        checkbox.stateChanged.connect(lambda _state, table=table: self._on_visibility_checkbox_changed(table))
        table.setCellWidget(row, column, checkbox)

    def _set_marker(self, table: QTableWidget, row: int, column: int, public: bool) -> None:
        label = "Public" if public else "Hidden"
        item = QTableWidgetItem(label)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setTextAlignment(Qt.AlignCenter)
        item.setToolTip("Visible to players" if public else "Hidden from players")
        table.setItem(row, column, item)

    def _remove_selected_rows(self, table: QTableWidget) -> None:
        rows = sorted({item.row() for item in table.selectedItems()}, reverse=True)
        if not rows and table.currentRow() >= 0:
            rows = [table.currentRow()]
        for row in rows:
            table.removeRow(row)
        if rows:
            self._resize_table_for_rows(table)
            self._mark_dirty()
            self._apply_visibility_filter()

    def _on_visibility_checkbox_changed(self, table: QTableWidget) -> None:
        self._update_visibility_markers(table)
        self._apply_visibility_filter()
        self._mark_dirty()

    def _update_visibility_markers(self, table: QTableWidget) -> None:
        if table is self.faction_table:
            checkbox_col, marker_col = 4, 5
        elif table is self.obligations_table:
            checkbox_col, marker_col = 4, 5
        else:
            return
        for row in range(table.rowCount()):
            self._set_marker(table, row, marker_col, self._checkbox_value(table, row, checkbox_col))

    def _apply_visibility_filter(self) -> None:
        hide_hidden = self.hide_hidden_chk.isChecked()
        for row in range(self.faction_table.rowCount()):
            hidden = not self._checkbox_value(self.faction_table, row, 4)
            self.faction_table.setRowHidden(row, hide_hidden and hidden)
        for row in range(self.obligations_table.rowCount()):
            hidden = not self._checkbox_value(self.obligations_table, row, 4)
            self.obligations_table.setRowHidden(row, hide_hidden and hidden)

    def _text_item(self, table: QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        return item.text().strip() if item is not None else ""

    def _int_item(self, table: QTableWidget, row: int, column: int, section: str, field: str) -> int:
        raw = self._text_item(table, row, column)
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"{section}[{row}].{field}: {raw!r} must be an integer") from exc

    def _combo_value(self, table: QTableWidget, row: int, column: int, section: str, field: str) -> str:
        combo = table.cellWidget(row, column)
        if not isinstance(combo, QComboBox):
            raise ValueError(f"{section}[{row}].{field}: missing dropdown")
        return combo.currentText()

    def _checkbox_value(self, table: QTableWidget, row: int, column: int) -> bool:
        checkbox = table.cellWidget(row, column)
        return bool(checkbox.isChecked()) if isinstance(checkbox, QCheckBox) else False

    def _mark_dirty(self) -> None:
        if self._suppress_dirty:
            return
        self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        if self._dirty == dirty:
            return
        self._dirty = dirty
        self.dirtyChanged.emit(dirty)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def _validate_save_ready(self, state: CampaignState) -> None:
        if not state.current.date.strip():
            raise ValueError("current.date: game date is required")

    def _real_date_text(self) -> str:
        return self._now_func().strftime("%Y-%m-%d")

    def _finish_added_row(self, table: QTableWidget, row: int, dirty: bool) -> None:
        self._resize_table_for_rows(table)
        table.selectRow(row)
        first_item = table.item(row, 0)
        if first_item is not None:
            table.scrollToItem(first_item, QAbstractItemView.PositionAtCenter)
        self._apply_visibility_filter()
        if dirty:
            self._mark_dirty()

    @staticmethod
    def _resize_table_for_rows(table: QTableWidget) -> None:
        visible_rows = max(2, min(table.rowCount(), 6))
        header_height = table.horizontalHeader().height()
        row_height = table.verticalHeader().defaultSectionSize()
        frame = table.frameWidth() * 2
        table.setMinimumHeight(header_height + (row_height * visible_rows) + frame + 12)

    @staticmethod
    def _header_tooltip(header: str) -> str:
        tooltips = {
            "Clock": "Current clock value. Must be a whole number from 0 through Max.",
            "Max": "Maximum clock value. Must be greater than 0.",
            "Visible?": "Check when this faction clock is safe to show players.",
            "Public?": "Check when this obligation is public or safe to show players.",
            "Marker": "Shows whether this row is Public or Hidden.",
            "State": "Plot state: active, escalating, stalled, resolved, or abandoned.",
            "Tier": "Obligation tier: trivial, minor, major, or life.",
            "Timing": "Consequence timing: immediate, next_session, soon, or later.",
            "Auto Date": "Set automatically to today's real-world date when saved.",
        }
        return tooltips.get(header, "")

    @staticmethod
    def _cell_tooltip(header: str) -> str:
        if header == "Clock":
            return "Enter a whole number from 0 through Max."
        if header == "Max":
            return "Enter the size of the clock, usually 6."
        return ""


__all__ = ["CampaignTab"]
