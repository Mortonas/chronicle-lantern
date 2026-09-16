import os
import shutil
from pathlib import Path

import pytest

from core.markdown_rewrite import apply_image_preserving_rewrite, extract_non_image_text


def test_image_lines_preserved():
    original = "Intro\n![[img.png]]\nText\n![alt](path.jpg)\nTail\n"
    new = "Generated content"
    result = apply_image_preserving_rewrite(original, new)
    expected = "Generated content\n![[img.png]]\n![alt](path.jpg)\n"
    assert result == expected


def test_no_non_image_lines_returns_original():
    original = "![[one.png]]\n![two](other.jpg)\n"
    result = apply_image_preserving_rewrite(original, "New text")
    assert result == original


def test_trailing_newlines_normalized():
    original = "Line\n"
    result = apply_image_preserving_rewrite(original, "New")
    assert result == "New\n"
    original_no_newline = "Line"
    result_no_newline = apply_image_preserving_rewrite(original_no_newline, "New")
    assert result_no_newline == "New"


def test_extract_non_image_text_strips_images_and_blank_edges():
    original = "\nIntro\n![[img.png]]\nBody\n![alt](img.jpg)\nTail\n\n"
    assert extract_non_image_text(original) == "Intro\nBody\nTail"


@pytest.fixture(autouse=True)
def ensure_mock_adapter(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "1")
