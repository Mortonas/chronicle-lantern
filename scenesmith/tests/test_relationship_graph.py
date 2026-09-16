from __future__ import annotations

from pathlib import Path

from core.relationship_graph import (
    build_relationship_graph,
    extract_relationship_links,
    normalize_wiki_link_target,
)


def test_extracts_character_relationship_links_only_from_section() -> None:
    text = """Intro [[Ignored]]
### Relationships
- **[[Nathaniel Bordruff]] (Adversary):** Respected foe.
- **[[Bobby Weatherbottom|Bobby]] (Ally):** Trusted informant.
### Notes
[[Also Ignored]]
"""

    assert extract_relationship_links(text) == ["Nathaniel Bordruff", "Bobby Weatherbottom"]


def test_normalize_wiki_link_target_strips_alias_and_section() -> None:
    assert normalize_wiki_link_target("Maldavis|The Voice") == "Maldavis"
    assert normalize_wiki_link_target("Maldavis#Relationships") == "Maldavis"


def test_build_relationship_graph_preserves_direction_and_derives_adjacency(tmp_path: Path) -> None:
    a = tmp_path / "Alicia.md"
    b = tmp_path / "Bobby Weatherbottom.md"
    c = tmp_path / "Maldavis.md"
    a.write_text(
        """### Relationships
- **[[Bobby Weatherbottom]] (Ally):** Trusted.
- **[[Missing Person]] (Enemy):** Gone.
""",
        encoding="utf-8",
    )
    b.write_text(
        """### Relationships
- **[[Maldavis|Maldavis]] (Enemy):** Dangerous.
""",
        encoding="utf-8",
    )
    c.write_text("No relationships.", encoding="utf-8")

    graph = build_relationship_graph(tmp_path)

    assert [(edge.source, edge.target) for edge in graph.directed_edges] == [
        (str(a), str(b)),
        (str(b), str(c)),
    ]
    assert graph.adjacency[str(a)] == frozenset([str(b)])
    assert graph.adjacency[str(b)] == frozenset([str(a), str(c)])
    assert graph.adjacency[str(c)] == frozenset([str(b)])
