from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from config_io import load_app_config
from core.club_generation import ClubGenerationService, build_dashboard_skeleton, build_npc_panel_skeleton, safe_debug_json
from core.club_prep import (
    conversation_opening_display_rows, prep_references, relevance_display_rows,
    rumor_guidance_display_rows, scene_display_sections, visible_rumor_guidance,
    visible_scene,
)

VALIDATION_ERROR_PREVIEW_CHARS = 300
CAPTURED_DIAGNOSTIC_PREVIEW_CHARS = 1200


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Generate a Club dashboard/panel from chosen NPC sheets without launching the UI.",
    )
    parser.add_argument("--guest", action="append", default=[], help="NPC sheet path. May be repeated.")
    parser.add_argument("--guest-file", type=Path, help="Text file with one NPC sheet path per line.")
    parser.add_argument("--npc", help="NPC name, filename stem, or npc_id to generate a panel for.")
    parser.add_argument("--seed", type=int, default=1, help="Deterministic event seed.")
    parser.add_argument("--venue", default="The Lantern Room")
    parser.add_argument("--event-type", default="social gathering")
    parser.add_argument("--use-cache", action="store_true", help="Allow cached event/panel output. Default bypasses caches.")
    parser.add_argument("--cache-root", type=Path, help="Explicit isolated cache directory; otherwise a temporary directory is used.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of readable text.")
    parser.add_argument(
        "--include-debug",
        action="store_true",
        help="Include structured debug JSON and emit captured diagnostics to stderr.",
    )
    args = parser.parse_args()

    captured_stdout = io.StringIO()
    with contextlib.redirect_stdout(captured_stdout):
        cfg = load_app_config(str(ROOT / "config" / "app.example.yaml"), source="example")
    vault_root = Path(str(cfg.get("vault_path") or ROOT)).expanduser()
    guest_paths = _guest_paths(args.guest, args.guest_file, vault_root=vault_root)
    if not guest_paths:
        raise SystemExit("No guest paths supplied. Use --guest or --guest-file.")

    service = ClubGenerationService(
        cfg,
        cache_root=args.cache_root or Path(tempfile.mkdtemp(prefix="chronicle-lantern-debug-cache-")),
        vault_root=vault_root,
    )
    with contextlib.redirect_stdout(captured_stdout):
        result = service.build_event_result(
            [str(path) for path in guest_paths],
            venue=args.venue,
            event_type=args.event_type,
            seed=args.seed,
            use_ai=True,
            force=not args.use_cache,
        )

        panel: dict[str, Any] | None = None
        panel_skeleton: dict[str, Any] | None = None
        if args.npc:
            npc_id = _resolve_npc(args.npc, result.attendee_summaries())
            panel = service.build_npc_panel_from_result(result, npc_id, use_ai=True, force=not args.use_cache)
            panel_skeleton = build_npc_panel_skeleton(result, npc_id)

    if args.include_debug:
        _write_captured_diagnostics(captured_stdout.getvalue())

    if args.json:
        payload: dict[str, Any] = {
            "generation": {
                "dashboard": _generation_summary(result.event.metadata),
                "panel": _generation_summary(panel.get("metadata")) if panel is not None else None,
            },
            "event": _visible_event_payload(
                result.event.to_dict(),
                {row["npc_id"]: row["name"] for row in result.attendee_summaries()},
            ),
            "attendees": result.attendee_summaries(),
            "panel": _visible_panel_payload(panel),
        }
        if args.include_debug:
            debug_payload = result.to_debug_dict()
            debug_payload["skeleton"] = {
                "dashboard": build_dashboard_skeleton(result),
                "panel": panel_skeleton,
            }
            if panel is not None:
                debug_payload["panel"] = panel
                if result.prep_context:
                    debug_payload["npc_prep_support"] = result.prep_context.debug_support(prep_references([
                        panel.get("who_matters", []), panel.get("conversation_openings", []),
                    ]))
            payload["debug"] = debug_payload
        print(safe_debug_json(payload))
        return

    print(_dashboard_text(result.event.dashboard, result.attendee_summaries(), result.event.late_arrival_id))
    print()
    print("Generation")
    _print_generation_summary("dashboard", result.event.metadata)
    if panel is not None:
        print()
        _print_generation_summary("panel", panel.get("metadata"))
        print()
        print(_panel_text(panel, result.attendee_summaries()))


