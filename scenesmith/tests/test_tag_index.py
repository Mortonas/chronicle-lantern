from __future__ import annotations

from pathlib import Path

from core.tag_index import build_tag_index, extract_hashtags


def test_extract_hashtags_lowercases_and_handles_hyphens() -> None:
    assert extract_hashtags("#Scholars #newcomer #lore #newcomer") == {
        "scholars",
        "newcomer",
        "lore",
    }


def test_build_tag_index_counts_each_tag_once_per_file(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    first.write_text("#Scholars\n#scholars\n#Lore", encoding="utf-8")
    second = tmp_path / "second.md"
    second.write_text("A #newcomer and #scholars scene.", encoding="utf-8")

    summaries = {summary.tag: summary for summary in build_tag_index(tmp_path)}

    assert summaries["scholars"].file_count == 2
    assert summaries["lore"].file_count == 1
    assert summaries["newcomer"].file_count == 1
    assert tuple(path.name for path in summaries["scholars"].files) == ("first.md", "second.md")


def test_build_tag_index_uses_markdown_discovery_rules(tmp_path: Path) -> None:
    (tmp_path / "visible.md").write_text("#visible", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("#ignored", encoding="utf-8")
    hidden_dir = tmp_path / ".hidden"
    hidden_dir.mkdir()
    (hidden_dir / "hidden.md").write_text("#hidden", encoding="utf-8")

    tags = {summary.tag for summary in build_tag_index(tmp_path)}

    assert tags == {"visible"}
