
from __future__ import annotations
import os
import random
from pathlib import Path
from typing import Any, Iterable, Mapping
from app_types import SelectionResult
from search_ripgrep import eval_expression, filter_lore


def prepare_selection(
    rg_path: str,
    vault: str,
    base_tags: list[str],
    groups: list[dict],
    npc_count: int,
    location_text: str,
    scene_concept: str,
    *,
    rng: random.Random | None = None,
    locked_primary_files: list[str] | None = None,
) -> SelectionResult:
    """
    Resolve primary/lore files and return a SelectionResult.
    """
    rng = rng or random.Random()
    final_npc_count = rng.randint(1, 4) if not npc_count or npc_count < 0 else npc_count
    location = (location_text or "").strip() or "location of your choice"

    # Build candidate tag pool
    flat_group_tags: list[str] = []
    for g in groups:
        flat_group_tags.extend(g.get("tags", []))
    candidate_pool = list(
        dict.fromkeys(
            [
                *(t.lower().strip() for t in base_tags),
                *(t.lower().strip() for t in flat_group_tags),
            ]
        )
    )
    candidate_pool = [t for t in candidate_pool if t]
    print(f"[SELECT] Candidate tag pool: {candidate_pool}")

    active_tags = candidate_pool.copy()
    should_include_lore = any(t.lower() == "lore" for t in active_tags)
    sample_pool = [t for t in active_tags if t.lower() != "lore"]
    if should_include_lore and not sample_pool:
        print("[SELECT] Lore tag requested but no other tags; lore will participate in NPC sampling.")
    sampling_pool = sample_pool if sample_pool else active_tags

    valid_locked_files = _valid_locked_files(locked_primary_files)
    final_npc_count = max(final_npc_count, len(valid_locked_files))
    locked_set = set(valid_locked_files)

    N = max(1, final_npc_count)
    random_slots = max(0, N - len(valid_locked_files))
    sample_mode = "without replacement"
    if sampling_pool and random_slots:
        if len(sampling_pool) >= random_slots:
            planned_tags = rng.sample(sampling_pool, random_slots)
        else:
            planned_tags = [rng.choice(sampling_pool) for _ in range(random_slots)]
            sample_mode = "with replacement"
    else:
        planned_tags = []
    print(f"[SELECT] Sample pool (post-lore filtering): {sampling_pool}")
    print(f"[SELECT] Sample mode: {sample_mode}")
    print(f"[SELECT] Planned tags: {planned_tags}")

    # Per-NPC, per-tag search
    tag_results: dict[str, list[str]] = {}
    npc_tag_map: dict[int, str] = {}
    npc_primary_files: list[str] = list(valid_locked_files)
    chosen_tags: list[str] = []

    def search_for_tag(tag: str) -> list[str]:
        if tag not in tag_results:
            tag_results[tag] = sorted(
                eval_expression(
                    rg_path=rg_path,
                    vault=str(vault),
                    base_tags=[tag],
                    groups=[],
                    rgignore_path=".rgignore",
                )
            )
        return tag_results[tag]

    candidate_tags = list(planned_tags)
    if random_slots and sampling_pool:
        candidate_tags.extend(tag for tag in sampling_pool if tag not in candidate_tags)
        attempts = 0
        max_attempts = max(len(candidate_tags), random_slots) * 2
        while len(npc_primary_files) < N and attempts < max_attempts and candidate_tags:
            tag = candidate_tags[attempts % len(candidate_tags)]
            attempts += 1
            files = search_for_tag(tag)
            available_files = [path for path in files if path not in locked_set and path not in npc_primary_files]
            if available_files:
                chosen_file = rng.choice(available_files)
                npc_primary_files.append(chosen_file)
                chosen_tags.append(tag)
                npc_tag_map[len(chosen_tags)] = tag
            else:
                print(f"[SELECT] Tag '{tag}' returned no available files.")
    print(f"[SELECT] Chosen tags: {chosen_tags}")
    print(f"[SELECT] NPC tag assignments: {npc_tag_map}")
    print(f"[SELECT] Primary file per NPC: {npc_primary_files}")

    primary_files = npc_primary_files
    lore_files: list[str] = []
    if should_include_lore:
        lore_candidates = sorted(
            filter_lore(
                rg_path=rg_path,
                vault=str(vault),
                primary=set(primary_files),
                rgignore_path=".rgignore",
            )
        )
        if lore_candidates:
            chosen_lore = rng.choice(lore_candidates)
            lore_files = [chosen_lore]
            print(f"[SELECT] Added lore file: {chosen_lore}")
        else:
            print("[SELECT] Lore tag requested but no additional lore files were found.")
    else:
        print("[SELECT] Lore tag not requested; skipping lore lookup.")

    active_tags_out = active_tags
    return SelectionResult(
        scene_concept=scene_concept,
        npc_count=final_npc_count,
        location=location,
        active_tags=active_tags_out,
        primary_files=primary_files,
        lore_files=lore_files,
        chosen_tags=chosen_tags,
        npc_tag_map=npc_tag_map,
    )


