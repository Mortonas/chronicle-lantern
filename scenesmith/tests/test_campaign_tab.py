from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from core.campaign_state import (
    Obligation,
    CampaignState,
    Crisis,
    CurrentCampaign,
    FactionClock,
    NextConsequence,
    RecentEvent,
)
from ui.campaign_tab import CampaignTab


def _fixed_now() -> datetime:
    return datetime(2026, 8, 12, 10, 15, 0)


def test_campaign_tab_populates_empty_defaults(qapp, qtbot, tmp_path: Path) -> None:
    tab = CampaignTab(tmp_path / "campaign_state.yaml")
    qtbot.addWidget(tab)

    assert tab.night_edit.text() == ""
    assert tab.faction_table.rowCount() == 0
    assert tab.status_label.text() == "No campaign state saved yet."
    assert not tab.is_dirty()


def test_campaign_tab_loads_sample_state_and_sorts_consequences(qapp, qtbot, tmp_path: Path) -> None:
    tab = CampaignTab(tmp_path / "campaign_state.yaml")
    qtbot.addWidget(tab)
    tab.set_state(
        CampaignState(
            current=CurrentCampaign(night="Night 4", domain="Hyde Park"),
            next_consequences=[
                NextConsequence(source="Later", consequence="Later pressure", timing="later"),
                NextConsequence(source="Now", consequence="Immediate pressure", timing="immediate"),
            ],
        )
    )

    assert tab.night_edit.text() == "Night 4"
    assert tab.domain_edit.text() == "Hyde Park"
    assert tab.consequences_table.item(0, 0).text() == "Now"


def test_campaign_tab_ui_coerces_numeric_bool_and_enum_values(qapp, qtbot, tmp_path: Path) -> None:
    tab = CampaignTab(tmp_path / "campaign_state.yaml")
    qtbot.addWidget(tab)
    tab.set_state(
        CampaignState(
            crises=[Crisis(title="Gossip scrutiny", clock=2, max_clock=6)],
            faction_clocks=[FactionClock(faction="Independents", clock=4, max_clock=6, visible_to_players=True)],
            obligations=[Obligation(creditor="Critias", debtor="Group", tier="major", public=True)],
        )
    )

    state = tab.campaign_state_from_ui()

    assert state.crises[0].clock == 2
    assert state.faction_clocks[0].visible_to_players is True
    assert state.obligations[0].tier == "major"
    assert state.obligations[0].public is True


def test_campaign_tab_add_remove_row_updates_dirty_state(qapp, qtbot, tmp_path: Path) -> None:
    tab = CampaignTab(tmp_path / "campaign_state.yaml")
    qtbot.addWidget(tab)

    tab._add_crisis_row(Crisis(title="New crisis"), dirty=True)
    assert tab.crises_table.rowCount() == 1
    assert tab.is_dirty()

    tab.crises_table.selectRow(0)
    tab._remove_selected_rows(tab.crises_table)

    assert tab.crises_table.rowCount() == 0
    assert tab.is_dirty()


def test_campaign_tab_add_row_buttons_can_add_multiple_rows(qapp, qtbot, tmp_path: Path) -> None:
    tab = CampaignTab(tmp_path / "campaign_state.yaml")
    qtbot.addWidget(tab)
    button_type = type(tab.save_btn)
    cases = [
        ("crises_add_btn", tab.crises_table),
        ("recent_events_add_btn", tab.events_table),
        ("next_consequences_add_btn", tab.consequences_table),
    ]

    for object_name, table in cases:
        add_btn = tab.findChild(button_type, object_name)
        qtbot.mouseClick(add_btn, Qt.LeftButton)
        qtbot.mouseClick(add_btn, Qt.LeftButton)

        assert table.rowCount() == 2
        assert table.currentRow() == 1
    assert tab.is_dirty()


def test_campaign_tab_visibility_markers_and_hide_toggle(qapp, qtbot, tmp_path: Path) -> None:
    tab = CampaignTab(tmp_path / "campaign_state.yaml")
    qtbot.addWidget(tab)
    tab.set_state(
        CampaignState(
            faction_clocks=[
                FactionClock(faction="Hidden faction", visible_to_players=False),
                FactionClock(faction="Public faction", visible_to_players=True),
            ],
            obligations=[
                Obligation(creditor="Hidden obligation", public=False),
                Obligation(creditor="Public obligation", public=True),
            ],
        )
    )

    assert tab.faction_table.item(0, 5).text() == "Hidden"
    assert tab.faction_table.item(1, 5).text() == "Public"
    assert tab.obligations_table.item(0, 5).text() == "Hidden"
    assert tab.obligations_table.item(1, 5).text() == "Public"

    tab.hide_hidden_chk.setChecked(True)

    assert tab.faction_table.isRowHidden(0)
    assert not tab.faction_table.isRowHidden(1)
    assert tab.obligations_table.isRowHidden(0)
    assert not tab.obligations_table.isRowHidden(1)


