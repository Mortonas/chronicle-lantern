from __future__ import annotations

import re
from pathlib import Path

RE_IMG_OBSIDIAN = re.compile(r'^!\[\[.+\.(?:png|jpe?g|gif|webp|svg)(?:\|.*)?\]\]$', re.IGNORECASE)
RE_IMG_MARKDOWN = re.compile(r'^!\[[^\]]*\]\([^)]+\)$', re.IGNORECASE)


def apply_image_preserving_rewrite(original: str, new_text: str) -> str:
    if original is None:
        original = ""
    if new_text is None:
        new_text = ""

    lines = original.splitlines(keepends=False)
    image_flags = [_is_image_line(line) for line in lines]

    first_non_image_idx = next((i for i, is_image in enumerate(image_flags) if not is_image), None)
    if first_non_image_idx is None:
        # No place to inject rewritten text; preserve original newline behavior.
        return _normalize_newline(original)

    injected = False
    output_lines: list[str] = []
    trimmed_new_text = new_text.strip()

    for idx, (line, is_image) in enumerate(zip(lines, image_flags)):
        if is_image:
            output_lines.append(line)
            continue
        if not injected:
            if trimmed_new_text:
                output_lines.append(trimmed_new_text)
            injected = True
        # Skip subsequent non-image lines entirely.

    if not injected and trimmed_new_text:
        output_lines.append(trimmed_new_text)

    result = "\n".join(output_lines)

    if original.endswith("\n"):
        result = result.rstrip("\n") + "\n"

    return result


def extract_non_image_text(original: str) -> str:
    if original is None:
        return ""
    lines = original.splitlines(keepends=False)
    non_image_lines = [line for line in lines if not _is_image_line(line)]
    text = "\n".join(non_image_lines)
    return text.strip("\n")


def _is_image_line(line: str) -> bool:
    if not line:
        return False
    return bool(RE_IMG_OBSIDIAN.match(line) or RE_IMG_MARKDOWN.match(line))


def _normalize_newline(text: str) -> str:
    if text.endswith("\n"):
        return text.rstrip("\n") + "\n"
    return text


__all__ = [
    "apply_image_preserving_rewrite",
    "extract_non_image_text",
    "RE_IMG_MARKDOWN",
    "RE_IMG_OBSIDIAN",
]
