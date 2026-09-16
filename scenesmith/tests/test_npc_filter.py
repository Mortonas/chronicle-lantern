from __future__ import annotations

from pathlib import Path

from core.npc_filter import is_npc_note, is_template_note


def test_template_notes_are_detected_by_name_and_folder(tmp_path: Path) -> None:
    assert is_template_note(tmp_path / "character template.md")
    assert is_template_note(tmp_path / "Templates" / "Mortal.md")
    assert is_template_note(tmp_path / "V5 Gemini NPC generator.md")


def test_npc_note_requires_npc_tag_and_not_template(tmp_path: Path) -> None:
    npc = tmp_path / "Maldavis.md"
    template = tmp_path / "character template.md"

    assert is_npc_note(npc, {"npc", "independent"})
    assert not is_npc_note(npc, {"independent"})
    assert not is_npc_note(template, {"npc", "campaign"})
