from __future__ import annotations

import os
import random
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core.npc_filter import NPC_TAG, is_template_note
from core.relationship_graph import RelationshipGraph, build_relationship_graph
from core_logic import build_expression
from search_ripgrep import eval_expression

TAG_RE = re.compile(r"#?([A-Za-z0-9][\w\-]+)")
MAX_WEB_HOPS = 3  # Tunable: maximum relationship hops for Web guest-list expansion.


@dataclass
class GuestPick:
    file_path: str
    single_tag: str
    anchor_tags: Tuple[str, ...]
    why_picked: str = ""


def _normalize_tags(raw: Iterable[str] | None) -> List[str]:
    cleaned: List[str] = []
    for tag in raw or []:
        val = (tag or "").strip()
        if not val:
            continue
        match = TAG_RE.fullmatch(val) or TAG_RE.search(val)
        if match:
            cleaned.append(match.group(1).lower())
    seen: Set[str] = set()
    result: List[str] = []
    for tag in cleaned:
        if tag not in seen:
            seen.add(tag)
            result.append(tag)
    return result


def _parse_free_text_tags(text: str | None) -> List[str]:
    if not text:
        return []
    matches = TAG_RE.findall(text)
    return _normalize_tags(matches)


def _parse_anchor_tags(raw: str | Sequence[str] | None) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        tokens = re.split(r"[\s,]+", raw.strip())
        return _normalize_tags(tokens)
    if isinstance(raw, Sequence):
        return _normalize_tags(raw)
    return []


def _coerce_extra_groups(extra_groups_ui: Optional[Sequence]) -> List[Tuple[List[str], str]]:
    if not extra_groups_ui:
        return []
    coerced: List[Tuple[List[str], str]] = []
    for entry in extra_groups_ui:
        tags: Iterable[str]
        op: str
        if isinstance(entry, dict):
            tags = entry.get("tags", []) if isinstance(entry.get("tags"), Iterable) else []
            op = str(entry.get("op", "OR"))
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            tags = entry[0] if isinstance(entry[0], Iterable) else []
            op = str(entry[1])
        else:
            continue
        if isinstance(tags, str):
            tag_iter: Iterable = [tags]
        else:
            tag_iter = tags
        norm_tags = [str(t).strip() for t in tag_iter if str(t).strip()]
        if norm_tags:
            coerced.append((norm_tags, op))
    return coerced


def _candidate_files(rg_path: str, vault: str, base_tags: List[str], groups: List[Dict]) -> Set[str]:
    return eval_expression(rg_path, vault, base_tags, groups)


