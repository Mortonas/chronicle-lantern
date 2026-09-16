from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def stable_json_dumps(value: Any) -> str:
    return json.dumps(_normalize(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json_dumps(value).encode("utf-8")).hexdigest()


def file_content_hash(path: Path | str) -> str:
    p = Path(path)
    digest = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_path_key(path: Path | str, *, vault_root: Path | str | None = None) -> str:
    p = Path(path).expanduser()
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p.absolute()
    if vault_root is not None:
        try:
            root = Path(vault_root).expanduser().resolve()
            return resolved.relative_to(root).as_posix().lower()
        except (OSError, ValueError):
            pass
    return resolved.as_posix().lower()


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, set):
        return [_normalize(item) for item in sorted(value, key=lambda item: str(item))]
    if isinstance(value, Path):
        return value.as_posix()
    return value


__all__ = ["file_content_hash", "normalize_path_key", "stable_hash", "stable_json_dumps"]