def _guest_paths(raw_paths: Sequence[str], guest_file: Path | None, *, vault_root: Path) -> list[Path]:
    values = list(raw_paths)
    if guest_file is not None:
        values.extend(_read_guest_file(guest_file))
    paths: list[Path] = []
    for value in values:
        value = str(value).strip().strip('"')
        if not value or value.startswith("#"):
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = vault_root / path
        path = path.resolve()
        if not path.exists() or not path.is_file():
            raise SystemExit(f"Guest file not found: {path}")
        paths.append(path)
    return paths


def _read_guest_file(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"Guest list file not found: {path}")
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _resolve_npc(query: str, attendees: Sequence[dict[str, str]]) -> str:
    normalized = query.strip().lower()
    for attendee in attendees:
        candidates = {
            str(attendee.get("npc_id") or "").lower(),
            str(attendee.get("name") or "").lower(),
            Path(str(attendee.get("path") or "")).stem.lower(),
        }
        if normalized in candidates:
            return str(attendee["npc_id"])
    choices = ", ".join(str(attendee.get("name") or attendee.get("npc_id")) for attendee in attendees)
    raise SystemExit(f"NPC is not in this event: {query}. Attendees: {choices}")


def _visible_event_payload(event: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any]:
    dashboard = event.get("dashboard") if isinstance(event.get("dashboard"), dict) else {}
    metadata = dict(event.get("metadata")) if isinstance(event.get("metadata"), dict) else {}
    prep_stages = metadata.get("prep_stages")
    if isinstance(prep_stages, dict):
        metadata["prep_stages"] = {
            name: value for name, value in prep_stages.items() if name != "encounter_cues"
        }
    return {
        "event_id": event.get("event_id"),
        "attendee_ids": list(event.get("attendee_ids") or []),
        "late_arrival_id": event.get("late_arrival_id"),
        "dashboard": _visible_dashboard_payload(dashboard, names),
        "cache_key": event.get("cache_key"),
        "seed": event.get("seed"),
        "metadata": metadata,
    }


def _visible_dashboard_payload(dashboard: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any]:
    event = dashboard.get("event") if isinstance(dashboard.get("event"), dict) else {}
    guidance = visible_rumor_guidance(dashboard.get("rumor_guidance") or [], names or {})
    if isinstance(dashboard.get("scene_prep"), dict):
        return {"event": {k: event.get(k) for k in ("venue", "host", "event_type", "mood", "unusual_circumstance")},
                "scene_prep": visible_scene(dashboard["scene_prep"]),
                "rumors_in_circulation": _visible_strings(dashboard.get("rumors_in_circulation") or []),
                "rumor_guidance": guidance,
                "late_arrival_id": str(dashboard.get("late_arrival_id") or "")}
    return {
        "event": {
            "venue": event.get("venue"),
            "host": event.get("host"),
            "event_type": event.get("event_type"),
            "mood": event.get("mood"),
            "unusual_circumstance": event.get("unusual_circumstance"),
        },
        "room_situation": str(dashboard.get("room_situation") or ""),
        "hot_connections": _visible_strings(dashboard.get("hot_connections") or []),
        "possible_pressure": _visible_strings(dashboard.get("possible_pressure") or []),
        "rumors_in_circulation": _visible_strings(dashboard.get("rumors_in_circulation") or []),
        "rumor_guidance": guidance,
        "guest_brief": _visible_strings(dashboard.get("guest_brief") or []),
        "late_arrival_id": str(dashboard.get("late_arrival_id") or ""),
    }


