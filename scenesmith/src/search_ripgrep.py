from __future__ import annotations
import os
import re
import subprocess
from typing import List, Dict, Set

def log(msg: str) -> None:
    print(f"[DEBUG] {msg}")


def _abs_paths_from_rg_output(vault: str, lines: list[str]) -> set[str]:
    """Join rg's relative output paths to absolute paths rooted at vault."""
    return {
        os.path.normpath(os.path.join(vault, line.strip()))
        for line in lines
        if line.strip()
    }


def _maybe_ignore_args(vault: str, rgignore_path: str | None) -> list[str]:
    """Avoid surprising exclusions by only passing an ignore file that actually exists."""
    if not rgignore_path:
        return []
    path = rgignore_path if os.path.isabs(rgignore_path) else os.path.join(vault, rgignore_path)
    return ["--ignore-file", path] if os.path.exists(path) else []


def _hidden_subprocess_kwargs() -> dict:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def _run_rg_paths(rg_path: str, args: List[str], cwd: str | None = None) -> Set[str]:
    # Always add --color=never to avoid ANSI sequences
    if "--color=never" not in args:
        args = ["--color=never"] + args
    cmd = [rg_path, *args]
    log(f"ripgrep: {cmd} (cwd={cwd})")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        **_hidden_subprocess_kwargs(),
    )
    out = proc.stdout or ""
    err = proc.stderr or ""
    if proc.returncode not in (0, 1):
        log(f"ripgrep error: {err}")
        raise RuntimeError(f"ripgrep error: {err or f'code {proc.returncode}'}")
    vault = cwd or "."
    files = _abs_paths_from_rg_output(vault, out.splitlines())
    log(f"ripgrep output ({len(files)}): {sorted(files)}")
    # Fallback: if no files, retry with --hidden -uu --no-ignore
    if not files:
        fallback_args = args.copy()
        # Only add if not already present
        for flag in ["--hidden", "-uu", "--no-ignore"]:
            if flag not in fallback_args:
                fallback_args.insert(0, flag)
        if "--color=never" not in fallback_args:
            fallback_args.insert(0, "--color=never")
        log(f"ripgrep fallback: {[rg_path, *fallback_args]} (cwd={cwd})")
        proc2 = subprocess.run(
            [rg_path, *fallback_args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            **_hidden_subprocess_kwargs(),
        )
        out2 = proc2.stdout or ""
        err2 = proc2.stderr or ""
        if proc2.returncode not in (0, 1):
            log(f"ripgrep fallback error: {err2}")
            raise RuntimeError(f"ripgrep fallback error: {err2 or f'code {proc2.returncode}'}")
        files = _abs_paths_from_rg_output(vault, out2.splitlines())
        log(f"ripgrep fallback output ({len(files)}): {sorted(files)}")
    return files


def _base_args_files_only(
    md_only: bool,
    case_insensitive: bool = True,
    use_pcre2: bool = True,
    rgignore_args: list[str] | None = None,
) -> list[str]:
    """Standard flags for filename-only matches; PCRE2 enables lookarounds."""
    args: list[str] = ["-l", "--no-messages"]
    if case_insensitive:
        args.append("-i")
    if use_pcre2:
        args.append("-P")  # why: lookarounds and robust boundaries
    if rgignore_args:
        args.extend(rgignore_args)
    if md_only:
        args.extend(["-g", "*.md"])
    return args


def files_for_hashtag(
    rg_path: str,
    vault: str,
    tag: str,
    rgignore_path: str | None = ".rgignore",
) -> Set[str]:
    """
    Match '#tag' at start-of-line or after whitespace, with a word boundary.
    """
    vault = str(vault)
    tag = tag.strip().lstrip("#").lower()
    pattern = rf"(?<!\S)#{re.escape(tag)}\b"
    args = _base_args_files_only(
        md_only=True,
        case_insensitive=True,
        use_pcre2=True,
        rgignore_args=_maybe_ignore_args(vault, rgignore_path),
    )
    args.extend(["-e", pattern, "."])
    return _run_rg_paths(rg_path, args, cwd=vault)


def files_for_tag_union(
    rg_path: str,
    vault: str,
    tag: str,
    rgignore_path: str | None = ".rgignore",
) -> Set[str]:
    return files_for_hashtag(rg_path, vault, tag, rgignore_path)


def eval_group_AND(
    rg_path: str,
    vault: str,
    tags: List[str],
    rgignore_path: str | None = ".rgignore",
) -> Set[str]:
    if not tags:
        return set()
    sets = [files_for_tag_union(rg_path, vault, t.lower(), rgignore_path) for t in tags]
    acc = sets[0].copy()
    for s in sets[1:]:
        acc &= s
    return acc


def eval_expression(
    rg_path: str,
    vault: str,
    base_tags: List[str],
    groups: List[Dict],
    rgignore_path: str | None = ".rgignore",
) -> Set[str]:
    result = eval_group_AND(rg_path, vault, [t.lower() for t in base_tags], rgignore_path) if base_tags else set()
    for g in groups:
        gset = eval_group_AND(rg_path, vault, [t.lower() for t in g["tags"]], rgignore_path)
        if g.get("op", "OR").upper() == "AND":
            result = (result & gset) if result else gset
        else:
            result = result | gset
    return result


def filter_lore(
    rg_path: str,
    vault: str,
    primary: Set[str],
    rgignore_path: str | None = ".rgignore",
) -> Set[str]:
    lore_files = files_for_tag_union(rg_path, vault, "lore", rgignore_path)
    # Only return lore entries that are not already part of the primary selection.
    return lore_files - set(primary)

# Public API

__all__ = [
    "files_for_hashtag",
    "files_for_tag_union",
    "eval_group_AND",
    "eval_expression",
    "filter_lore"
]
