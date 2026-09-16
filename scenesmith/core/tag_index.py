from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.file_discovery import find_markdown_files


TAG_RE = re.compile(r"(?<![\w/-])#([A-Za-z0-9][\w-]*)")


@dataclass(frozen=True)
class TagSummary:
    tag: str
    file_count: int
    files: tuple[Path, ...]


def extract_hashtags(text: str | None) -> set[str]:
    if not text:
        return set()
    return {match.group(1).lower() for match in TAG_RE.finditer(text)}


def build_tag_index(vault_root: Path | str) -> list[TagSummary]:
    root = Path(vault_root).expanduser()
    by_tag: dict[str, set[Path]] = {}

    for path in find_markdown_files(root, recurse=True):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            continue
        for tag in extract_hashtags(content):
            by_tag.setdefault(tag, set()).add(path)

    summaries: list[TagSummary] = []
    for tag, files in by_tag.items():
        sorted_files = tuple(sorted(files, key=lambda p: tuple(part.lower() for part in p.parts)))
        summaries.append(TagSummary(tag=tag, file_count=len(sorted_files), files=sorted_files))

    return sorted(summaries, key=lambda item: (-item.file_count, item.tag))


__all__ = ["TAG_RE", "TagSummary", "build_tag_index", "extract_hashtags"]