def _visible_panel_payload(panel: dict[str, Any] | None) -> dict[str, Any] | None:
    if panel is None:
        return None
    identity = panel.get("identity") if isinstance(panel.get("identity"), dict) else {}
    presentation = panel.get("presentation") if isinstance(panel.get("presentation"), dict) else {}
    conversation = panel.get("conversation") if isinstance(panel.get("conversation"), dict) else {}
    conversation_openings = panel.get("conversation_openings") if isinstance(panel.get("conversation_openings"), list) else []
    result = {
        "npc_id": panel.get("npc_id"),
        "name": panel.get("name"),
        "identity": {
            "affiliation": identity.get("affiliation"),
            "faction": identity.get("faction"),
            "status": identity.get("status"),
            "roles": list(identity.get("roles") or []),
        },
        "current_read": str(panel.get("current_read") or _fallback_panel_read(panel)),
        "conversation_openings": [
            {key: row[key] for key in ("lane", "topic", "response", "possible_gain", "possible_risk")}
            for row in conversation_openings
            if isinstance(row, dict) and all(key in row for key in ("lane", "topic", "response", "possible_gain", "possible_risk"))
        ],
        "people_here": _panel_cue_strings(presentation.get("people_here"), panel.get("people_here") or []),
        "likely_conversation": _panel_cue_strings(
            presentation.get("if_approached"),
            conversation.get("likely_subjects") or [],
            fallback=panel.get("likely_conversation") or [],
        ),
        "sensitive_subjects": _panel_cue_strings(
            presentation.get("keep_guarded"),
            conversation.get("sensitive") or [],
            fallback=panel.get("sensitive_subjects") or [],
        ),
        "useful_hook": str((presentation.get("hook") or {}).get("text") or panel.get("useful_hook") or _item_text(panel.get("interesting_detail")) or ""),
        "empty_state": str(panel.get("empty_state") or ""),
        "metadata": panel.get("metadata") if isinstance(panel.get("metadata"), dict) else {},
    }
    if "who_matters" in panel:
        result.pop("people_here", None)
        result["who_matters"] = [{k: r[k] for k in ("npc_id", "text", "classification")} for r in panel["who_matters"]]
    return result


def _visible_strings(items: Sequence[Any]) -> list[str]:
    return [str(item).strip() for item in items if str(item).strip()]


def _dashboard_text(dashboard: dict[str, Any], attendees: Sequence[dict[str, str]], late_arrival_id: str) -> str:
    name_by_id = {str(item["npc_id"]): str(item["name"]) for item in attendees if item.get("npc_id")}
    event = dashboard.get("event") if isinstance(dashboard.get("event"), dict) else {}
    if isinstance(dashboard.get("scene_prep"), dict):
        lines = [str(event.get("venue") or "The Lantern Room")]
        for title, rows in scene_display_sections(dashboard["scene_prep"], name_by_id):
            lines.extend(["", title, *rows])
        lines.extend(["", "Rumors in Circulation"])
        lines.extend(_rumor_text_lines(dashboard, name_by_id))
        return "\n".join(lines)
    lines = [
        str(event.get("venue") or "The Lantern Room"),
        f"Event: {event.get('event_type', 'social gathering')} Mood: {event.get('mood', '')} Late Arrival: {name_by_id.get(late_arrival_id, late_arrival_id)}",
        "",
        "Room Situation",
    ]
    first_impression = " ".join(str(dashboard.get("first_impression") or "").split())
    if first_impression:
        lines.append(f"First impression (AI presentation): {first_impression}")
    grounded_situation = " ".join(str(dashboard.get("room_situation") or "").split()) or "No grounded prep surfaced for this section."
    lines.append(f"Grounded situation: {grounded_situation}")
    for title, prep_field, grounded_field in [
        ("Hot Connections", "hot_connections", "top_connections"),
        ("Possible Pressure", "possible_pressure", "possible_drama"),
        ("Rumors in Circulation", "rumors_in_circulation", "rumors"),
    ]:
        lines.extend(["", title])
        grounded = dashboard.get(grounded_field) or []
        if prep_field == "rumors_in_circulation":
            lines.extend(_rumor_text_lines(dashboard, name_by_id))
        else:
            lines.extend(_visible_items_text(dashboard.get(prep_field) or [], grounded_items=grounded))
    lines.extend(["", "Guests"])
    lines.extend(_strings_text(dashboard.get("guest_brief") or _guest_brief_text(attendees, late_arrival_id=late_arrival_id)))
    return "\n".join(lines)


