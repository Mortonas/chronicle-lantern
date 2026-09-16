from __future__ import annotations

from pathlib import Path
from PySide6.QtCore import QMimeData, QUrl, Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QLineEdit,
    QSpinBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QSizePolicy,
)
from PySide6.QtGui import QBrush, QKeySequence, QPixmap, QShortcut

from app.obsidian_open import open_in_obsidian
from core.character_art import resolve_character_art
from core.file_discovery import find_markdown_files
from core.npc_filter import is_npc_note
from core.tag_index import extract_hashtags


class HoverPortraitLabel(QLabel):
    def __init__(self, parent=None, *, size: int = 150, popup_size: int = 420):
        super().__init__(parent)
        self._source_pixmap: QPixmap | None = None
        self._popup: QLabel | None = None
        self._popup_size = popup_size
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(size, size)
        self.setText("No portrait")

    def set_portrait(self, image_path: str | None) -> bool:
        self._hide_popup()
        self._source_pixmap = None
        if not image_path:
            self.clear()
            self.setText("No portrait")
            return False
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.clear()
            self.setText("No portrait")
            return False
        self._source_pixmap = pixmap
        self.setPixmap(pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        return True

    def enterEvent(self, event) -> None:  # type: ignore[override]
        if self._source_pixmap is not None:
            popup = QLabel(None, Qt.ToolTip)
            popup.setPixmap(
                self._source_pixmap.scaled(
                    self._popup_size,
                    self._popup_size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
            popup.adjustSize()
            popup.move(self.mapToGlobal(self.rect().topRight()))
            popup.show()
            self._popup = popup
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hide_popup()
        super().leaveEvent(event)

    def _hide_popup(self) -> None:
        if self._popup is not None:
            self._popup.hide()
            self._popup.deleteLater()
            self._popup = None


def copy_image_to_clipboard(image_path: str | None) -> bool:
    if not image_path:
        return False
    pixmap = QPixmap(image_path)
    if pixmap.isNull():
        return False
    mime = QMimeData()
    mime.setImageData(pixmap.toImage())
    mime.setUrls([QUrl.fromLocalFile(image_path)])
    QApplication.clipboard().setMimeData(mime)
    return True


def _normalize_display_names(display_names: dict[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for note_stem, display_name in (display_names or {}).items():
        key = " ".join(str(note_stem).split()).casefold()
        value = " ".join(str(display_name).split())
        if key and value:
            result[key] = value
    return result


def _display_name_for_path(path: str | Path, display_names: dict[str, str]) -> str:
    stem = Path(path).stem
    return display_names.get(" ".join(stem.split()).casefold(), stem)


class NpcPickerDialog(QDialog):
    """Shared modal NPC picker for host and manual guest selection."""

    def __init__(
        self,
        vault_path: str | None,
        *,
        mode: str,
        parent=None,
        art_dir: str | None = None,
        display_names: dict[str, str] | None = None,
    ):
        super().__init__(parent)
        self.mode = mode if mode in {"host", "guest"} else "guest"
        self._vault_path = vault_path
        self._art_dir = art_dir
        self._display_names = _normalize_display_names(display_names)
        self._rows: list[dict[str, str]] = []
        self._current_portrait_path: str | None = None
        self.setWindowTitle("Pick Host" if self.mode == "host" else "Pick Guests")
        self.resize(940, 480)
        self._build_ui()
        self._load_rows()
        self._apply_filter()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self.searchEdit = QLineEdit()
        self.searchEdit.setPlaceholderText("Search NPC name or tag")
        layout.addWidget(self.searchEdit)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["NPC", "Path", "Tags"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        selection_mode = QAbstractItemView.SingleSelection if self.mode == "host" else QAbstractItemView.ExtendedSelection
        self.table.setSelectionMode(selection_mode)
        self.table.itemDoubleClicked.connect(lambda _item: self.accept())

        body = QHBoxLayout()
        body.addWidget(self.table, 3)

        portrait_panel = QVBoxLayout()
        portrait_panel.addWidget(QLabel("Portrait"))
        self.portraitLabel = HoverPortraitLabel()
        portrait_panel.addWidget(self.portraitLabel)
        self.portraitNameLabel = QLabel("-")
        self.portraitNameLabel.setWordWrap(True)
        portrait_panel.addWidget(self.portraitNameLabel)
        self.copyImageBtn = QPushButton("Copy Image")
        self.copyImageBtn.setEnabled(False)
        portrait_panel.addWidget(self.copyImageBtn)
        portrait_panel.addStretch(1)
        body.addLayout(portrait_panel, 1)
        layout.addLayout(body, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.searchEdit.textChanged.connect(self._apply_filter)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.copyImageBtn.clicked.connect(self.copy_selected_image)

    def _load_rows(self) -> None:
        self._rows = []
        if not self._vault_path:
            return
        root = Path(self._vault_path).expanduser()
        if not root.exists():
            return
        for path in find_markdown_files(root, recurse=True):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeError):
                text = ""
            tags = sorted(extract_hashtags(text))
            if not is_npc_note(path, set(tags)):
                continue
            portrait = resolve_character_art(
                path,
                vault_root=root,
                art_dir=self._art_dir or root / "Assets" / "Character art",
            )
            self._rows.append(
                {
                    "name": _display_name_for_path(path, self._display_names),
                    "path": str(path),
                    "tags": " ".join(f"#{tag}" for tag in tags),
                    "portrait": str(portrait) if portrait else "",
                }
            )
        self._rows.sort(key=lambda row: (row["name"].lower(), row["path"].lower()))

    def _apply_filter(self) -> None:
        needle = self.searchEdit.text().strip().lower()
        self.table.setRowCount(0)
        for row_data in self._rows:
            searchable = f"{row_data['name']} {row_data['path']} {row_data['tags']}".lower()
            if needle and needle not in searchable:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, key in enumerate(("name", "path", "tags")):
                item = QTableWidgetItem(row_data[key])
                item.setData(Qt.UserRole, row_data["portrait"])
                self.table.setItem(row, column, item)
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        rows = sorted({item.row() for item in self.table.selectedItems()})
        if not rows:
            self._set_portrait(None, None)
            return
        row = rows[0]
        name_item = self.table.item(row, 0)
        portrait = name_item.data(Qt.UserRole) if name_item else ""
        self._set_portrait(str(portrait) if portrait else None, name_item.text() if name_item else None)

    def _set_portrait(self, image_path: str | None, name: str | None) -> None:
        self._current_portrait_path = image_path
        loaded = self.portraitLabel.set_portrait(image_path)
        self.portraitNameLabel.setText(name or "-")
        self.copyImageBtn.setEnabled(loaded)

    def copy_selected_image(self) -> None:
        copy_image_to_clipboard(self._current_portrait_path)

    def selected_paths(self) -> list[str]:
        rows = sorted({item.row() for item in self.table.selectedItems()})
        paths: list[str] = []
        for row in rows:
            path_item = self.table.item(row, 1)
            if path_item:
                paths.append(path_item.text())
        return paths


class GuestTab(QWidget):
    generateRequested = Signal(dict)
    clubRequested = Signal(list, dict)

    def __init__(
        self,
        presets: dict[str, dict[str, list[str]]],
        parent=None,
        *,
        display_names: dict[str, str] | None = None,
    ):
        super().__init__(parent)
        self._presets = presets or {}
        self._display_names = _normalize_display_names(display_names)
        self._host_file: str | None = None
        self._forced_files: list[str] = []
        self._last_picks: list = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Preset:"))
        self.presetCombo = QComboBox()
        self.presetCombo.addItem("-- None --", userData=None)
        for key in sorted(self._presets.keys()):
            self.presetCombo.addItem(key, userData=key)
        row1.addWidget(self.presetCombo, 1)

        row1.addWidget(QLabel("Extra Tags:"))
        self.tagsEdit = QLineEdit()
        self.tagsEdit.setPlaceholderText("faction scholar outsider")
        row1.addWidget(self.tagsEdit, 2)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Extra Must Have Tags:"))
        self.anchorEdit = QLineEdit()
        self.anchorEdit.setPlaceholderText("#organizer #neighborhood (optional)")
        row2.addWidget(self.anchorEdit, 2)

        row2.addWidget(QLabel("Mode:"))
        self.modeCombo = QComboBox()
        self.modeCombo.addItem("Random", userData="random")
        self.modeCombo.addItem("Cohesive", userData="cohesive")
        self.modeCombo.addItem("Web", userData="web")
        row2.addWidget(self.modeCombo)

        row2.addWidget(QLabel("NPC Count:"))
        self.countSpin = QSpinBox()
        self.countSpin.setRange(1, 50)
        self.countSpin.setValue(6)
        row2.addWidget(self.countSpin)
        root.addLayout(row2)

        host_row = QHBoxLayout()
        host_row.addWidget(QLabel("Host:"))
        self.hostLabel = QLabel("-")
        host_row.addWidget(self.hostLabel, 1)
        self.pickHostBtn = QPushButton("Pick Host")
        self.clearHostBtn = QPushButton("Clear Host")
        host_row.addWidget(self.pickHostBtn)
        host_row.addWidget(self.clearHostBtn)
        root.addLayout(host_row)

        selected_row = QHBoxLayout()
        selected_row.addWidget(QLabel("Selected Guests:"))
        self.addSelectedGuestBtn = QPushButton("Add Selected")
        self.removeSelectedGuestBtn = QPushButton("Remove Selected")
        self.clearSelectedGuestsBtn = QPushButton("Clear Selected")
        selected_row.addWidget(self.addSelectedGuestBtn)
        selected_row.addWidget(self.removeSelectedGuestBtn)
        selected_row.addWidget(self.clearSelectedGuestsBtn)
        selected_row.addStretch(1)
        root.addLayout(selected_row)

        self.selectedGuestTable = QTableWidget(0, 2)
        self.selectedGuestTable.setHorizontalHeaderLabels(["NPC", "Path"])
        self.selectedGuestTable.horizontalHeader().setStretchLastSection(True)
        self.selectedGuestTable.setSelectionBehavior(QAbstractItemView.SelectRows)
        root.addWidget(self.selectedGuestTable)

        row3 = QHBoxLayout()
        self.generateBtn = QPushButton("Generate Guest List")
        self.createClubBtn = QPushButton("Create Club Event")
        self.createClubBtn.setEnabled(False)
        row3.addWidget(self.generateBtn)
        row3.addWidget(self.createClubBtn)
        row3.addStretch()
        root.addLayout(row3)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Portrait", "NPC (file)", "Path", "Single Tag", "Anchors", "Why"])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 64)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 180)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.itemActivated.connect(self._on_item_activated)
        self.table.itemClicked.connect(self._on_item_clicked)
        self.table.viewport().setCursor(Qt.ArrowCursor)
        root.addWidget(self.table, 1)

        self.generateBtn.clicked.connect(self._on_generate)
        self.createClubBtn.clicked.connect(self._on_create_club)
        self.pickHostBtn.clicked.connect(self.pick_host)
        self.clearHostBtn.clicked.connect(self.clear_host)
        self.addSelectedGuestBtn.clicked.connect(self.add_selected_guests)
        self.removeSelectedGuestBtn.clicked.connect(self.remove_selected_guests)
        self.clearSelectedGuestsBtn.clicked.connect(self.clear_selected_guests)
        self.selectedGuestTable.itemActivated.connect(self._on_manual_guest_activated)
        self.copyResultPortraitShortcut = QShortcut(QKeySequence.Copy, self.table)
        self.copyResultPortraitShortcut.activated.connect(self.copy_selected_result_portrait)
        self.presetCombo.currentIndexChanged.connect(self._on_preset_changed)
        self._on_preset_changed()

    def _on_generate(self) -> None:
        preset_cfg = self._current_preset_config()
        choose_from: list[str] = preset_cfg.get("choose_from") or preset_cfg.get("tags", [])
        payload = {
            "preset_tags": choose_from,
            "free_text": self.tagsEdit.text(),
            "anchor_tag": self.anchorEdit.text(),
            "count": self.countSpin.value(),
            "mode": self.modeCombo.currentData() or "random",
            "host_file": self._host_file,
            "forced_files": list(self._forced_files),
            "must_include_tags": preset_cfg.get("must_include", []),
            "must_have_tags": preset_cfg.get("must_have") or preset_cfg.get("anchors", []),
            "prefer_tags": preset_cfg.get("prefer", []),
            "exclude_tags": preset_cfg.get("exclude", []),
            "eligible_tags": preset_cfg.get("eligible_tags", []),
            "allow_guests": preset_cfg.get("allow_guests", []),
            "prefer_guests": preset_cfg.get("prefer_guests", []),
            "exclude_guests": preset_cfg.get("exclude_guests", []),
        }
        self.generateRequested.emit(payload)

    def pick_host(self) -> None:
        paths = self._pick_npcs("host")
        if paths:
            self.set_host_file(paths[0])

    def clear_host(self) -> None:
        self.set_host_file(None)

    def set_host_file(self, path: str | None) -> None:
        self._host_file = path.strip() if isinstance(path, str) and path.strip() else None
        self._refresh_host_display()

    def add_selected_guests(self) -> None:
        self.add_forced_files(self._pick_npcs("guest"))

    def add_forced_files(self, paths: list[str]) -> None:
        seen = set(self._forced_files)
        for path in paths:
            clean = path.strip()
            if clean and clean not in seen:
                self._forced_files.append(clean)
                seen.add(clean)
        self._populate_selected_guests()

    def remove_selected_guests(self) -> None:
        rows = sorted({item.row() for item in self.selectedGuestTable.selectedItems()}, reverse=True)
        for row in rows:
            path_item = self.selectedGuestTable.item(row, 1)
            if path_item and path_item.text() in self._forced_files:
                self._forced_files.remove(path_item.text())
        self._populate_selected_guests()

    def clear_selected_guests(self) -> None:
        self._forced_files = []
        self._populate_selected_guests()

    def _pick_npcs(self, mode: str) -> list[str]:
        dialog = NpcPickerDialog(
            getattr(self, "_vault_path", None),
            mode=mode,
            parent=self,
            display_names=self._display_names,
        )
        if dialog.exec() == QDialog.Accepted:
            return dialog.selected_paths()
        return []

    def _refresh_host_display(self) -> None:
        self.hostLabel.setText(
            _display_name_for_path(self._host_file, self._display_names)
            if self._host_file
            else "-"
        )
        self.hostLabel.setToolTip(self._host_file or "")

    def _populate_selected_guests(self) -> None:
        self.selectedGuestTable.setRowCount(0)
        for path in self._forced_files:
            row = self.selectedGuestTable.rowCount()
            self.selectedGuestTable.insertRow(row)
            self.selectedGuestTable.setItem(
                row,
                0,
                QTableWidgetItem(_display_name_for_path(path, self._display_names)),
            )
            self.selectedGuestTable.setItem(row, 1, QTableWidgetItem(path))

    def show_results(self, picks) -> None:
        self._last_picks = list(picks or [])
        self.createClubBtn.setEnabled(bool(self._last_picks))
        self.table.setRowCount(0)
        for pick in picks:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setRowHeight(row, 58)
            filename = _display_name_for_path(pick.file_path, self._display_names)
            portrait_path = self._resolve_portrait_path(pick.file_path)
            portrait_label = HoverPortraitLabel(size=48)
            portrait_label.set_portrait(portrait_path)
            name_item = QTableWidgetItem(filename)
            path_item = QTableWidgetItem(pick.file_path)
            tag_item = QTableWidgetItem(pick.single_tag or "")
            anchor_text = ", ".join(f"#{tag}" if tag and not tag.startswith("#") else tag for tag in getattr(pick, "anchor_tags", ()))
            anchor_item = QTableWidgetItem(anchor_text)
            why_item = QTableWidgetItem(getattr(pick, "why_picked", "") or "")
            for item in (name_item, path_item, tag_item, anchor_item, why_item):
                item.setData(Qt.UserRole, portrait_path or "")

            font = path_item.font()
            font.setUnderline(True)
            path_item.setFont(font)
            path_item.setForeground(QBrush(Qt.blue))

            self.table.setCellWidget(row, 0, portrait_label)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, path_item)
            self.table.setItem(row, 3, tag_item)
            self.table.setItem(row, 4, anchor_item)
            self.table.setItem(row, 5, why_item)

    def copy_selected_result_portrait(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 1) or self.table.item(row, 2)
        portrait_path = item.data(Qt.UserRole) if item else ""
        copy_image_to_clipboard(str(portrait_path) if portrait_path else None)

    def _resolve_portrait_path(self, file_path: str) -> str | None:
        portrait = resolve_character_art(
            file_path,
            vault_root=getattr(self, "_vault_path", None),
        )
        return str(portrait) if portrait else None

    def _on_item_activated(self, item: QTableWidgetItem) -> None:
        if item.column() != 2:
            return

        file_path = item.text()
        vault_path = getattr(self, "_vault_path", None)
        vault_name = getattr(self, "_vault_name", None)

        if not open_in_obsidian(file_path, vault_path=vault_path, vault_name=vault_name):
            print(f"[WARN] GuestTab: failed to open in Obsidian: {file_path}")

    def _on_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 2:
            self._on_item_activated(item)

    def _on_manual_guest_activated(self, item: QTableWidgetItem) -> None:
        row = item.row()
        path_item = self.selectedGuestTable.item(row, 1)
        if path_item is None:
            return
        vault_path = getattr(self, "_vault_path", None)
        vault_name = getattr(self, "_vault_name", None)
        if not open_in_obsidian(path_item.text(), vault_path=vault_path, vault_name=vault_name):
            print(f"[WARN] GuestTab: failed to open in Obsidian: {path_item.text()}")

    def _current_preset_config(self) -> dict[str, list[str]]:
        preset_key = self.presetCombo.currentData()
        if isinstance(preset_key, str):
            return self._presets.get(preset_key, {})
        return {}

    def _on_preset_changed(self) -> None:
        self.anchorEdit.clear()

    def _on_create_club(self) -> None:
        if not self._last_picks:
            return
        self.clubRequested.emit(
            list(self._last_picks),
            {
                "host_file": self._host_file,
            },
        )
