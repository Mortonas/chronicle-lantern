from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


CACHE_PRODUCT = "chronicle-lantern"
CACHE_NAMESPACE = "generic-v1"
CACHE_DESCRIPTOR_VERSION = 1
CACHE_KEY_CONTRACT_VERSION = "generic-key-v1"
CACHE_MANIFEST_NAME = "cache_manifest.json"
CACHE_DIRECTORIES = (
    "indexes", "events", "npc_panels", "prep_readings", "prep_sections",
    "prep_relevance", "prep_npc_conversation", "prep_rumor_guidance",
)
CACHE_FILES = ("identities.json",)


class CacheUnavailableError(RuntimeError):
    """Raised before generation when the active cache cannot be trusted."""


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class CacheDescriptor:
    owners: Mapping[str, str]
    descriptor_version: int = CACHE_DESCRIPTOR_VERSION
    namespace: str = CACHE_NAMESPACE
    key_contract_version: str = CACHE_KEY_CONTRACT_VERSION

    @property
    def canonical_payload(self) -> dict[str, Any]:
        return {
            "descriptor_version": self.descriptor_version,
            "namespace": self.namespace,
            "owners": dict(sorted((str(key), str(value)) for key, value in self.owners.items())),
            "key_contract_version": self.key_contract_version,
        }

    @property
    def schema_hash(self) -> str:
        return hashlib.sha256(_canonical_json(self.canonical_payload).encode("utf-8")).hexdigest()

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "product": CACHE_PRODUCT,
            "namespace": self.namespace,
            "descriptor_version": self.descriptor_version,
            "owners": self.canonical_payload["owners"],
            "key_contract_version": self.key_contract_version,
            "schema_hash": self.schema_hash,
        }


def default_namespace_root(local_app_data: str | Path | None = None) -> Path:
    raw_root = local_app_data if local_app_data is not None else os.environ.get("LOCALAPPDATA")
    if not raw_root:
        raise CacheUnavailableError("Club cache is unavailable")
    return Path(raw_root) / "Chronicle Lantern" / "cache" / CACHE_NAMESPACE


def activate_cache_root(
    owners: Mapping[str, str], *, local_app_data: str | Path | None = None
) -> tuple[Path, CacheDescriptor]:
    descriptor = CacheDescriptor(owners)
    root = default_namespace_root(local_app_data) / descriptor.schema_hash
    manifest_path = root / CACHE_MANIFEST_NAME
    try:
        root.mkdir(parents=True, exist_ok=True)
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing != descriptor.manifest or root.name != descriptor.schema_hash:
                raise CacheUnavailableError("Club cache is unavailable")
        else:
            ClubCacheService._atomic_write_json(manifest_path, descriptor.manifest)
            if json.loads(manifest_path.read_text(encoding="utf-8")) != descriptor.manifest:
                raise CacheUnavailableError("Club cache is unavailable")
    except CacheUnavailableError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CacheUnavailableError("Club cache is unavailable") from exc
    return root, descriptor


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _recognized_legacy_json(path: Path, *, identity_registry: bool = False) -> bool:
    if not path.is_file() or path.is_symlink() or path.suffix.casefold() != ".json":
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict):
        return False
    if identity_registry:
        return (
            str(value.get("schema_version", "")).startswith("club_identity_")
            or isinstance(value.get("identities"), dict)
        )
    return any(
        key in value for key in ("schema_version", "cache_key", "event_key", "npc_id", "identity")
    )


def recognized_legacy_cache(root: str | Path) -> bool:
    legacy_root = Path(root)
    if not legacy_root.is_dir() or _is_reparse_point(legacy_root):
        return False
    identity_path = legacy_root / "identities.json"
    if _recognized_legacy_json(identity_path, identity_registry=True):
        return True
    for name in CACHE_DIRECTORIES:
        child = legacy_root / name
        if child.is_dir() and not _is_reparse_point(child):
            for entry in child.iterdir():
                if _recognized_legacy_json(entry):
                    return True
    return False


