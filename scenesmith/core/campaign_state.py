from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = 2
DEFAULT_CAMPAIGN_NOTE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.cwd())) / "Chronicle Lantern" / "campaign-notes"

ACTIVE_PLOT_STATES = ("active", "escalating", "stalled", "resolved", "abandoned")
OBLIGATION_TIERS = ("trivial", "minor", "major", "life")
CONSEQUENCE_TIMINGS = ("immediate", "next_session", "soon", "later")
TIMING_SORT_ORDER = {value: index for index, value in enumerate(CONSEQUENCE_TIMINGS)}


@dataclass
class CurrentCampaign:
    night: str = ""
    date: str = ""
    haven: str = ""
    domain: str = ""
    status_notes: str = ""


@dataclass
class Crisis:
    title: str = ""
    pressure: str = ""
    clock: int = 0
    max_clock: int = 6
    next_consequence: str = ""


@dataclass
class FactionClock:
    faction: str = ""
    goal: str = ""
    clock: int = 0
    max_clock: int = 6
    visible_to_players: bool = False
    omen: str = ""


@dataclass
class ActivePlot:
    title: str = ""
    state: str = "active"
    npc: str = ""
    faction: str = ""
    next_step: str = ""


@dataclass
class Obligation:
    creditor: str = ""
    debtor: str = ""
    tier: str = "minor"
    reason: str = ""
    public: bool = False
    current_use: str = ""


@dataclass
class RecentEvent:
    date: str = ""
    summary: str = ""
    fallout: str = ""


@dataclass
class NextConsequence:
    source: str = ""
    consequence: str = ""
    timing: str = "next_session"


@dataclass
class CampaignState:
    schema_version: int = SCHEMA_VERSION
    current: CurrentCampaign = field(default_factory=CurrentCampaign)
    crises: list[Crisis] = field(default_factory=list)
    faction_clocks: list[FactionClock] = field(default_factory=list)
    active_plots: list[ActivePlot] = field(default_factory=list)
    obligations: list[Obligation] = field(default_factory=list)
    recent_events: list[RecentEvent] = field(default_factory=list)
    past_events: list[RecentEvent] = field(default_factory=list)
    next_consequences: list[NextConsequence] = field(default_factory=list)


def default_campaign_state() -> CampaignState:
    return CampaignState()


