from __future__ import annotations

import json
import os
import subprocess
from tempfile import TemporaryDirectory
from pathlib import Path

from core.club_prep import capture_document, fallback_scene
from tools.debug_club_generation import _dashboard_text, _generation_source, _generation_summary, _redacted_preview


def test_debug_club_generation_tool_readable_output_matches_review_shape(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Political maneuvering that could expose Annabelle.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")

    result = _run_tool(
        "--guest",
        str(damien),
        "--guest",
        str(annabelle),
        "--seed",
        "7",
        "--npc",
        "Damien",
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert "The Lantern Room" in result.stdout
    assert "Political maneuvering" in result.stdout
    assert "- dashboard: source=deterministic_fallback" in result.stdout
    assert "- panel: source=deterministic_fallback" in result.stdout
    assert "[AI unavailable" not in result.stdout
    assert "ai_error_type=ai_output_validation_failed" in result.stdout
    assert "validation error:" not in result.stdout
    assert "fallback:" not in result.stdout
    assert "Other Grounded Hooks" not in result.stdout
    assert "[DEBUG]" not in result.stdout

    _assert_in_order(
        result.stdout,
        [
            "Start the Scene",
            "Who Matters Here",
            "Social Groups and Loners",
            "Useful to Know",
            "Rumors in Circulation",
                "Generation",
                "NPC Panel",
                "Right Now",
                "Play Them",
                "Agenda Tonight",
                "Who Matters Here",
                "If Approached",
                "Keep Guarded",
                "Pressure / Hook",
        ],
    )
    assert "first_impression" not in result.stdout
    assert "top_connections" not in result.stdout
    assert "possible_drama" not in result.stdout


def test_debug_club_generation_tool_include_debug_sends_captured_diagnostics_to_stderr(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Distrust): Political maneuvering.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")

    result = _run_tool(
        "--guest",
        str(damien),
        "--guest",
        str(annabelle),
        "--include-debug",
    )

    assert result.returncode == 0
    assert "The Lantern Room" in result.stdout
    assert "[DEBUG]" not in result.stdout
    assert "[DEBUG]" in result.stderr
    assert len(result.stderr) <= 1201


def test_debug_club_generation_tool_json_without_include_debug_has_visible_shape_only(tmp_path: Path) -> None:
    son = tmp_path / "Son.md"
    alexa = tmp_path / "Alexa Santos.md"
    son.write_text(
        "### Relationships\n"
        "- [[Alexa Santos]] (Toy): Young Oracle he manipulates and may commit grave misconduct against.\n",
        encoding="utf-8",
    )
    alexa.write_text("Affiliation: Oracle\n", encoding="utf-8")
    guest_file = tmp_path / "guests.txt"
    guest_file.write_text(f"{son}\n{alexa}\n", encoding="utf-8")

    result = _run_tool(
        "--guest-file",
        str(guest_file),
        "--npc",
        "Son",
        "--json",
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert "[DEBUG]" not in result.stdout
    assert payload["generation"]["dashboard"]["source"] == "deterministic_fallback"
    assert payload["generation"]["panel"]["source"] == "deterministic_fallback"
    assert payload["generation"]["dashboard"]["ai_attempted"] is True
    assert payload["generation"]["dashboard"]["fallback_used"] is True
    assert payload["generation"]["dashboard"]["ai_error_type"] == "ai_output_validation_failed"
    assert payload["generation"]["dashboard"]["ai_error_stage"] == "ai_output_validation"
    assert "[AI unavailable" not in result.stdout
    assert "debug" not in payload
    assert "dashboard_skeleton" not in payload
    assert "panel_skeleton" not in payload
    assert "source_map" not in payload
    assert "source_map" not in json.dumps(payload)
    dashboard = payload["event"]["dashboard"]
    assert dashboard["scene_prep"]["encounters"]
    assert dashboard["scene_prep"]["opening"]
    assert "evidence" not in json.dumps(dashboard)
    assert "first_impression" not in dashboard
    assert "top_connections" not in dashboard
    assert "possible_drama" not in dashboard
    assert "rumors" not in dashboard
    assert payload["panel"]["npc_id"]
    assert payload["panel"]["who_matters"]
    assert payload["panel"]["conversation_openings"] == []
    assert "evidence" not in json.dumps(payload["panel"]["conversation_openings"])
    assert "conversation" not in payload["panel"]
    assert "current_desire" not in payload["panel"]
    assert "tonight" not in payload["panel"]
    assert "interesting_detail" not in payload["panel"]
    assert "skeleton" not in payload["panel"]


def test_debug_club_generation_tool_json_include_debug_namespaces_internals(tmp_path: Path) -> None:
    son = tmp_path / "Son.md"
    alexa = tmp_path / "Alexa Santos.md"
    son.write_text(
        "### Relationships\n"
        "- [[Alexa Santos]] (Toy): Young Oracle he manipulates and may commit grave misconduct against.\n",
        encoding="utf-8",
    )
    alexa.write_text("Affiliation: Oracle\n", encoding="utf-8")

    result = _run_tool(
        "--guest",
        str(son),
        "--guest",
        str(alexa),
        "--npc",
        "Son",
        "--json",
        "--include-debug",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "[DEBUG]" not in result.stdout
    assert "[DEBUG]" in result.stderr
    assert "debug" in payload
    assert "dashboard_skeleton" not in payload
    assert "panel_skeleton" not in payload
    assert "source_map" not in payload
    assert "dashboard_skeleton" not in payload["event"]["dashboard"]
    assert payload["debug"]["skeleton"]["panel"]["name"] == "Son"
    assert payload["debug"]["skeleton"]["panel"]["current_read"]
    assert payload["debug"]["skeleton"]["panel"]["source_map"]
    panel_skeleton = payload["debug"]["skeleton"]["panel"]
    assert panel_skeleton["tonight"]["current_desire"] is None
    assert panel_skeleton["people_here"][0]["type"] == "toy"
    assert panel_skeleton["people_here"][0]["sources"]
    son_id = payload["attendees"][0]["npc_id"]
    alexa_id = payload["attendees"][1]["npc_id"]
    connection = payload["debug"]["skeleton"]["dashboard"]["possible_drama"][0]
    source = connection["sources"][0]
    source_map_entry = payload["debug"]["source_map"][source["source_id"]]
    assert connection["source_npc_id"] == son_id
    assert connection["target_npc_id"] == alexa_id
    assert Path(source["path"]).name == son.name
    assert source["character_id"] == son_id
    assert source_map_entry["target_npc_id"] == alexa_id
    assert payload["debug"]["skeleton"]["dashboard"]["source_map"][source["source_id"]]["target_npc_id"] == alexa_id


def test_debug_club_generation_readable_fallback_omits_legacy_pressure_sections(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Keeps blackmail material about Annabelle's secret private.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")

    result = _run_tool("--guest", str(damien), "--guest", str(annabelle), "--seed", "7")

    assert result.returncode == 0
    assert result.stderr == ""
    assert "Social Groups and Loners" in result.stdout
    assert "Useful to Know" in result.stdout
    assert "Hot Connections" not in result.stdout
    assert "Possible Pressure" not in result.stdout
    assert "secret private" not in result.stdout
    assert "has a useful tie" not in result.stdout
    assert "has a offended tie" not in result.stdout
    assert "tied into the room's visible social map" not in result.stdout
    assert "Other Grounded Hooks" not in result.stdout


def test_readable_debug_uses_bounded_known_position_fallback() -> None:
    documents = tuple(
        capture_document(npc_id, name, f"Status: {name} position")
        for npc_id, name in (
            ("g", "Gale"), ("a", "Ada"), ("f", "Faye"), ("c", "Cy"),
            ("b", "Bea"), ("e", "Eve"), ("d", "Dee"),
        )
    )
    positions = {document.npc_id: f"{document.name} position" for document in documents}
    scene = fallback_scene(documents, "g", positions)
    attendees = [{"npc_id": document.npc_id, "name": document.name} for document in documents]

    text = _dashboard_text({"scene_prep": scene}, attendees, "g")
    section = _section_text(text, "Who Matters Here", "Social Groups and Loners")

    assert "Power assessment unavailable; showing five known-position guests." in section
    assert all(f"{name}:" in section for name in ("Ada", "Bea", "Cy", "Dee", "Eve"))
    assert "Faye:" not in section
    assert "Gale:" not in section


def test_debug_club_generation_surfaces_self_scoped_rumors_in_visible_output(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Rumors\n"
        "- **Hidden Ledger:** People whisper that Damien keeps a ledger of broken obligations.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")

    readable = _run_tool("--guest", str(damien), "--guest", str(annabelle), "--seed", "7")
    as_json = _run_tool("--guest", str(damien), "--guest", str(annabelle), "--seed", "7", "--json")

    assert readable.returncode == 0
    assert "Rumors in Circulation" in readable.stdout
    assert "Unverified rumor — Hidden Ledger: People whisper that Damien keeps a ledger of broken obligations." in readable.stdout
    payload = json.loads(as_json.stdout)
    rumors = payload["event"]["dashboard"]["rumors_in_circulation"]
    assert any("broken obligations" in item for item in rumors)
    assert "debug" not in payload
    assert "source_map" not in json.dumps(payload)


def test_debug_club_generation_readable_output_folds_first_impression_but_omits_legacy_arrays() -> None:
    dashboard = {
        "event": {"venue": "The Lantern Room", "event_type": "social gathering", "mood": "tense"},
        "first_impression": "Legacy first impression.",
        "top_connections": [{"summary": "Legacy top raw summary.", "characters": ["npc_a"], "sources": [{"source_id": "s"}]}],
        "possible_drama": [{"summary": "Legacy pressure raw summary.", "characters": ["npc_a"], "sources": [{"source_id": "s"}]}],
        "rumors": [{"summary": "Legacy rumor raw summary.", "characters": ["npc_a"], "sources": [{"source_id": "s"}]}],
        "opportunities": [{"summary": "Legacy opportunity raw summary.", "characters": ["npc_a"], "sources": [{"source_id": "s"}]}],
        "unresolved_business": [{"summary": "Legacy unresolved raw summary.", "characters": ["npc_a"], "sources": [{"source_id": "s"}]}],
    }

    text = _dashboard_text(dashboard, [{"npc_id": "npc_a", "name": "Damien"}], "npc_a")

    assert "First impression (AI presentation): Legacy first impression." in text
    assert "Legacy top raw summary." not in text
    assert "Legacy pressure raw summary." not in text
    assert "Legacy rumor raw summary." not in text
    assert "Legacy opportunity raw summary." not in text
    assert "Legacy unresolved raw summary." not in text
    assert "Other Grounded Hooks" not in text
    assert "No grounded prep surfaced for this section." in text


def test_debug_club_generation_tool_json_include_debug_keeps_stdout_parseable(tmp_path: Path) -> None:
    damien = tmp_path / "Damien.md"
    annabelle = tmp_path / "Annabelle.md"
    damien.write_text(
        "### Relationships\n"
        "- [[Annabelle]] (Blackmail): Political maneuvering that could expose Annabelle.\n",
        encoding="utf-8",
    )
    annabelle.write_text("Affiliation: Artists\n", encoding="utf-8")

    result = _run_tool(
        "--guest",
        str(damien),
        "--guest",
        str(annabelle),
        "--json",
        "--include-debug",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "[DEBUG]" not in result.stdout
    assert "[DEBUG]" in result.stderr
    assert payload["generation"]["dashboard"]["source"] == "deterministic_fallback"
    assert payload["generation"]["panel"] is None
    assert "debug" in payload
    assert "dashboard_skeleton" not in payload
    assert "panel_skeleton" not in payload
    assert "source_map" not in payload
    connection = payload["debug"]["skeleton"]["dashboard"]["possible_drama"][0]
    source_id = connection["sources"][0]["source_id"]
    assert payload["debug"]["source_map"][source_id]["target_npc_id"] == connection["target_npc_id"]
    assert payload["debug"]["source_map"][source_id]["source_id"] == source_id
    assert payload["event"]["dashboard"]["scene_prep"]["encounters"]
    assert "possible_drama" not in payload["event"]["dashboard"]


def test_debug_club_generation_generation_source_classification() -> None:
    assert _generation_source({"generation_mode": "ai", "from_cache": False}) == "live_ai"
    assert _generation_source({"generation_mode": "ai", "from_cache": True}) == "cached_ai"
    assert _generation_source({"generation_mode": "deterministic_fallback", "from_cache": False}) == "deterministic_fallback"
    assert _generation_source({"generation_mode": "deterministic", "from_cache": False}) == "deterministic"
    assert _generation_summary({"generation_mode": "ai", "from_cache": True}) == {
        "source": "cached_ai",
        "generation_mode": "ai",
        "from_cache": True,
        "ai_attempted": False,
        "fallback_used": False,
        "ai_error_type": None,
        "ai_error_stage": None,
    }


def test_debug_club_generation_summarizes_dashboard_and_panel_independently() -> None:
    dashboard = _generation_summary({"generation_mode": "ai", "from_cache": True})
    panel = _generation_summary({"generation_mode": "ai", "from_cache": False})

    assert dashboard["source"] == "cached_ai"
    assert panel["source"] == "live_ai"


def test_debug_club_generation_validation_error_preview_redacts_and_truncates() -> None:
    raw = "provider failed api_key=sk-test password: hunter2 " + ("details " * 80)

    preview = _redacted_preview(raw, limit=80)

    assert "sk-test" not in preview
    assert "hunter2" not in preview
    assert "api_key=<redacted>" in preview
    assert "password=<redacted>" in preview
    assert len(preview) <= 80
    assert preview.endswith("...")


def test_debug_club_generation_parser_has_no_normal_ai_toggle() -> None:
    result = _run_tool("--help")

    assert result.returncode == 0
    assert "--ai" not in result.stdout
    assert "--no-ai" not in result.stdout


def _assert_in_order(text: str, needles: list[str]) -> None:
    cursor = -1
    for needle in needles:
        position = text.find(needle, cursor + 1)
        assert position > cursor, f"{needle!r} did not appear after offset {cursor}"
        cursor = position


def _section_text(text: str, start: str, end: str) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    return text[start_index:end_index]


def _run_tool(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["LLM_MOCK"] = "1"
    with TemporaryDirectory(prefix="club-debug-test-") as cache_root:
        return subprocess.run(
            [
                os.sys.executable,
                "tools/debug_club_generation.py",
                "--cache-root", cache_root,
                *args,
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
