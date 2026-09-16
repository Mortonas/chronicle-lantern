from __future__ import annotations

from pathlib import Path


NPC_TAG = "npc"
TEMPLATE_NAME_MARKERS = (
    "template",
    "v5 gemini npc",
)
TEMPLATE_FOLDER_NAMES = {
    "template",
    "templates",
    "_template",
    "_templates",
}


def is_template_note(path: Path | str) -> bool:
    note_path = Path(path)
    stem = note_path.stem.lower()
    if any(marker in stem for marker in TEMPLATE_NAME_MARKERS):
        return True
    return any(part.lower() in TEMPLATE_FOLDER_NAMES for part in note_path.parts[:-1])


def is_npc_note(path: Path | str, tags: set[str]) -> bool:
    return NPC_TAG in tags and not is_template_note(path)


__all__ = ["NPC_TAG", "is_npc_note", "is_template_note"]