def _valid_locked_files(paths: list[str] | None) -> list[str]:
    valid: list[str] = []
    seen: set[str] = set()
    for path_str in paths or []:
        path = Path(path_str)
        norm = str(path)
        if norm in seen:
            continue
        seen.add(norm)
        if path.exists() and path.is_file():
            valid.append(norm)
        else:
            print(f"[WARN] locked NPC file missing: {norm}")
    return valid


def build_context_block(primary_files: Iterable[str], lore_files: Iterable[str]) -> str:
    """
    Build a readable context block from files with graceful error handling.
    """
    parts: list[str] = []

    def add_section(title: str, files: Iterable[str]) -> None:
        files = list(files)
        if not files:
            return
        parts.append(f"===== {title} =====")
        for path in files:
            parts.append(_render_file_entry(path))

    add_section("PRIMARY", primary_files)
    add_section("LORE", lore_files)

    if not parts:
        return "===== CONTEXT =====\n[No files discovered]\n"

    return "\n".join(parts) + "\n"


_MAX_BYTES = 300_000  # Why: avoid overwhelming prompts with giant files.

def _render_file_entry(path_str: str) -> str:
    p = Path(path_str)
    header = f">>> FILE: {path_str}"
    if not p.exists() or not p.is_file():
        print(f"[WARN] missing file: {path_str}")
        return f"{header}\n[FILE NOT FOUND]\n"
    try:
        content = _read_text_safe(p)
        return f"{header}\n{content}\n"
    except Exception as e:
        print(f"[ERROR] reading file: {path_str} -> {e}")
        return f"{header}\n[ERROR READING FILE: {e}]\n"


def _read_text_safe(p: Path) -> str:
    size = p.stat().st_size
    if size > _MAX_BYTES:
        with p.open("rb") as fh:
            chunk = fh.read(_MAX_BYTES)
        try:
            head = chunk.decode("utf-8", errors="replace")
        except Exception:
            head = chunk.decode("latin-1", errors="replace")
        return f"[TRUNCATED TO {_MAX_BYTES} BYTES]\n{head}"
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="latin-1", errors="replace")


class _SafeDict(dict):
    """Leave unknown placeholders intact instead of raising."""
    def __missing__(self, key):
        return "{" + key + "}"


def render_prompt(template_ref: str, variables: Mapping[str, Any]) -> str:
    """
    Render final prompt. Accepts a file path or a literal template string.
    Placeholders use Python's str.format syntax (e.g., {context}).
    """
    template = _load_template(template_ref)
    # Normalize common types to strings to keep templates clean.
    norm_vars = {k: _normalize(v) for k, v in variables.items()}
    try:
        return template.format_map(_SafeDict(norm_vars))
    except Exception as e:
        print(f"[ERROR] render_prompt: {e}")
        return f"{template}\n\n[RENDER NOTE] Failed to format with variables: {e}\n"

def _load_template(template_ref: str) -> str:
    """
    Load a template from a file path if it exists, else treat as a literal string.
    """
    if os.path.exists(template_ref):
        try:
            with open(template_ref, encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"[ERROR] loading template file: {template_ref} -> {e}")
            return f"[TEMPLATE LOAD ERROR: {e}]"
    return template_ref

def _normalize(v: Any) -> str:
    if isinstance(v, (str, int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return ", ".join(map(str, v))
    return str(v)

__all__ = [
    "prepare_selection",
    "build_context_block",
    "render_prompt"
]
