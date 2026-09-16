from __future__ import annotations

from pathlib import Path

from selection_pipeline import prepare_selection
from selection_pipeline import render_prompt
from search_ripgrep import files_for_hashtag


def test_render_prompt_preserves_missing_placeholders() -> None:
    rendered = render_prompt(
        "Scene: {scene_concept}\nMood: {mood}\nTags: {tags}",
        {"scene_concept": "Ambush", "tags": ["tense", "rain"]},
    )

    assert rendered == "Scene: Ambush\nMood: {mood}\nTags: tense, rain"


def test_render_prompt_loads_existing_template_file(tmp_path: Path) -> None:
    template = tmp_path / "prompt.txt"
    template.write_text("Location: {location}\nNPCs: {npc_count}", encoding="utf-8")

    rendered = render_prompt(str(template), {"location": "The docks", "npc_count": 3})

    assert rendered == "Location: The docks\nNPCs: 3"


def test_files_for_hashtag_accepts_prefixed_tags(monkeypatch, tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_run_rg_paths(rg_path: str, args: list[str], cwd: str | None = None) -> set[str]:
        captured.append(args)
        return set()

    monkeypatch.setattr("search_ripgrep._run_rg_paths", fake_run_rg_paths)

    files_for_hashtag("rg", str(tmp_path), "#Scholars")

    pattern_index = captured[0].index("-e") + 1
    assert captured[0][pattern_index] == r"(?<!\S)#scholars\b"


def test_prepare_selection_includes_locked_npcs_first_and_raises_count(monkeypatch, tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("#scholars", encoding="utf-8")
    second.write_text("#executives", encoding="utf-8")
    monkeypatch.setattr("selection_pipeline.eval_expression", lambda **kwargs: set())

    result = prepare_selection(
        "rg",
        str(tmp_path),
        [],
        [],
        1,
        "Atrium",
        "A tense meeting",
        locked_primary_files=[str(first), str(second)],
    )

    assert result.primary_files == [str(first), str(second)]
    assert result.npc_count == 2


def test_prepare_selection_random_fill_avoids_locked_duplicates(monkeypatch, tmp_path: Path) -> None:
    locked = tmp_path / "locked.md"
    other = tmp_path / "other.md"
    locked.write_text("#scholars", encoding="utf-8")
    other.write_text("#scholars", encoding="utf-8")

    def fake_eval_expression(**kwargs):
        return {str(locked), str(other)}

    monkeypatch.setattr("selection_pipeline.eval_expression", fake_eval_expression)

    result = prepare_selection(
        "rg",
        str(tmp_path),
        ["scholars"],
        [],
        2,
        "Chantry",
        "Ritual trouble",
        locked_primary_files=[str(locked)],
    )

    assert result.primary_files == [str(locked), str(other)]


def test_prepare_selection_skips_missing_locked_files(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "missing.md"
    monkeypatch.setattr("selection_pipeline.eval_expression", lambda **kwargs: set())

    result = prepare_selection(
        "rg",
        str(tmp_path),
        [],
        [],
        1,
        "",
        "Empty room",
        locked_primary_files=[str(missing)],
    )

    assert result.primary_files == []
