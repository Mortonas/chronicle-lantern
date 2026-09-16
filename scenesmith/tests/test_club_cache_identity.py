from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.club_cache import ClubCacheService
from core.club_hashing import stable_hash
from core.club_identity import NpcIdentityRegistry


def test_stable_hash_is_deterministic_across_dict_order() -> None:
    first = stable_hash({"b": 2, "a": {"y": 1, "x": [3, 2, 1]}})
    second = stable_hash({"a": {"x": [3, 2, 1], "y": 1}, "b": 2})

    assert first == second


def test_cache_atomic_write_and_invalid_json_cache_miss(tmp_path: Path) -> None:
    cache = ClubCacheService(tmp_path)

    cache.write_json("events", "one.json", data={"ok": True})

    assert cache.read_json("events", "one.json") == {"ok": True}
    assert not list((tmp_path / "events").glob("*.tmp"))

    broken = tmp_path / "events" / "broken.json"
    broken.write_text("{", encoding="utf-8")

    assert cache.read_json("events", "broken.json", default={"miss": True}) == {"miss": True}


def test_identity_survives_rename_by_content_fingerprint(tmp_path: Path) -> None:
    cache = ClubCacheService(tmp_path / ".club-cache")
    registry = NpcIdentityRegistry(cache, vault_root=tmp_path)
    first = tmp_path / "Damien.md"
    second = tmp_path / "Damien Renamed.md"
    first.write_text("Affiliation: Dockworkers\n", encoding="utf-8")

    original = registry.get_or_create(first)
    first.rename(second)
    renamed = registry.get_or_create(second)

    assert renamed.npc_id == original.npc_id
    assert renamed.current_path == "damien renamed.md"


def test_concurrent_identity_get_or_create_returns_one_uuid(tmp_path: Path) -> None:
    cache = ClubCacheService(tmp_path / ".club-cache")
    registry = NpcIdentityRegistry(cache, vault_root=tmp_path)
    npc = tmp_path / "Damien.md"
    npc.write_text("Affiliation: Dockworkers\n", encoding="utf-8")

    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(lambda _idx: registry.get_or_create(npc).npc_id, range(20)))

    assert len(set(ids)) == 1
    stored = cache.read_json("identities.json")
    assert len(stored["identities"]) == 1