def load_campaign_state(path: Path | str) -> CampaignState:
    path_obj = Path(path)
    if not path_obj.exists():
        return default_campaign_state()
    try:
        with path_obj.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path_obj}: invalid YAML: {exc}") from exc
    except UnicodeError as exc:
        raise ValueError(f"{path_obj}: unable to decode campaign state as UTF-8: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"{path_obj}: unable to read campaign state: {exc}") from exc
    if data is None:
        return default_campaign_state()
    return campaign_state_from_dict(data)


def save_campaign_state(path: Path | str, state: CampaignState) -> None:
    data = campaign_state_to_dict(state)
    campaign_state_from_dict(data)

    path_obj = Path(path)
    temp_path = path_obj.with_name(path_obj.name + ".tmp")
    try:
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        if path_obj.exists():
            shutil.copy2(path_obj, path_obj.with_name(path_obj.name + ".bak"))
        with temp_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
        os.replace(temp_path, path_obj)
    except OSError as exc:
        raise ValueError(f"{path_obj}: unable to save campaign state: {exc}") from exc
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def export_campaign_state_note(
    note_dir: Path | str,
    state: CampaignState,
    *,
    real_date: str | None = None,
    timestamp: str | None = None,
) -> Path:
    if not state.current.date.strip():
        raise ValueError("current.date: game date is required")
    note_dir_obj = Path(note_dir)
    real_date_text = real_date or datetime.now().strftime("%Y-%m-%d")
    timestamp_text = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_game_date = _safe_filename_part(state.current.date)
    note_path = note_dir_obj / f"Campaign State {timestamp_text} {safe_game_date}.md"
    body = render_campaign_state_markdown(state, real_date=real_date_text)
    try:
        note_dir_obj.mkdir(parents=True, exist_ok=True)
        note_path.write_text(body, encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{note_path}: unable to write campaign state note: {exc}") from exc
    return note_path


def render_campaign_state_markdown(state: CampaignState, *, real_date: str) -> str:
    data = campaign_state_to_dict(state)
    lines = [
        f"# Campaign State - {state.current.date}",
        "",
        f"- Real Date: {real_date}",
        f"- Game Date: {state.current.date}",
        f"- Night: {state.current.night or '-'}",
        f"- Haven: {state.current.haven or '-'}",
        f"- Domain: {state.current.domain or '-'}",
        "",
        "## Status Notes",
        state.current.status_notes or "-",
        "",
    ]
    lines.extend(_markdown_clock_section("## Crises", data["crises"], title_key="title"))
    lines.extend(_markdown_clock_section("## Faction Clocks", data["faction_clocks"], title_key="faction"))
    lines.extend(_markdown_plot_section(data["active_plots"]))
    lines.extend(_markdown_obligation_section(data["obligations"]))
    lines.extend(_markdown_event_section("## Recent Events", data["recent_events"]))
    lines.extend(_markdown_event_section("## Past Events", data["past_events"]))
    lines.extend(_markdown_consequence_section(data["next_consequences"]))
    return "\n".join(lines).rstrip() + "\n"


def campaign_state_from_dict(data: dict) -> CampaignState:
    if not isinstance(data, dict):
        raise ValueError("campaign_state: document must be a mapping")
    version = _required_int(data, "schema_version", "campaign_state")
    if version != SCHEMA_VERSION:
        raise ValueError(f"schema_version: {version!r} is unsupported; expected {SCHEMA_VERSION}")
    _reject_unknown_keys("campaign_state", data, _top_level_keys())

    current_raw = data.get("current", {})
    if current_raw is None:
        current_raw = {}
    if not isinstance(current_raw, dict):
        raise ValueError(f"current: {current_raw!r} must be a mapping")
    current = CurrentCampaign(
        night=_optional_str(current_raw, "night", "current"),
        date=_optional_str(current_raw, "date", "current"),
        haven=_optional_str(current_raw, "haven", "current"),
        domain=_optional_str(current_raw, "domain", "current"),
        status_notes=_optional_str(current_raw, "status_notes", "current"),
    )
    _reject_unknown_keys("current", current_raw, {"night", "date", "haven", "domain", "status_notes"})

    state = CampaignState(
        schema_version=version,
        current=current,
        crises=[
            _crisis_from_dict(item, index)
            for index, item in enumerate(_optional_list(data, "crises", "campaign_state"))
        ],
        faction_clocks=[
            _faction_clock_from_dict(item, index)
            for index, item in enumerate(_optional_list(data, "faction_clocks", "campaign_state"))
        ],
        active_plots=[
            _active_plot_from_dict(item, index)
            for index, item in enumerate(_optional_list(data, "active_plots", "campaign_state"))
        ],
        obligations=[
            _obligation_from_dict(item, index)
            for index, item in enumerate(_optional_list(data, "obligations", "campaign_state"))
        ],
        recent_events=[
            _recent_event_from_dict(item, index)
            for index, item in enumerate(_optional_list(data, "recent_events", "campaign_state"))
        ],
        past_events=[
            _past_event_from_dict(item, index)
            for index, item in enumerate(_optional_list(data, "past_events", "campaign_state"))
        ],
        next_consequences=[
            _next_consequence_from_dict(item, index)
            for index, item in enumerate(_optional_list(data, "next_consequences", "campaign_state"))
        ],
    )
    return state


def campaign_state_to_dict(state: CampaignState) -> dict:
    data = campaign_state_to_dict_without_validation(state)
    data["next_consequences"] = sorted(
        data["next_consequences"],
        key=lambda item: TIMING_SORT_ORDER.get(item["timing"], len(TIMING_SORT_ORDER)),
    )
    campaign_state_from_dict(data)
    return data


def validate_campaign_state(state: CampaignState) -> None:
    if not isinstance(state, CampaignState):
        raise ValueError(f"campaign_state: {state!r} must be a CampaignState")
    campaign_state_from_dict(campaign_state_to_dict_without_validation(state))


def campaign_state_to_dict_without_validation(state: CampaignState) -> dict:
    return {
        "schema_version": state.schema_version,
        "current": {
            "night": state.current.night,
            "date": state.current.date,
            "haven": state.current.haven,
            "domain": state.current.domain,
            "status_notes": state.current.status_notes,
        },
        "crises": [
            {
                "title": item.title,
                "pressure": item.pressure,
                "clock": item.clock,
                "max_clock": item.max_clock,
                "next_consequence": item.next_consequence,
            }
            for item in state.crises
        ],
        "faction_clocks": [
            {
                "faction": item.faction,
                "goal": item.goal,
                "clock": item.clock,
                "max_clock": item.max_clock,
                "visible_to_players": item.visible_to_players,
                "omen": item.omen,
            }
            for item in state.faction_clocks
        ],
        "active_plots": [
            {
                "title": item.title,
                "state": item.state,
                "npc": item.npc,
                "faction": item.faction,
                "next_step": item.next_step,
            }
            for item in state.active_plots
        ],
        "obligations": [
            {
                "creditor": item.creditor,
                "debtor": item.debtor,
                "tier": item.tier,
                "reason": item.reason,
                "public": item.public,
                "current_use": item.current_use,
            }
            for item in state.obligations
        ],
        "recent_events": [
            {
                "date": item.date,
                "summary": item.summary,
                "fallout": item.fallout,
            }
            for item in state.recent_events
        ],
        "past_events": [
            {
                "date": item.date,
                "summary": item.summary,
                "fallout": item.fallout,
            }
            for item in state.past_events
        ],
        "next_consequences": [
            {
                "source": item.source,
                "consequence": item.consequence,
                "timing": item.timing,
            }
            for item in state.next_consequences
        ],
    }


def _crisis_from_dict(item: Any, index: int) -> Crisis:
    section = f"crises[{index}]"
    data = _require_item_mapping(item, section)
    _reject_unknown_keys(section, data, {"title", "pressure", "clock", "max_clock", "next_consequence"})
    crisis = Crisis(
        title=_optional_str(data, "title", section),
        pressure=_optional_str(data, "pressure", section),
        clock=_optional_int(data, "clock", section, default=0),
        max_clock=_optional_int(data, "max_clock", section, default=6),
        next_consequence=_optional_str(data, "next_consequence", section),
    )
    _validate_clock(crisis.clock, crisis.max_clock, section)
    return crisis


def _faction_clock_from_dict(item: Any, index: int) -> FactionClock:
    section = f"faction_clocks[{index}]"
    data = _require_item_mapping(item, section)
    _reject_unknown_keys(section, data, {"faction", "goal", "clock", "max_clock", "visible_to_players", "omen"})
    clock = FactionClock(
        faction=_optional_str(data, "faction", section),
        goal=_optional_str(data, "goal", section),
        clock=_optional_int(data, "clock", section, default=0),
        max_clock=_optional_int(data, "max_clock", section, default=6),
        visible_to_players=_optional_bool(data, "visible_to_players", section, default=False),
        omen=_optional_str(data, "omen", section),
    )
    _validate_clock(clock.clock, clock.max_clock, section)
    return clock


def _active_plot_from_dict(item: Any, index: int) -> ActivePlot:
    section = f"active_plots[{index}]"
    data = _require_item_mapping(item, section)
    _reject_unknown_keys(section, data, {"title", "state", "npc", "faction", "next_step"})
    return ActivePlot(
        title=_optional_str(data, "title", section),
        state=_optional_enum(data, "state", section, ACTIVE_PLOT_STATES, default="active"),
        npc=_optional_str(data, "npc", section),
        faction=_optional_str(data, "faction", section),
        next_step=_optional_str(data, "next_step", section),
    )


def _obligation_from_dict(item: Any, index: int) -> Obligation:
    section = f"obligations[{index}]"
    data = _require_item_mapping(item, section)
    _reject_unknown_keys(section, data, {"creditor", "debtor", "tier", "reason", "public", "current_use"})
    return Obligation(
        creditor=_optional_str(data, "creditor", section),
        debtor=_optional_str(data, "debtor", section),
        tier=_optional_enum(data, "tier", section, OBLIGATION_TIERS, default="minor"),
        reason=_optional_str(data, "reason", section),
        public=_optional_bool(data, "public", section, default=False),
        current_use=_optional_str(data, "current_use", section),
    )


def _recent_event_from_dict(item: Any, index: int) -> RecentEvent:
    section = f"recent_events[{index}]"
    data = _require_item_mapping(item, section)
    _reject_unknown_keys(section, data, {"date", "summary", "fallout"})
    return RecentEvent(
        date=_optional_str(data, "date", section),
        summary=_optional_str(data, "summary", section),
        fallout=_optional_str(data, "fallout", section),
    )


def _past_event_from_dict(item: Any, index: int) -> RecentEvent:
    section = f"past_events[{index}]"
    data = _require_item_mapping(item, section)
    _reject_unknown_keys(section, data, {"date", "summary", "fallout"})
    return RecentEvent(
        date=_optional_str(data, "date", section),
        summary=_optional_str(data, "summary", section),
        fallout=_optional_str(data, "fallout", section),
    )


def _next_consequence_from_dict(item: Any, index: int) -> NextConsequence:
    section = f"next_consequences[{index}]"
    data = _require_item_mapping(item, section)
    _reject_unknown_keys(section, data, {"source", "consequence", "timing"})
    return NextConsequence(
        source=_optional_str(data, "source", section),
        consequence=_optional_str(data, "consequence", section),
        timing=_optional_enum(data, "timing", section, CONSEQUENCE_TIMINGS, default="next_session"),
    )


def _top_level_keys() -> set[str]:
    return {
        "schema_version",
        "current",
        "crises",
        "faction_clocks",
        "active_plots",
        "obligations",
        "recent_events",
        "past_events",
        "next_consequences",
    }


def _reject_unknown_keys(section: str, data: dict, allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{section}: unknown key {unknown[0]!r}")


def _required_int(data: dict, key: str, section: str) -> int:
    if key not in data:
        raise ValueError(f"{section}.{key}: missing required integer")
    return _optional_int(data, key, section, default=0)


def _optional_list(data: dict, key: str, section: str) -> list:
    value = data.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{section}.{key}: {value!r} must be a list")
    return value


def _require_item_mapping(item: Any, section: str) -> dict:
    if not isinstance(item, dict):
        raise ValueError(f"{section}: {item!r} must be a mapping")
    return item


def _optional_str(data: dict, key: str, section: str, default: str = "") -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{section}.{key}: {value!r} must be a string")
    return value


def _optional_int(data: dict, key: str, section: str, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{section}.{key}: {value!r} must be an integer")
    return value


def _optional_bool(data: dict, key: str, section: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{section}.{key}: {value!r} must be a boolean")
    return value


def _optional_enum(data: dict, key: str, section: str, choices: tuple[str, ...], default: str) -> str:
    value = _optional_str(data, key, section, default=default)
    if value not in choices:
        options = ", ".join(choices)
        raise ValueError(f"{section}.{key}: {value!r} must be one of {options}")
    return value


def _validate_clock(clock: int, max_clock: int, section: str) -> None:
    if clock < 0:
        raise ValueError(f"{section}.clock: {clock!r} must be >= 0")
    if max_clock <= 0:
        raise ValueError(f"{section}.max_clock: {max_clock!r} must be > 0")
    if clock > max_clock:
        raise ValueError(f"{section}.clock: {clock!r} exceeds max_clock {max_clock!r}")


def _safe_filename_part(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in (" ", "-", "_") else "-" for ch in value.strip())
    safe = " ".join(safe.split())
    return safe or "game-date"


def _markdown_clock_section(title: str, rows: list[dict], *, title_key: str) -> list[str]:
    lines = [title]
    if not rows:
        return [*lines, "-", ""]
    for item in rows:
        name = item.get(title_key) or "-"
        clock = f"{item.get('clock', 0)}/{item.get('max_clock', 0)}"
        parts = [f"- {name}: {clock}"]
        detail = item.get("pressure") or item.get("goal")
        if detail:
            parts.append(f"  - Detail: {detail}")
        if item.get("next_consequence"):
            parts.append(f"  - Next Consequence: {item['next_consequence']}")
        if item.get("omen"):
            parts.append(f"  - Omen: {item['omen']}")
        if "visible_to_players" in item:
            parts.append(f"  - Visibility: {'Public' if item['visible_to_players'] else 'Hidden'}")
        lines.extend(parts)
    lines.append("")
    return lines


def _markdown_plot_section(rows: list[dict]) -> list[str]:
    lines = ["## Active Plots"]
    if not rows:
        return [*lines, "-", ""]
    for item in rows:
        lines.append(f"- {item.get('title') or '-'} ({item.get('state') or 'active'})")
        if item.get("npc"):
            lines.append(f"  - NPC: {item['npc']}")
        if item.get("faction"):
            lines.append(f"  - Faction: {item['faction']}")
        if item.get("next_step"):
            lines.append(f"  - Next Step: {item['next_step']}")
    lines.append("")
    return lines


def _markdown_obligation_section(rows: list[dict]) -> list[str]:
    lines = ["## Obligations"]
    if not rows:
        return [*lines, "-", ""]
    for item in rows:
        lines.append(f"- {item.get('tier') or 'minor'}: {item.get('creditor') or '-'} -> {item.get('debtor') or '-'}")
        if item.get("reason"):
            lines.append(f"  - Reason: {item['reason']}")
        if item.get("current_use"):
            lines.append(f"  - Current Use: {item['current_use']}")
        lines.append(f"  - Visibility: {'Public' if item.get('public') else 'Hidden'}")
    lines.append("")
    return lines


def _markdown_event_section(title: str, rows: list[dict]) -> list[str]:
    lines = [title]
    if not rows:
        return [*lines, "-", ""]
    for item in rows:
        lines.append(f"- {item.get('date') or '-'}: {item.get('summary') or '-'}")
        if item.get("fallout"):
            lines.append(f"  - Fallout: {item['fallout']}")
    lines.append("")
    return lines


def _markdown_consequence_section(rows: list[dict]) -> list[str]:
    lines = ["## Next Consequences"]
    if not rows:
        return [*lines, "-", ""]
    for item in rows:
        lines.append(f"- {item.get('timing') or 'next_session'}: {item.get('consequence') or '-'}")
        if item.get("source"):
            lines.append(f"  - Source: {item['source']}")
    lines.append("")
    return lines


__all__ = [
    "ACTIVE_PLOT_STATES",
    "OBLIGATION_TIERS",
    "CONSEQUENCE_TIMINGS",
    "DEFAULT_CAMPAIGN_NOTE_DIR",
    "SCHEMA_VERSION",
    "TIMING_SORT_ORDER",
    "ActivePlot",
    "Obligation",
    "CampaignState",
    "Crisis",
    "CurrentCampaign",
    "FactionClock",
    "NextConsequence",
    "RecentEvent",
    "campaign_state_from_dict",
    "campaign_state_to_dict",
    "default_campaign_state",
    "export_campaign_state_note",
    "load_campaign_state",
    "render_campaign_state_markdown",
    "save_campaign_state",
    "validate_campaign_state",
]
