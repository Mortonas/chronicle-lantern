from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.file_discovery import find_markdown_files


ANY_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


@dataclass(frozen=True)
class RelationshipEdge:
    source: str
    target: str
    link_text: str


@dataclass(frozen=True)
class RelationshipGraph:
    directed_edges: tuple[RelationshipEdge, ...]
    adjacency: dict[str, frozenset[str]]
    paths_by_stem: dict[str, str]


def build_relationship_graph(
    vault_root: Path | str, *, relationship_headings: tuple[str, ...] = ("Relationships",)
) -> RelationshipGraph:
    root = Path(vault_root).expanduser()
    markdown_files = find_markdown_files(root, recurse=True)
    paths_by_stem = {path.stem.lower(): str(path) for path in markdown_files}

    edges: list[RelationshipEdge] = []
    adjacency: dict[str, set[str]] = {str(path): set() for path in markdown_files}

    for path in markdown_files:
        source = str(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            continue
        for link_text in extract_relationship_links(text, relationship_headings=relationship_headings):
            target = resolve_wiki_link(link_text, paths_by_stem)
            if not target or target == source:
                continue
            edges.append(RelationshipEdge(source=source, target=target, link_text=link_text))
            adjacency.setdefault(source, set()).add(target)
            adjacency.setdefault(target, set()).add(source)

    frozen_adjacency = {path: frozenset(sorted(neighbors)) for path, neighbors in adjacency.items()}
    return RelationshipGraph(
        directed_edges=tuple(edges),
        adjacency=frozen_adjacency,
        paths_by_stem=dict(paths_by_stem),
    )


def extract_relationship_links(
    text: str | None, *, relationship_headings: tuple[str, ...] = ("Relationships",)
) -> list[str]:
    if not text:
        return []

    links: list[str] = []
    in_section = False
    heading_re = re.compile(
        r"^\s*#{1,6}\s+(?:" + "|".join(re.escape(item) for item in relationship_headings) + r")\s*$",
        re.IGNORECASE,
    )
    for line in text.splitlines():
        if heading_re.match(line):
            in_section = True
            continue
        if in_section and ANY_HEADING_RE.match(line):
            break
        if not in_section:
            continue
        for raw_link in WIKI_LINK_RE.findall(line):
            target = normalize_wiki_link_target(raw_link)
            if target:
                links.append(target)
    return links


def normalize_wiki_link_target(raw_link: str) -> str:
    target = raw_link.split("|", 1)[0].split("#", 1)[0].strip()
    return target


def resolve_wiki_link(link_text: str, paths_by_stem: dict[str, str]) -> str | None:
    return paths_by_stem.get(link_text.strip().lower())


__all__ = [
    "RelationshipEdge",
    "RelationshipGraph",
    "build_relationship_graph",
    "extract_relationship_links",
    "normalize_wiki_link_target",
    "resolve_wiki_link",
]