def _rumor_text_lines(dashboard: dict[str, Any], name_by_id: dict[str, str]) -> list[str]:
    visible = _visible_strings(dashboard.get("rumors_in_circulation") or [])
    grounded = [item for item in dashboard.get("rumors") or [] if isinstance(item, dict)]
    guidance = rumor_guidance_display_rows(
        dashboard.get("rumor_guidance") or [], name_by_id, include_evidence=True,
    )
    by_rumor = {row["rumor_item_id"]: row["lines"] for row in guidance}
    result = []
    for index, text in enumerate(visible):
        result.append(f"- {text}")
        item_id = grounded[index].get("item_id") if index < len(grounded) else None
        result.extend(f"  {line}" for line in by_rumor.get(item_id, []))
    return result or ["- No grounded prep surfaced for this section."]


def _social_map_text(items: Sequence[Any], *, name_by_id: dict[str, str]) -> list[str]:
    rows: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            text = str(item).strip()
            if text:
                rows.append(f"- {text}")
            continue
        location = str(item.get("location") or item.get("area") or "Area")
        npc_ids = [str(npc_id) for npc_id in item.get("npc_ids") or item.get("characters") or []]
        names = ", ".join(name_by_id.get(npc_id, npc_id) for npc_id in npc_ids)
        summary = str(item.get("summary") or "")
        separator = " - " if names and summary else ""
        rows.append(f"- {location}: {names}{separator}{summary}".strip())
    return rows or ["- No grounded entries for this section."]


def _strings_text(items: Sequence[Any]) -> list[str]:
    rows = [f"- {str(item).strip()}" for item in items if str(item).strip()]
    return rows or ["- No grounded prep surfaced for this section."]


def _visible_items_text(items: Sequence[Any], *, grounded_items: Sequence[Any]) -> list[str]:
    visible_items = _visible_strings(items)
    if not visible_items:
        return ["- No grounded prep surfaced for this section."]
    rows: list[str] = []
    for idx, text in enumerate(visible_items):
        support = _support_for_visible_text(text, grounded_items, idx)
        why = _why_text(support) if support is not None else ""
        rows.append(f"- {text}{why}")
    return rows


def _guest_brief_text(attendees: Sequence[dict[str, str]], *, late_arrival_id: str) -> list[str]:
    rows: list[str] = []
    for attendee in attendees:
        npc_id = str(attendee.get("npc_id") or "")
        name = str(attendee.get("name") or npc_id)
        suffix = " Late arrival." if npc_id == late_arrival_id else ""
        rows.append(f"{name}.{suffix}")
    return rows


def _panel_text(panel: dict[str, Any], attendees: Sequence[dict[str, str]] = ()) -> str:
    lines = ["NPC Panel", str(panel.get("name") or panel.get("npc_id") or "")]
    conversation = panel.get("conversation") if isinstance(panel.get("conversation"), dict) else {}
    presentation = panel.get("presentation") if isinstance(panel.get("presentation"), dict) else {}
    lines.extend(["", "Right Now", f"- {panel.get('current_read') or _fallback_panel_read(panel)}"])
    tonight = panel.get("tonight") if isinstance(panel.get("tonight"), dict) else {}
    focus = tonight.get("current_desire") if isinstance(tonight.get("current_desire"), dict) else None
    agenda = presentation.get("agenda") if isinstance(presentation.get("agenda"), dict) else {}
    lines.extend(["", "Play Them"])
    lines.extend(_strings_text([presentation.get("play_cue") or tonight.get("current_demeanor")]))
    lines.extend(["", "Agenda Tonight"])
    agenda_text = str(agenda.get("text") or _item_text(focus))
    lines.extend([f"- {agenda_text}{_why_text(focus)}"] if agenda_text else ["- No grounded prep surfaced for this section."])
    lines.extend(["", "Who Matters Here"])
    if "who_matters" in panel:
        lines.extend(_strings_text(relevance_display_rows(panel["who_matters"], {a["npc_id"]: a["name"] for a in attendees})))
    else:
        lines.extend(_panel_cue_text(presentation.get("people_here"), panel.get("people_here") or []))
    metadata = panel.get("metadata") if isinstance(panel.get("metadata"), dict) else {}
    lines.extend(["", "Conversation Openings"])
    lines.extend(_strings_text(conversation_opening_display_rows(
        panel.get("conversation_openings") if isinstance(panel.get("conversation_openings"), list) else [],
        metadata.get("conversation_openings_stage") if isinstance(metadata.get("conversation_openings_stage"), dict) else None,
        include_evidence=True,
    )))
    lines.extend(["", "If Approached"])
    lines.extend(
        _panel_cue_text(
            presentation.get("if_approached"),
            conversation.get("likely_subjects") or [],
            fallback=panel.get("likely_conversation") or [],
        )
    )
    lines.extend(["", "Keep Guarded"])
    lines.extend(
        _panel_cue_text(
            presentation.get("keep_guarded"),
            conversation.get("sensitive") or [],
            fallback=panel.get("sensitive_subjects") or [],
        )
    )
    hook = presentation.get("hook") if isinstance(presentation.get("hook"), dict) else {}
    hook_text = str(hook.get("text") or panel.get("useful_hook") or _item_text(panel.get("interesting_detail")) or panel.get("empty_state") or "No grounded hook surfaced.")
    lines.extend(["", "Pressure / Hook", f"- {hook_text}{_why_text(panel.get('interesting_detail'))}"])
    return "\n".join(lines)


