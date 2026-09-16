from __future__ import annotations

import re
from pathlib import Path


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
OBSIDIAN_IMAGE_RE = re.compile(r"!\[\[([^\]|#]+(?:\|[^\]]*)?)\]\]", re.IGNORECASE)


def extract_first_image_embed(text: str | None) -> str | None:
    if not text:
        return None
    for match in OBSIDIAN_IMAGE_RE.finditer(text):
        target = match.group(1).split("|", 1)[0].strip()
        if Path(target).suffix.lower() in IMAGE_EXTENSIONS:
            return target
    return None


def resolve_character_art(
    note_path: Path | str,
    *,
    vault_root: Path | str | None = None,
    art_dir: Path | str | None = None,
) -> Path | None:
    note = Path(note_path)
    try:
        embed = extract_first_image_embed(note.read_text(encoding="utf-8", errors="replace"))
    except (OSError, UnicodeError):
        return None
    if not embed:
        return None

    embedded_path = Path(embed)
    candidates: list[Path] = []
    if embedded_path.is_absolute():
        candidates.append(embedded_path)
    if art_dir is not None:
        candidates.append(Path(art_dir).expanduser() / embedded_path.name)
    if vault_root is not None:
        root = Path(vault_root).expanduser()
        candidates.append(root / embedded_path)
        candidates.append(root / "Assets" / "Character art" / embedded_path.name)
    candidates.append(note.parent / embedded_path)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


__all__ = ["extract_first_image_embed", "resolve_character_art"]
