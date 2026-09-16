from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from core.club_cache import ClubCacheService
from core.club_hashing import file_content_hash, normalize_path_key
from core.club_models import NpcIdentity

IDENTITY_REGISTRY_VERSION = "club_identity_v1"
REGISTRY_FILE = "identities.json"


ALIASES_RE = re.compile(r"^\s*(?:aliases?|also known as)\s*[:\-]\s*(.+)$", re.IGNORECASE)


class NpcIdentityRegistry:
    def __init__(self, cache: ClubCacheService, *, vault_root: Path | str | None = None) -> None:
        self.cache = cache
        self.vault_root = Path(vault_root).expanduser() if vault_root else None

    def get_or_create(self, path: Path | str) -> NpcIdentity:
        p = Path(path)
        fingerprint = file_content_hash(p)
        path_key = normalize_path_key(p, vault_root=self.vault_root)
        display_name = p.stem
        aliases = tuple(_extract_aliases(p))

        def updater(registry: dict[str, Any]) -> tuple[NpcIdentity, dict[str, Any]]:
            registry = _coerce_registry(registry)
            entries = registry["identities"]
            match = _find_identity(entries, path_key=path_key, fingerprint=fingerprint, display_name=display_name, aliases=aliases)
            if match is None:
                match = {
                    "npc_id": str(uuid.uuid4()),
                    "current_path": path_key,
                    "display_name": display_name,
                    "aliases": list(aliases),
                    "content_fingerprint": fingerprint,
                }
                entries.append(match)
            else:
                match["current_path"] = path_key
                match["display_name"] = display_name or match.get("display_name", "")
                match["aliases"] = sorted(set([*(match.get("aliases") or []), *aliases]), key=str.lower)
                match["content_fingerprint"] = fingerprint
            identity = _identity_from_entry(match)
            return identity, registry

        return self.cache.update_json(REGISTRY_FILE, default=_empty_registry(), updater=updater)

    def load_all(self) -> list[NpcIdentity]:
        registry = _coerce_registry(self.cache.read_json(REGISTRY_FILE, default=_empty_registry()))
        return [_identity_from_entry(entry) for entry in registry["identities"]]


def _empty_registry() -> dict[str, Any]:
    return {"schema_version": IDENTITY_REGISTRY_VERSION, "identities": []}


def _coerce_registry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _empty_registry()
    entries = value.get("identities")
    if not isinstance(entries, list):
        entries = []
    return {"schema_version": IDENTITY_REGISTRY_VERSION, "identities": entries}


def _find_identity(
    entries: list[dict[str, Any]],
    *,
    path_key: str,
    fingerprint: str,
    display_name: str,
    aliases: tuple[str, ...],
) -> dict[str, Any] | None:
    for entry in entries:
        if entry.get("current_path") == path_key:
            return entry
    for entry in entries:
        if entry.get("content_fingerprint") == fingerprint:
            return entry

    candidates = []
    names = {display_name.lower(), *(alias.lower() for alias in aliases)}
    for entry in entries:
        entry_names = {str(entry.get("display_name") or "").lower()}
        entry_names.update(str(alias).lower() for alias in entry.get("aliases") or [])
        if names & entry_names:
            candidates.append(entry)
    if len(candidates) == 1:
        return candidates[0]
    return None


def _identity_from_entry(entry: dict[str, Any]) -> NpcIdentity:
    return NpcIdentity(
        npc_id=str(entry["npc_id"]),
        current_path=str(entry.get("current_path") or ""),
        display_name=str(entry.get("display_name") or ""),
        aliases=tuple(str(alias) for alias in entry.get("aliases") or [] if str(alias).strip()),
        content_fingerprint=str(entry.get("content_fingerprint") or ""),
    )


def _extract_aliases(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return []
    aliases: list[str] = []
    for line in text.splitlines()[:40]:
        match = ALIASES_RE.match(line)
        if not match:
            continue
        for part in re.split(r"[,;]", match.group(1)):
            alias = part.strip().strip("[]")
            if alias:
                aliases.append(alias)
    return aliases


__all__ = ["IDENTITY_REGISTRY_VERSION", "NpcIdentityRegistry"]