def generate_guest_list_v2(
    *,
    rg_path: str,
    vault: str,
    preset_tags: List[str],
    free_text: str,
    count: int,
    anchor_tag: str | Sequence[str] | None = None,
    extra_groups_ui: Optional[Sequence] = None,
    shuffle_seed: Optional[int] = None,
    mode: str = "random",
    host_file: str | None = None,
    forced_files: Sequence[str] | None = None,
    must_include_tags: Sequence[str] | None = None,
    must_have_tags: Sequence[str] | None = None,
    prefer_tags: Sequence[str] | None = None,
    exclude_tags: Sequence[str] | None = None,
    eligible_tags: Sequence[str] | None = None,
    allow_guests: Sequence[str] | None = None,
    prefer_guests: Sequence[str] | None = None,
    exclude_guests: Sequence[str] | None = None,
) -> List[GuestPick]:
    if count is None or count < 1:
        raise ValueError("count must be >= 1")

    if shuffle_seed is not None:
        random.seed(shuffle_seed)

    user_tags = _parse_free_text_tags(free_text)
    all_single_tags = _normalize_tags((preset_tags or []) + user_tags)

    anchor_tags = _parse_anchor_tags(anchor_tag)
    required_tags = _normalize_tags([*anchor_tags, *(must_have_tags or [])])
    preferred_tags = _normalize_tags(prefer_tags)
    include_tags = _normalize_tags(must_include_tags)
    blocked_tags = _normalize_tags(exclude_tags)
    automatic_eligible_tags = _normalize_tags(eligible_tags)
    explicit_manual_entries = _valid_manual_entries(host_file, forced_files)

    _, groups = build_expression(
        True,
        [],
        _coerce_extra_groups(extra_groups_ui),
    )

    if not all_single_tags and not include_tags and not explicit_manual_entries and not prefer_guests:
        print("[WARN] GuestListV2: no guest-list tags or manual guests provided; returning empty list.")
        return []

    candidate_cache: Dict[str, Set[str]] = {}
    blocked_files_cache: Set[str] | None = None
    npc_files_cache: Set[str] | None = None
    display_eligible_files_cache: Set[str] | None = None
    automatic_eligible_files_cache: Set[str] | None = None

    def _get_candidates(tag: str) -> Set[str]:
        if tag not in candidate_cache:
            base = _with_npc_tag([*required_tags, tag])
            files = _candidate_files(rg_path, vault, base, groups)
            candidate_cache[tag] = _exclude_template_notes(files)
            _debug_list_files(f"Candidates for tag={tag} required={required_tags}", files)
        return candidate_cache[tag]

    def _get_npc_files() -> Set[str]:
        nonlocal npc_files_cache
        if npc_files_cache is None:
            npc_files_cache = _exclude_template_notes(_candidate_files(rg_path, vault, [NPC_TAG], []))
            _debug_list_files("NPC-tagged files", npc_files_cache)
        return npc_files_cache

    def _get_display_eligible_files() -> Set[str]:
        nonlocal display_eligible_files_cache
        if display_eligible_files_cache is None:
            display_eligible_files_cache = _filter_blocked(_get_npc_files())
            _debug_list_files("Display-eligible NPC files", display_eligible_files_cache)
        return display_eligible_files_cache

    def _get_automatic_eligible_files() -> Set[str]:
        nonlocal automatic_eligible_files_cache
        if automatic_eligible_files_cache is None:
            if automatic_eligible_tags:
                eligible_sets = [
                    _exclude_template_notes(
                        _candidate_files(rg_path, vault, _with_npc_tag([tag]), [])
                    )
                    for tag in automatic_eligible_tags
                ]
                automatic_eligible_files_cache = (
                    set().union(*eligible_sets) if eligible_sets else set()
                )
                automatic_eligible_files_cache.update(allowed_guest_paths)
                if required_tags:
                    required_files = _exclude_template_notes(
                        _candidate_files(rg_path, vault, _with_npc_tag(required_tags), [])
                    )
                    automatic_eligible_files_cache.intersection_update(required_files)
                automatic_eligible_files_cache = _filter_blocked(automatic_eligible_files_cache)
            else:
                # Presets without eligible_tags retain the legacy all-NPC display rule.
                automatic_eligible_files_cache = _get_display_eligible_files()
            automatic_eligible_files_cache = set(automatic_eligible_files_cache) - excluded_guest_paths
            _debug_list_files(
                f"Automatically eligible NPC files for tags={automatic_eligible_tags}",
                automatic_eligible_files_cache,
            )
        return automatic_eligible_files_cache

    def _get_blocked_files() -> Set[str]:
        nonlocal blocked_files_cache
        if blocked_files_cache is None:
            blocked_sets = [_candidate_files(rg_path, vault, [tag], []) for tag in blocked_tags]
            blocked_files_cache = set().union(*blocked_sets) if blocked_sets else set()
            _debug_list_files(f"Excluded files for tags={blocked_tags}", blocked_files_cache)
        return blocked_files_cache

    def _filter_blocked(files: Set[str]) -> Set[str]:
        if not blocked_tags:
            return set(files)
        return set(files) - _get_blocked_files()

    def _get_allowed_candidates(tag: str) -> Set[str]:
        candidates = _filter_blocked(_get_candidates(tag))
        if automatic_eligible_tags or excluded_guest_paths:
            candidates.intersection_update(_get_automatic_eligible_files())
        return candidates

    configured_guest_names = _unique_guest_names(
        [*(allow_guests or []), *(prefer_guests or []), *(exclude_guests or [])]
    )
    resolved_guest_paths = (
        _resolve_named_guest_paths(configured_guest_names, _get_npc_files())
        if configured_guest_names
        else {}
    )
    allowed_guest_paths = {
        resolved_guest_paths[name_key]
        for name in allow_guests or []
        if (name_key := _guest_name_key(name)) in resolved_guest_paths
    }
    excluded_guest_paths = {
        resolved_guest_paths[name_key]
        for name in exclude_guests or []
        if (name_key := _guest_name_key(name)) in resolved_guest_paths
    }
    named_preferred_paths = [
        resolved_guest_paths[name_key]
        for name in prefer_guests or []
        if (name_key := _guest_name_key(name)) in resolved_guest_paths
    ]
    named_preferred_paths = _unique_paths(named_preferred_paths)
    named_preferred_paths = [
        path for path in named_preferred_paths if path in _get_automatic_eligible_files()
    ]
    if not automatic_eligible_tags:
        if required_tags:
            required_files = _exclude_template_notes(
                _candidate_files(rg_path, vault, _with_npc_tag(required_tags), [])
            )
            named_preferred_paths = [path for path in named_preferred_paths if path in required_files]

    def _get_include_candidates(tag: str) -> Set[str]:
        candidates = _filter_blocked(
            _exclude_template_notes(_candidate_files(rg_path, vault, _with_npc_tag([tag]), []))
        )
        if automatic_eligible_tags or excluded_guest_paths:
            candidates.intersection_update(_get_automatic_eligible_files())
        return candidates

    include_entries = _tag_include_entries(
        include_tags=include_tags,
        get_candidates=_get_include_candidates,
        existing_paths={path for path, _why in explicit_manual_entries},
    )
    manual_entries = [*explicit_manual_entries, *include_entries]
    target_count = max(count, len(manual_entries))

    normalized_mode = (mode or "random").strip().lower()
    if normalized_mode in {"cohesive", "web"}:
        relationship_manual_entries = [
            (path, why)
            for path, why in explicit_manual_entries
            if path in _get_display_eligible_files()
        ]
        relationship_manual_entries.extend(include_entries)
        picks = _generate_relationship_aware_picks(
            vault=vault,
            tags=all_single_tags,
            anchor_tags=tuple(anchor_tags),
            count=target_count,
            mode=normalized_mode,
            get_candidates=_get_allowed_candidates,
            manual_entries=relationship_manual_entries,
            preferred_tags=preferred_tags,
            preferred_guest_paths=named_preferred_paths,
            traversal_npc_files=_get_npc_files(),
            display_eligible_files=_get_automatic_eligible_files(),
        )
        print(
            f"[SELECT] GuestListV2 mode={normalized_mode} count={target_count} anchors={anchor_tags} "
            f"tags={all_single_tags} got={len(picks)}"
        )
        return picks

    return _generate_random_picks(
        tags=all_single_tags,
        anchor_tags=tuple(anchor_tags),
        count=target_count,
        get_candidates=_get_allowed_candidates,
        manual_entries=manual_entries,
        preferred_tags=preferred_tags,
        preferred_guest_paths=named_preferred_paths,
    )


