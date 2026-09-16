from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6 import QtCore
from PySide6.QtCore import QStringListModel
from PySide6.QtWidgets import QCompleter, QLineEdit


TOKEN_RE = re.compile(r"(^|[\s,])([#A-Za-z0-9_-]*)$")

DEFAULT_CHARACTER_TAGS = ["npc", "character"]


@dataclass
class TagAutocompleteBinding:
    edit: QLineEdit
    completer: QCompleter
    model: QStringListModel


class TagAutocompleteRegistry:
    def __init__(self, default_tags: list[str] | None = None) -> None:
        self._tags: list[str] = []
        self._default_tags: list[str] = self._normalize_tags(default_tags or DEFAULT_CHARACTER_TAGS)
        self._bindings: list[TagAutocompleteBinding] = []

    def set_tags(self, tags: list[str]) -> None:
        self._tags = self._normalize_tags(tags)
        self._sync_models()

    def set_default_tags(self, tags: list[str]) -> None:
        self._default_tags = self._normalize_tags(tags)
        self._sync_models()

    def _completion_tags(self) -> list[str]:
        result = list(self._tags)
        seen = set(result)
        for tag in sorted(self._default_tags):
            if tag not in seen:
                result.append(tag)
                seen.add(tag)
        return result

    def _sync_models(self) -> None:
        tags = self._completion_tags()
        for binding in self._bindings:
            binding.model.setStringList(tags)

    def bind(self, edit: QLineEdit) -> None:
        if any(binding.edit is edit for binding in self._bindings):
            return
        model = QStringListModel(self._completion_tags(), edit)
        completer = QCompleter(model, edit)
        completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchStartsWith)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        edit.setCompleter(completer)
        binding = TagAutocompleteBinding(edit=edit, completer=completer, model=model)
        self._bindings.append(binding)

        edit.textEdited.connect(lambda _text, b=binding: self._complete_current_token(b))
        completer.activated[str].connect(lambda completion, b=binding: self._apply_completion(b, completion))

    def _complete_current_token(self, binding: TagAutocompleteBinding) -> None:
        token = self._current_token(binding.edit)
        if token is None:
            binding.completer.popup().hide()
            return
        prefix = token.lstrip("#")
        if len(prefix) < 1:
            binding.completer.popup().hide()
            return
        binding.completer.setCompletionPrefix(prefix)
        if binding.completer.completionCount() > 0:
            binding.completer.complete()
        else:
            binding.completer.popup().hide()

    def _apply_completion(self, binding: TagAutocompleteBinding, completion: str) -> None:
        edit = binding.edit
        cursor = edit.cursorPosition()
        before = edit.text()[:cursor]
        after = edit.text()[cursor:]
        match = TOKEN_RE.search(before)
        if not match:
            edit.insert(completion)
            return
        start = match.start(2)
        replacement = before[:start] + completion
        if after and not after.startswith((" ", ",")):
            replacement += " "
        edit.setText(replacement + after)
        edit.setCursorPosition(len(replacement))

    @staticmethod
    def _current_token(edit: QLineEdit) -> str | None:
        before = edit.text()[: edit.cursorPosition()]
        match = TOKEN_RE.search(before)
        if not match:
            return None
        return match.group(2).strip()

    @staticmethod
    def _normalize_tags(tags: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            value = str(tag or "").strip().lstrip("#").lower()
            if not value or value in seen:
                continue
            result.append(value)
            seen.add(value)
        return result


__all__ = ["DEFAULT_CHARACTER_TAGS", "TagAutocompleteRegistry", "TOKEN_RE"]