def _panel_cue_strings(cues: Any, supports: Sequence[Any], *, fallback: Sequence[Any] = ()) -> list[str]:
    support_ids = {
        str(item.get("item_id") or "")
        for item in supports
        if isinstance(item, dict) and str(item.get("item_id") or "")
    }
    if isinstance(cues, list):
        rows = [
            str(cue.get("text") or "").strip()
            for cue in cues
            if isinstance(cue, dict)
            and str(cue.get("item_id") or "") in support_ids
            and str(cue.get("text") or "").strip()
        ]
        if rows:
            return rows
    fallback_rows = _visible_strings(fallback)
    return fallback_rows or [_item_text(item) for item in supports if _item_text(item).strip()]


def _panel_cue_text(cues: Any, supports: Sequence[Any], *, fallback: Sequence[Any] = ()) -> list[str]:
    support_by_id = {
        str(item.get("item_id") or ""): item
        for item in supports
        if isinstance(item, dict) and str(item.get("item_id") or "")
    }
    if isinstance(cues, list):
        rows: list[str] = []
        for cue in cues:
            if not isinstance(cue, dict):
                continue
            support = support_by_id.get(str(cue.get("item_id") or ""))
            text = str(cue.get("text") or "").strip()
            if support is not None and text:
                rows.append(f"- {text}{_why_text(support)}")
        if rows:
            return rows
    fallback_rows = _visible_strings(fallback)
    if fallback_rows:
        return [f"- {text}{_why_text(supports[index]) if index < len(supports) and isinstance(supports[index], dict) else ''}" for index, text in enumerate(fallback_rows)]
    return _items_text(supports, name_by_id={}, section="panel")


def _item_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("prep_text") or item.get("summary") or "")
    return str(item or "")


def _fallback_panel_read(panel: dict[str, Any]) -> str:
    name = " ".join(str(panel.get("name") or panel.get("npc_id") or "This NPC").split()) or "This NPC"
    return f"{name} has limited attendee-specific prep in the current indexes."


def _generation_summary(metadata: Any) -> dict[str, Any]:
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    generation_mode = str(metadata_dict.get("generation_mode") or "")
    from_cache = bool(metadata_dict.get("from_cache", False))
    return {
        "source": _generation_source(metadata_dict),
        "generation_mode": generation_mode,
        "from_cache": from_cache,
        "ai_attempted": bool(metadata_dict.get("ai_attempted", False)),
        "fallback_used": bool(metadata_dict.get("fallback_used", False)),
        "ai_error_type": metadata_dict.get("ai_error_type"),
        "ai_error_stage": metadata_dict.get("ai_error_stage"),
    }