def test_campaign_tab_failed_reload_preserves_current_ui(qapp, qtbot, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "campaign_state.yaml"
    tab = CampaignTab(path)
    qtbot.addWidget(tab)
    tab.night_edit.setText("Keep this")
    tab._set_dirty(False)
    path.write_text("schema_version: [", encoding="utf-8")
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(tab, "_show_error", lambda title, message: errors.append((title, message)))

    assert not tab.load_from_disk(confirm_dirty=False)

    assert tab.night_edit.text() == "Keep this"
    assert errors


def test_campaign_tab_save_writes_valid_yaml_and_clears_dirty(qapp, qtbot, tmp_path: Path) -> None:
    path = tmp_path / "campaign_state.yaml"
    tab = CampaignTab(path, note_dir=tmp_path / "notes", now_func=_fixed_now)
    qtbot.addWidget(tab)
    tab.night_edit.setText("Night 8")
    tab.date_edit.setText("April 4, 2026")

    assert tab.save_to_disk()

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert data["current"]["night"] == "Night 8"
    assert data["current"]["date"] == "April 4, 2026"
    assert not tab.is_dirty()


def test_campaign_tab_save_requires_game_date(qapp, qtbot, tmp_path: Path, monkeypatch) -> None:
    tab = CampaignTab(tmp_path / "campaign_state.yaml", note_dir=tmp_path / "notes", now_func=_fixed_now)
    qtbot.addWidget(tab)
    errors: list[str] = []
    monkeypatch.setattr(tab, "_show_error", lambda _title, message: errors.append(message))

    assert not tab.save_to_disk()

    assert errors == ["current.date: game date is required"]
    assert not (tmp_path / "notes").exists()


def test_campaign_tab_save_exports_note_and_rolls_recent_events_to_past(qapp, qtbot, tmp_path: Path) -> None:
    path = tmp_path / "campaign_state.yaml"
    note_dir = tmp_path / "notes"
    tab = CampaignTab(path, note_dir=note_dir, now_func=_fixed_now)
    qtbot.addWidget(tab)
    tab.set_state(
        CampaignState(
            current=CurrentCampaign(night="Night 9", date="April 5, 2026"),
            recent_events=[RecentEvent(summary="The group embarrassed a Executives.", fallout="A rival smiled.")],
            past_events=[RecentEvent(date="2026-08-11", summary="They met Jackson.", fallout="A obligation lingered.")],
        )
    )

    assert tab.save_to_disk()

    notes = list(note_dir.glob("*.md"))
    assert len(notes) == 1
    note_text = notes[0].read_text(encoding="utf-8")
    assert "Real Date: 2026-08-12" in note_text
    assert "Game Date: April 5, 2026" in note_text
    assert "2026-08-12: The group embarrassed a Executives." in note_text
    assert "2026-08-11: They met Jackson." in note_text

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["recent_events"] == []
    assert saved["past_events"][-1]["date"] == "2026-08-12"
    assert saved["past_events"][-1]["summary"] == "The group embarrassed a Executives."
    assert tab.events_table.rowCount() == 0
    assert tab.past_events_table.item(tab.past_events_table.rowCount() - 1, 1).text() == (
        "The group embarrassed a Executives."
    )


def test_campaign_tab_confirm_prompts_are_action_specific(qapp, qtbot, tmp_path: Path, monkeypatch) -> None:
    tab = CampaignTab(tmp_path / "campaign_state.yaml")
    qtbot.addWidget(tab)
    messages: list[str] = []

    def fake_question(_parent, _title, message, *_args) -> QMessageBox.StandardButton:
        messages.append(message)
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", fake_question)

    assert not tab.confirm_reload_changes()
    assert not tab.confirm_leave_with_unsaved_changes()
    assert not tab.confirm_close_with_unsaved_changes()

    assert "will be replaced" in messages[0]
    assert "will stay here" in messages[1]
    assert "will be lost" in messages[2]


def test_campaign_tab_has_clock_and_enum_guidance(qapp, qtbot, tmp_path: Path) -> None:
    tab = CampaignTab(tmp_path / "campaign_state.yaml")
    qtbot.addWidget(tab)
    tab.set_state(CampaignState(crises=[Crisis()], active_plots=[]))

    assert "whole number" in tab.crises_table.horizontalHeaderItem(2).toolTip()
    assert "greater than 0" in tab.crises_table.horizontalHeaderItem(3).toolTip()
    assert "hide hidden faction clocks" in tab.hide_hidden_chk.toolTip()
