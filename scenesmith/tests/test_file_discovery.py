import os
import shutil
from pathlib import Path

import pytest

from core.file_discovery import find_markdown_files


def test_find_markdown_files_basic(tmp_path):
    root_md = tmp_path / "root.md"
    root_md.write_text("root")
    (tmp_path / "README.txt").write_text("nope")
    notes = tmp_path / "notes"
    notes.mkdir()
    nested_md = notes / "nested.MD"
    nested_md.write_text("nested")

    found_recurse = find_markdown_files(tmp_path, recurse=True)
    found_no_recurse = find_markdown_files(tmp_path, recurse=False)

    assert sorted(p.name for p in found_recurse) == sorted(["root.md", "nested.MD"])
    assert sorted(p.name for p in found_no_recurse) == ["root.md"]


def test_hidden_and_symlink_handling(tmp_path):
    visible = tmp_path / "visible.md"
    visible.write_text("ok")
    (tmp_path / ".hidden.md").write_text("hidden file")
    hidden_dir = tmp_path / ".secret"
    hidden_dir.mkdir()
    (hidden_dir / "inside.md").write_text("hidden dir file")

    link_target = tmp_path / "link_target.md"
    link_target.write_text("target")
    link = tmp_path / "link.md"
    try:
        link.symlink_to(link_target)
    except (OSError, NotImplementedError):
        link = None

    broken = tmp_path / "broken.md"
    try:
        broken.symlink_to(tmp_path / "missing.md")
    except (OSError, NotImplementedError):
        broken = None

    results = find_markdown_files(tmp_path, recurse=True)
    names = sorted(p.name for p in results)
    assert "visible.md" in names
    assert all(not name.startswith(".hidden") for name in names)
    if link:
        assert "link.md" in names
    if broken:
        assert "broken.md" not in names


@pytest.fixture(autouse=True)
def ensure_mock_adapter(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "1")
