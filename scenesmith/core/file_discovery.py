from __future__ import annotations

from pathlib import Path
from typing import Iterable


def find_markdown_files(root: Path, recurse: bool) -> list[Path]:
    """Return markdown files under *root* obeying filtering rules."""
    if root is None:
        return []

    try:
        root = root.expanduser().resolve()
    except FileNotFoundError:
        # If the root path cannot be resolved, no files can be discovered.
        return []

    if not root.exists() or not root.is_dir():
        return []

    paths: list[Path] = []

    if recurse:
        _collect_recursive(root, paths)
    else:
        _collect_shallow(root, paths)

    return sorted(paths, key=_sort_key)


def _collect_shallow(directory: Path, acc: list[Path]) -> None:
    for entry in _iter_directory(directory):
        if entry.is_file() and _is_markdown(entry):
            acc.append(entry)


def _collect_recursive(directory: Path, acc: list[Path]) -> None:
    # Explicit stack to avoid recursion depth concerns and to control traversal.
    stack: list[Path] = [directory]
    while stack:
        current = stack.pop()
        for entry in _iter_directory(current):
            if entry.is_file() and _is_markdown(entry):
                acc.append(entry)
            elif entry.is_dir() and not entry.is_symlink():
                stack.append(entry)


def _iter_directory(directory: Path) -> Iterable[Path]:
    try:
        entries = list(directory.iterdir())
    except (OSError, PermissionError):
        return []
    filtered: list[Path] = []
    for entry in entries:
        name = entry.name
        if name.startswith('.'):
            continue
        if entry.is_symlink() and not entry.exists():
            # Skip broken symlinks entirely.
            continue
        filtered.append(entry)
    return filtered


def _is_markdown(path: Path) -> bool:
    return path.suffix.lower() == '.md'


def _sort_key(path: Path) -> tuple[str, ...]:
    # Case-insensitive comparison across all path components.
    return tuple(part.lower() for part in path.parts)


__all__ = ["find_markdown_files"]