def _with_npc_tag(tags: Sequence[str]) -> list[str]:
    normalized = _normalize_tags(tags)
    if NPC_TAG in normalized:
        return normalized
    return [NPC_TAG, *normalized]


def _exclude_template_notes(files: Set[str]) -> Set[str]:
    return {path for path in files if not is_template_note(path)}


def _guest_name_key(value: object) -> str:
    return " ".join(str(value).split()).casefold()


def _unique_guest_names(names: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        display_name = " ".join(str(name).split())
        key = _guest_name_key(display_name)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(display_name)
    return result


def _resolve_named_guest_paths(names: Sequence[str], npc_files: Set[str]) -> dict[str, str]:
    paths_by_stem: dict[str, list[str]] = {}
    for path in sorted(npc_files, key=_path_sort_key):
        paths_by_stem.setdefault(_guest_name_key(Path(path).stem), []).append(path)

    resolved: dict[str, str] = {}
    for name in names:
        key = _guest_name_key(name)
        matches = paths_by_stem.get(key, [])
        if len(matches) == 1:
            resolved[key] = matches[0]
        elif not matches:
            print(f"[WARN] GuestListV2: named guest not found or ineligible as an NPC '{name}'; skipping.")
        else:
            print(f"[WARN] GuestListV2: ambiguous named guest '{name}'; skipping.")
    return resolved


def _path_identity(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _unique_paths(paths: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for path in paths:
        identity = _path_identity(path)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(path)
    return result


def _tag_include_entries(
    *,
    include_tags: Sequence[str],
    get_candidates,
    existing_paths: set[str],
) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    seen = set(existing_paths)
    for tag in include_tags:
        for path in sorted(get_candidates(tag), key=_path_sort_key):
            if path in seen:
                continue
            if not Path(path).is_file():
                print(f"[WARN] GuestListV2: must-include guest file missing; skipping: {path}")
                continue
            seen.add(path)
            entries.append((path, f"Must include: #{tag}"))
    return entries


def _valid_manual_entries(host_file: str | None, forced_files: Sequence[str] | None) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(path_text: str | None, why: str) -> None:
        path = (path_text or "").strip()
        if not path or path in seen:
            return
        if not Path(path).is_file():
            print(f"[WARN] GuestListV2: manual guest file missing; skipping: {path}")
            return
        seen.add(path)
        entries.append((path, why))

    _add(host_file, "Host")
    for forced in forced_files or []:
        _add(forced, "Selected guest")
    return entries


def _manual_picks(
    manual_entries: Sequence[tuple[str, str]],
    *,
    anchor_tags: Tuple[str, ...],
    candidate_tags: dict[str, str],
) -> list[GuestPick]:
    return [
        GuestPick(
            file_path=path,
            single_tag=candidate_tags.get(path, ""),
            anchor_tags=anchor_tags,
            why_picked=why,
        )
        for path, why in manual_entries
    ]


def _generate_random_picks(
    *,
    tags: List[str],
    anchor_tags: Tuple[str, ...],
    count: int,
    get_candidates,
    manual_entries: Sequence[tuple[str, str]] = (),
    preferred_tags: Sequence[str] = (),
    preferred_guest_paths: Sequence[str] = (),
    allowed_npc_files: set[str] | None = None,
) -> List[GuestPick]:
    candidate_tags = _candidate_tags_by_path(tags, get_candidates) if tags else {}
    for path in preferred_guest_paths:
        candidate_tags.setdefault(path, "")
    chosen: List[GuestPick] = _manual_picks(
        manual_entries,
        anchor_tags=anchor_tags,
        candidate_tags=candidate_tags,
    )
    seen_files: Set[str] = {pick.file_path for pick in chosen}

    if not tags and not preferred_guest_paths:
        return chosen

    tag_preferred_paths = _preferred_candidate_paths(
        preferred_tags,
        get_candidates,
        seen_files,
        allowed_paths=set(candidate_tags),
    )
    tag_preferred_identities = {_path_identity(path) for path in tag_preferred_paths}
    preferred_paths = _unique_paths(
        [
            *tag_preferred_paths,
            *(path for path in preferred_guest_paths if path not in seen_files),
        ]
    )
    random.shuffle(preferred_paths)
    for file_path in preferred_paths:
        if len(chosen) >= count:
            break
        chosen.append(
            GuestPick(
                file_path=file_path,
                single_tag=_first_matching_candidate_tag(file_path, tags, get_candidates),
                anchor_tags=anchor_tags,
                why_picked=(
                    "Preferred tag match"
                    if _path_identity(file_path) in tag_preferred_identities
                    else "Preferred guest"
                ),
            )
        )
        seen_files.add(file_path)

    if not tags:
        return chosen

    tag_count = len(tags)
    tag_order = tags[:]
    random.shuffle(tag_order)

    attempts_without_pick = 0
    max_attempts = max(tag_count, 1) * 5
    idx = 0
    while len(chosen) < count and attempts_without_pick < max_attempts:
        tag = tag_order[idx % tag_count]
        idx += 1
        candidates = get_candidates(tag)
        available = [path for path in candidates if path not in seen_files]
        if not available:
            attempts_without_pick += 1
            continue
        file_path = random.choice(available)
        chosen.append(
            GuestPick(
                file_path=file_path,
                single_tag=tag,
                anchor_tags=anchor_tags,
            )
        )
        seen_files.add(file_path)
        attempts_without_pick = 0

    if len(chosen) < count:
        for tag in tags:
            get_candidates(tag)
        cached_sets = [get_candidates(tag) for tag in tags]
        union: Set[str] = set().union(*cached_sets) if cached_sets else set()
        leftovers = [path for path in union if path not in seen_files]
        random.shuffle(leftovers)

        for file_path in leftovers[: count - len(chosen)]:
            chosen.append(
                GuestPick(
                    file_path=file_path,
                    single_tag=_first_matching_candidate_tag(file_path, tags, get_candidates),
                    anchor_tags=anchor_tags,
                )
            )
            seen_files.add(file_path)

    print(
        f"[SELECT] GuestListV2 count={count} anchors={list(anchor_tags)} "
        f"tags={tags} got={len(chosen)}"
    )
    return chosen


def _generate_relationship_aware_picks(
    *,
    vault: str,
    tags: List[str],
    anchor_tags: Tuple[str, ...],
    count: int,
    mode: str,
    get_candidates,
    manual_entries: Sequence[tuple[str, str]] = (),
    preferred_tags: Sequence[str] = (),
    preferred_guest_paths: Sequence[str] = (),
    traversal_npc_files: set[str] | None = None,
    display_eligible_files: set[str] | None = None,
) -> List[GuestPick]:
    graph = build_relationship_graph(vault)
    candidate_tags = _candidate_tags_by_path(tags, get_candidates) if tags else {}
    for path in preferred_guest_paths:
        candidate_tags.setdefault(path, "")
    candidate_paths = [path for path in candidate_tags if _is_display_eligible(path, display_eligible_files)]

    chosen: list[GuestPick] = _manual_picks(
        manual_entries,
        anchor_tags=anchor_tags,
        candidate_tags=candidate_tags,
    )
    seen: set[str] = {pick.file_path for pick in chosen}
    seeds: list[str] = [path for path, _why in manual_entries]

    if not candidate_paths and not seeds:
        return []

    auto_seed_target = min(_seed_count_for(count), count)
    auto_seed_pool = [path for path in candidate_paths if path not in seen]
    auto_seed_count = max(0, min(auto_seed_target - len(seeds), len(auto_seed_pool)))
    auto_seeds = random.sample(auto_seed_pool, auto_seed_count) if auto_seed_count else []

    for seed in auto_seeds:
        chosen.append(
            GuestPick(
                file_path=seed,
                single_tag=candidate_tags.get(seed, ""),
                anchor_tags=anchor_tags,
                why_picked=f"Seed: #{candidate_tags.get(seed, '')}" if candidate_tags.get(seed) else "Seed",
            )
        )
        seen.add(seed)
    seeds.extend(auto_seeds)

    if mode == "cohesive":
        expanded = _cohesive_expansion(graph, seeds, seen)
    else:
        expanded = _web_expansion(
            graph,
            seeds,
            seen,
            max_hops=MAX_WEB_HOPS,
            traversal_npc_files=traversal_npc_files,
            display_eligible_files=display_eligible_files,
        )

    for path, reason in expanded:
        if len(chosen) >= count:
            break
        if path in seen:
            continue
        if not _is_display_eligible(path, display_eligible_files):
            continue
        chosen.append(
            GuestPick(
                file_path=path,
                single_tag=candidate_tags.get(path, ""),
                anchor_tags=anchor_tags,
                why_picked=reason,
            )
        )
        seen.add(path)

    if len(chosen) < count:
        tag_fallback = _preferred_candidate_paths(
            preferred_tags,
            get_candidates,
            seen,
            allowed_paths=set(candidate_paths),
        )
        tag_fallback_identities = {_path_identity(path) for path in tag_fallback}
        named_fallback_identities = {_path_identity(path) for path in preferred_guest_paths}
        fallback = _unique_paths(
            [
                *tag_fallback,
                *(path for path in preferred_guest_paths if path not in seen),
            ]
        )
        ordinary_fallback = [path for path in candidate_paths if path not in seen and path not in fallback]
        random.shuffle(ordinary_fallback)
        fallback.extend(ordinary_fallback)
        for path in fallback[: count - len(chosen)]:
            chosen.append(
                GuestPick(
                    file_path=path,
                    single_tag=candidate_tags.get(path, ""),
                    anchor_tags=anchor_tags,
                    why_picked=(
                        "Preferred guest"
                        if (
                            _path_identity(path) in named_fallback_identities
                            and _path_identity(path) not in tag_fallback_identities
                        )
                        else "Fallback tag match"
                    ),
                )
            )
            seen.add(path)

    return chosen


def _candidate_tags_by_path(tags: List[str], get_candidates) -> dict[str, str]:
    by_path: dict[str, str] = {}
    for tag in tags:
        for path in sorted(get_candidates(tag), key=_path_sort_key):
            by_path.setdefault(path, tag)
    return by_path


def _preferred_candidate_paths(
    preferred_tags: Sequence[str],
    get_candidates,
    seen_files: set[str],
    *,
    allowed_paths: set[str],
) -> list[str]:
    paths: list[str] = []
    seen = set(seen_files)
    for tag in preferred_tags:
        for path in sorted(get_candidates(tag), key=_path_sort_key):
            if path in seen or path not in allowed_paths:
                continue
            seen.add(path)
            paths.append(path)
    return paths


def _is_display_eligible(path: str, display_eligible_files: set[str] | None) -> bool:
    return display_eligible_files is None or path in display_eligible_files


def _first_matching_candidate_tag(path: str, tags: Sequence[str], get_candidates) -> str:
    for tag in tags:
        if path in get_candidates(tag):
            return tag
    return ""


def _seed_count_for(count: int) -> int:
    if count <= 3:
        return 1
    if count <= 7:
        return 2
    return 3


def _cohesive_expansion(graph: RelationshipGraph, seeds: list[str], seen: set[str]) -> list[tuple[str, str]]:
    seed_set = set(seeds)
    candidates: set[str] = set()
    for seed in seeds:
        candidates.update(graph.adjacency.get(seed, frozenset()))
    candidates -= seen
    ranked = sorted(
        candidates,
        # Ties are deterministic: alphabetical by file stem, then full path.
        key=lambda path: (-len(set(graph.adjacency.get(path, frozenset())) & seed_set), *_path_sort_key(path)),
    )
    return [(path, "Connected cluster") for path in ranked]


def _web_expansion(
    graph: RelationshipGraph,
    seeds: list[str],
    seen: set[str],
    *,
    max_hops: int,
    traversal_npc_files: set[str] | None = None,
    display_eligible_files: set[str] | None = None,
) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    visited = set(seeds)
    visible_sources = set(seen)
    frontier = [(seed, 0) for seed in seeds]
    index = 0
    while index < len(frontier):
        current, depth = frontier[index]
        index += 1
        if depth >= max_hops:
            continue
        neighbors = sorted(graph.adjacency.get(current, frozenset()), key=_path_sort_key)
        for neighbor in neighbors:
            if neighbor in visited:
                continue
            if traversal_npc_files is not None and neighbor not in traversal_npc_files:
                continue
            visited.add(neighbor)
            next_depth = depth + 1
            frontier.append((neighbor, next_depth))
            if neighbor not in seen and _is_display_eligible(neighbor, display_eligible_files):
                verb = "to" if current in visible_sources else "via"
                results.append((neighbor, f"Linked {verb} {Path(current).stem}"))
                visible_sources.add(neighbor)
    return results


def _path_sort_key(path: str) -> tuple[str, str]:
    # Ties are deterministic: alphabetical by file stem, then full path.
    return (Path(path).stem.lower(), path.lower())


def _debug_list_files(title: str, files: Set[str], limit: int = 5) -> None:
    try:
        preview = list(files)[:limit]
    except Exception:
        preview = []
    print(f"[DEBUG] {title}: total={len(files)} sample={preview}")
