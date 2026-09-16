from __future__ import annotations

from PySide6.QtWidgets import QLineEdit

from ui.tag_autocomplete import DEFAULT_CHARACTER_TAGS, TagAutocompleteRegistry


def test_tag_autocomplete_updates_models(qapp) -> None:
    edit = QLineEdit()
    registry = TagAutocompleteRegistry()

    registry.bind(edit)
    registry.set_tags(["#scholars", "#newcomer"])

    completer = edit.completer()
    assert completer is not None
    assert completer.model().stringList()[:2] == ["scholars", "newcomer"]


def test_tag_autocomplete_replaces_only_current_token(qapp) -> None:
    edit = QLineEdit("#council #tre")
    edit.setCursorPosition(len(edit.text()))
    registry = TagAutocompleteRegistry()
    registry.bind(edit)
    binding = registry._bindings[0]

    registry._apply_completion(binding, "scholars")

    assert edit.text() == "#council scholars"


def test_tag_autocomplete_normalizes_bare_current_token(qapp) -> None:
    edit = QLineEdit("cam")
    edit.setCursorPosition(len(edit.text()))
    registry = TagAutocompleteRegistry()

    assert registry._current_token(edit) == "cam"


def test_tag_autocomplete_includes_generic_character_tags_by_default(qapp) -> None:
    edit = QLineEdit()
    registry = TagAutocompleteRegistry()

    registry.bind(edit)

    completions = edit.completer().model().stringList()
    assert set(completions) == set(DEFAULT_CHARACTER_TAGS) == {"npc", "character"}


def test_tag_autocomplete_matches_without_hash_prefix(qapp) -> None:
    edit = QLineEdit("tre")
    edit.setCursorPosition(len(edit.text()))
    registry = TagAutocompleteRegistry()
    registry.bind(edit)
    binding = registry._bindings[0]

    registry._complete_current_token(binding)

    assert binding.completer.completionPrefix() == "tre"