def cleanup_recognized_legacy_cache(root: str | Path) -> bool:
    """Best-effort removal of recognized JSON entries; unknown data is preserved."""
    legacy_root = Path(root)
    if not recognized_legacy_cache(legacy_root):
        return False
    changed = False
    candidates = [
        legacy_root / name for name in CACHE_FILES
        if _recognized_legacy_json(legacy_root / name, identity_registry=True)
    ]
    for directory_name in CACHE_DIRECTORIES:
        directory = legacy_root / directory_name
        if directory.is_dir() and not _is_reparse_point(directory):
            candidates.extend(entry for entry in directory.iterdir() if _recognized_legacy_json(entry))
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            candidate.unlink()
            changed = True
        except OSError:
            pass
    for directory_name in CACHE_DIRECTORIES:
        directory = legacy_root / directory_name
        if directory.is_dir() and not _is_reparse_point(directory):
            try:
                directory.rmdir()
            except OSError:
                pass
    return changed


class ClubCacheService:
    def __init__(self, root: Path | str, *, descriptor: CacheDescriptor | None = None) -> None:
        self.root = Path(root)
        self.descriptor = descriptor
        self.cache_identity = (
            f"{descriptor.namespace}:{descriptor.schema_hash}" if descriptor is not None else "isolated-debug-cache"
        )
        self._lock = threading.RLock()
        if descriptor is not None:
            self._validate_manifest()

    def _validate_manifest(self) -> None:
        try:
            manifest = json.loads((self.root / CACHE_MANIFEST_NAME).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CacheUnavailableError("Club cache is unavailable") from exc
        if self.root.name != self.descriptor.schema_hash or manifest != self.descriptor.manifest:
            raise CacheUnavailableError("Club cache is unavailable")

    def read_json(self, *parts: str, default: Any = None) -> Any:
        path = self._path(*parts)
        with self._lock:
            if self.descriptor is not None:
                self._validate_manifest()
            try:
                with path.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
            except FileNotFoundError:
                return default
            except (OSError, json.JSONDecodeError):
                print("[WARN] Club cache entry is invalid; treating it as a cache miss.")
                return default

    def write_json(self, *parts: str, data: Any) -> None:
        path = self._path(*parts)
        with self._lock:
            if self.descriptor is not None:
                self._validate_manifest()
            self._atomic_write_json(path, data)

    def update_json(self, *parts: str, default: Any, updater: Callable[[Any], Any]) -> Any:
        path = self._path(*parts)
        with self._lock:
            current = self.read_json(*parts, default=default)
            result = updater(current)
            data_to_write = result[1] if isinstance(result, tuple) and len(result) == 2 else result
            if self.descriptor is not None:
                self._validate_manifest()
            self._atomic_write_json(path, data_to_write)
            return result[0] if isinstance(result, tuple) and len(result) == 2 else result

    def _path(self, *parts: str) -> Path:
        clean_parts = [part for part in parts if part]
        if not clean_parts:
            raise ValueError("cache path requires at least one part")
        if any(Path(part).is_absolute() or part in {".", ".."} for part in clean_parts):
            raise ValueError("cache path parts must be relative names")
        return self.root.joinpath(*clean_parts)

    @staticmethod
    def _atomic_write_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(data, fh, ensure_ascii=False, sort_keys=True, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise


__all__ = [
    "CACHE_DESCRIPTOR_VERSION", "CACHE_DIRECTORIES", "CACHE_FILES",
    "CACHE_KEY_CONTRACT_VERSION", "CACHE_MANIFEST_NAME", "CACHE_NAMESPACE",
    "CACHE_PRODUCT", "CacheDescriptor", "CacheUnavailableError", "ClubCacheService",
    "activate_cache_root", "cleanup_recognized_legacy_cache", "default_namespace_root",
    "recognized_legacy_cache",
]
