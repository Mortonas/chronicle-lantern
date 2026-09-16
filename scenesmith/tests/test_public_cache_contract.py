from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.club_cache import (
    CACHE_MANIFEST_NAME,
    CacheDescriptor,
    CacheUnavailableError,
    ClubCacheService,
    activate_cache_root,
    cleanup_recognized_legacy_cache,
    recognized_legacy_cache,
)


def test_descriptor_hash_is_stable_across_mapping_order() -> None:
    first = CacheDescriptor({"index": "v7", "panel": "v12"})
    second = CacheDescriptor({"panel": "v12", "index": "v7"})
    assert first.schema_hash == second.schema_hash
    assert len(first.schema_hash) == 64


def test_active_directory_and_manifest_match_descriptor(tmp_path: Path) -> None:
    root, descriptor = activate_cache_root({"index": "v7"}, local_app_data=tmp_path)
    manifest = json.loads((root / CACHE_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert root.name == descriptor.schema_hash == manifest["schema_hash"]
    assert manifest["product"] == "chronicle-lantern"
    assert manifest["namespace"] == "generic-v1"
    assert manifest["descriptor_version"] == 1


def test_mismatched_manifest_fails_closed_without_deleting_sibling(tmp_path: Path) -> None:
    root, descriptor = activate_cache_root({"index": "v7"}, local_app_data=tmp_path)
    sibling = root.parent / "unrelated-schema"
    sibling.mkdir()
    (sibling / "keep.txt").write_text("keep", encoding="utf-8")
    (root / CACHE_MANIFEST_NAME).write_text("{}", encoding="utf-8")
    with pytest.raises(CacheUnavailableError, match="Club cache is unavailable"):
        ClubCacheService(root, descriptor=descriptor)
    assert (sibling / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_manifest_is_rechecked_before_each_read_and_write(tmp_path: Path) -> None:
    root, descriptor = activate_cache_root({"index": "v7"}, local_app_data=tmp_path)
    cache = ClubCacheService(root, descriptor=descriptor)
    (root / CACHE_MANIFEST_NAME).write_text("{}", encoding="utf-8")
    with pytest.raises(CacheUnavailableError, match="Club cache is unavailable"):
        cache.read_json("events", "item.json")
    with pytest.raises(CacheUnavailableError, match="Club cache is unavailable"):
        cache.write_json("events", "item.json", data={"unsafe": True})
    assert not (root / "events" / "item.json").exists()


def test_corrupt_entry_is_a_cache_miss(tmp_path: Path) -> None:
    cache = ClubCacheService(tmp_path)
    target = tmp_path / "events" / "bad.json"
    target.parent.mkdir()
    target.write_text("{", encoding="utf-8")
    assert cache.read_json("events", "bad.json", default={"miss": True}) == {"miss": True}


def test_legacy_cleanup_preserves_unknown_files_and_never_removes_root(tmp_path: Path) -> None:
    legacy = tmp_path / ".club-cache"
    (legacy / "events").mkdir(parents=True)
    (legacy / "events" / "known.json").write_text(
        json.dumps({"cache_key": "old", "schema_version": "old"}), encoding="utf-8"
    )
    (legacy / "events" / "unknown.txt").write_text("keep", encoding="utf-8")
    (legacy / "events" / "unknown.json").write_text('{"hello": "world"}', encoding="utf-8")
    (legacy / "private.bin").write_bytes(b"keep")
    assert recognized_legacy_cache(legacy)

    assert cleanup_recognized_legacy_cache(legacy)

    assert legacy.exists()
    assert not (legacy / "events" / "known.json").exists()
    assert (legacy / "events" / "unknown.txt").read_text(encoding="utf-8") == "keep"
    assert (legacy / "events" / "unknown.json").read_text(encoding="utf-8") == '{"hello": "world"}'
    assert (legacy / "private.bin").read_bytes() == b"keep"


def test_unrecognized_directory_is_untouched(tmp_path: Path) -> None:
    legacy = tmp_path / ".club-cache"
    legacy.mkdir()
    unknown = legacy / "unknown.json"
    unknown.write_text('{"hello": "world"}', encoding="utf-8")
    assert not recognized_legacy_cache(legacy)
    assert not cleanup_recognized_legacy_cache(legacy)
    assert unknown.exists()