def _generation_source(metadata: Any) -> str:
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    generation_mode = str(metadata_dict.get("generation_mode") or "")
    if generation_mode == "ai":
        return "cached_ai" if bool(metadata_dict.get("from_cache", False)) else "live_ai"
    if generation_mode == "deterministic_fallback":
        return "deterministic_fallback"
    if generation_mode == "deterministic":
        return "deterministic"
    return generation_mode or "unknown"


def _print_generation_summary(label: str, metadata: Any) -> None:
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    summary = _generation_summary(metadata_dict)
    parts = [
        f"source={summary['source']}",
        f"mode={summary['generation_mode']}",
        f"model={metadata_dict.get('model_name', '')}",
        f"from_cache={summary['from_cache']}",
        f"ai_attempted={summary['ai_attempted']}",
        f"fallback_used={summary['fallback_used']}",
    ]
    if summary["ai_error_type"]:
        parts.append(f"ai_error_type={summary['ai_error_type']}")
    if summary["ai_error_stage"]:
        parts.append(f"ai_error_stage={summary['ai_error_stage']}")
    print(f"- {label}: {'; '.join(parts)}")


def _write_captured_diagnostics(text: str) -> None:
    preview = _redacted_preview(text, limit=CAPTURED_DIAGNOSTIC_PREVIEW_CHARS)
    if preview:
        print(preview, file=sys.stderr)


def _redacted_preview(text: str, *, limit: int = VALIDATION_ERROR_PREVIEW_CHARS) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    clean = re.sub(
        r"(?i)\b(api[_-]?key|authorization|bearer|password|secret|token)\b\s*[:=]\s*['\"]?[^'\"\s,;]+",
        r"\1=<redacted>",
        clean,
    )
    clean = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <redacted>", clean)
    if len(clean) > limit:
        clean = clean[: limit - 3].rstrip() + "..."
    return clean


def _items_text(items: Iterable[Any], *, name_by_id: dict[str, str], section: str) -> list[str]:
    rows: list[str] = []
    for item in items:
        if isinstance(item, dict):
            has_prep = bool(item.get("prep_text"))
            names = "" if has_prep else " ".join(name_by_id.get(str(npc_id), str(npc_id)) for npc_id in item.get("characters") or [])
            label = _display_label_for_text(item, section)
            prefix = " ".join(part for part in [names, f"{label}:" if label and not has_prep else ""] if part)
            why = _why_text(item)
            text = str(item.get("prep_text") or item.get("summary", ""))
            rows.append(f"- {' '.join(part for part in [prefix, text] if part)}{why}".strip())
        else:
            text = str(item).strip()
            if text:
                rows.append(f"- {text}")
    return rows or ["- No grounded entries for this section."]


def _display_label_for_text(item: dict[str, Any], section: str) -> str:
    label = str(item.get("display_label") or item.get("type") or "")
    if section in {"rumors", "opportunities", "unresolved_business", "background_details"} and label.lower() in {
        "topic",
        "goal",
        "rumor",
        "unresolved business",
    }:
        return ""
    return label


def _why_text(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    sources = item.get("sources") or []
    if not sources or not isinstance(sources[0], dict):
        return ""
    source_id = str(sources[0].get("source_id") or "")
    section = str(sources[0].get("section") or "")
    if not source_id:
        return ""
    return f"\n  Why? {source_id}{f' ({section})' if section else ''}"


def _support_for_visible_text(text: str, grounded_items: Sequence[Any], index: int) -> dict[str, Any] | None:
    normalized = text.strip()
    if index < len(grounded_items) and isinstance(grounded_items[index], dict):
        candidate = grounded_items[index]
        if str(candidate.get("prep_text") or "").strip() == normalized:
            return candidate
    return None


def _item_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    characters = [str(character) for character in item.get("characters") or []]
    subject_id = str(item.get("source_npc_id") or (characters[0] if characters else ""))
    object_id = str(item.get("target_npc_id") or (characters[1] if len(characters) > 1 else ""))
    sources = item.get("sources") or []
    source_id = ""
    if sources and isinstance(sources[0], dict):
        source_id = str(sources[0].get("source_id") or "")
    return (subject_id, object_id, str(item.get("type") or ""), source_id)


if __name__ == "__main__":
    main()
