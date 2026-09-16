from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.campaign_state import (
    ActivePlot,
    Obligation,
    CampaignState,
    CurrentCampaign,
    FactionClock,
    NextConsequence,
    RecentEvent,
    campaign_state_from_dict,
    campaign_state_to_dict,
    default_campaign_state,
    export_campaign_state_note,
    load_campaign_state,
    render_campaign_state_markdown,
    save_campaign_state,
)


def test_missing_campaign_state_returns_default(tmp_path: Path) -> None:
    state = load_campaign_state(tmp_path / "campaign_state.yaml")

    assert state == default_campaign_state()


def test_campaign_state_round_trips_valid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "campaign_state.yaml"
    state = CampaignState(
        current=CurrentCampaign(night="Night 3", date="April 4, 2026", haven="Bronzeville"),
        faction_clocks=[FactionClock(faction="Independents", clock=4, max_clock=6, visible_to_players=True)],
        active_plots=[ActivePlot(title="Gallery pressure", state="escalating")],
        obligations=[Obligation(creditor="Critias", debtor="Group", tier="major", public=True)],
        next_consequences=[NextConsequence(source="Independents", consequence="A warning arrives.", timing="immediate")],
    )

    save_campaign_state(path, state)
    loaded = load_campaign_state(path)

    assert loaded == state
    campaign_state_from_dict(campaign_state_to_dict(loaded))


def test_schema_version_fails_before_unknown_key_validation() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        campaign_state_from_dict({"schema_version": 3, "future_field": "from v3"})


def test_unknown_keys_fail_clearly() -> None:
    with pytest.raises(ValueError, match="campaign_state: unknown key 'mystery'"):
        campaign_state_from_dict({"schema_version": 2, "mystery": True})


def test_invalid_utf8_file_fails_clearly(tmp_path: Path) -> None:
    path = tmp_path / "campaign_state.yaml"
    path.write_bytes(b"\xff\xfe\xff")

    with pytest.raises(ValueError, match="unable to decode campaign state as UTF-8"):
        load_campaign_state(path)


def test_invalid_scalar_type_reports_section_and_field() -> None:
    with pytest.raises(ValueError, match=r"current\.night: 17 must be a string"):
        campaign_state_from_dict({"schema_version": 2, "current": {"night": 17}})


def test_invalid_enum_reports_section_row_and_field() -> None:
    with pytest.raises(ValueError, match=r"active_plots\[0\]\.state: 'paused' must be one of"):
        campaign_state_from_dict(
            {
                "schema_version": 2,
                "active_plots": [{"title": "Old thing", "state": "paused"}],
            }
        )


def test_clock_overflow_reports_section_row_and_field() -> None:
    with pytest.raises(ValueError, match=r"faction_clocks\[0\]\.clock: 7 exceeds max_clock 6"):
        campaign_state_from_dict(
            {
                "schema_version": 2,
                "faction_clocks": [{"faction": "SI", "clock": 7, "max_clock": 6}],
            }
        )


def test_campaign_state_to_dict_refuses_invalid_state() -> None:
    with pytest.raises(ValueError, match=r"faction_clocks\[0\]\.clock: 9 exceeds max_clock 6"):
        campaign_state_to_dict(CampaignState(faction_clocks=[FactionClock(clock=9, max_clock=6)]))


def test_save_refuses_invalid_state_and_leaves_file_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "campaign_state.yaml"
    path.write_text("schema_version: 1\ncurrent:\n  night: Old\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exceeds max_clock"):
        save_campaign_state(path, CampaignState(faction_clocks=[FactionClock(clock=9, max_clock=6)]))

    assert yaml.safe_load(path.read_text(encoding="utf-8"))["current"]["night"] == "Old"
    assert not (tmp_path / "campaign_state.yaml.bak").exists()


def test_save_creates_single_slot_backup(tmp_path: Path) -> None:
    path = tmp_path / "campaign_state.yaml"
    save_campaign_state(path, CampaignState(current=CurrentCampaign(night="First")))

    save_campaign_state(path, CampaignState(current=CurrentCampaign(night="Second")))

    backup = tmp_path / "campaign_state.yaml.bak"
    assert backup.exists()
    assert yaml.safe_load(backup.read_text(encoding="utf-8"))["current"]["night"] == "First"
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["current"]["night"] == "Second"


def test_campaign_state_supports_past_events_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "campaign_state.yaml"
    state = CampaignState(
        current=CurrentCampaign(date="April 4, 2026"),
        past_events=[RecentEvent(date="2026-08-12", summary="Old Atrium scene", fallout="Gossip noticed.")],
    )

    save_campaign_state(path, state)

    loaded = load_campaign_state(path)
    assert loaded.past_events == state.past_events


def test_export_campaign_state_note_requires_game_date(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="current.date: game date is required"):
        export_campaign_state_note(tmp_path, CampaignState(), real_date="2026-08-12", timestamp="20260812-101500")


def test_export_campaign_state_note_writes_all_campaign_sections(tmp_path: Path) -> None:
    state = CampaignState(
        current=CurrentCampaign(night="Night 5", date="April 4, 2026", haven="The Loft", domain="Hyde Park"),
        recent_events=[RecentEvent(date="2026-08-12", summary="Annabelle called.", fallout="Critias heard.")],
        past_events=[RecentEvent(date="2026-08-11", summary="The group met Jackson.", fallout="A obligation lingered.")],
        next_consequences=[NextConsequence(source="Jackson", consequence="Messenger arrives.", timing="next_session")],
    )

    note_path = export_campaign_state_note(
        tmp_path,
        state,
        real_date="2026-08-12",
        timestamp="20260812-101500",
    )

    text = note_path.read_text(encoding="utf-8")
    assert note_path.name == "Campaign State 20260812-101500 April 4- 2026.md"
    assert "Real Date: 2026-08-12" in text
    assert "Game Date: April 4, 2026" in text
    assert "## Recent Events" in text
    assert "Annabelle called." in text
    assert "## Past Events" in text
    assert "The group met Jackson." in text
    assert "## Next Consequences" in text


def test_save_wraps_file_system_failures(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "campaign_state.yaml"

    def fail_replace(_src, _dst) -> None:
        raise OSError("locked")

    monkeypatch.setattr("core.campaign_state.os.replace", fail_replace)

    with pytest.raises(ValueError, match="unable to save campaign state: locked"):
        save_campaign_state(path, CampaignState(current=CurrentCampaign(night="Blocked")))

    assert not path.exists()
    assert not (tmp_path / "campaign_state.yaml.tmp").exists()
