from __future__ import annotations
import difflib
import os
import random
import re
import sys
from pathlib import Path

import yaml

from dotenv import load_dotenv; load_dotenv()

SRC_DIR = Path(__file__).resolve().parent
APP_DIR = SRC_DIR.parent
for path in (APP_DIR, SRC_DIR):
    path_str = str(path)
    while path_str in sys.path:
        sys.path.remove(path_str)
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(APP_DIR))

from config_io import LOCAL_CONFIG_NAME, load_startup_config
from debug_console import enable_debug_console
from PySide6 import QtCore, QtWidgets
from PySide6.QtGui import QCloseEvent, QResizeEvent
from PySide6.QtCore import QObject, Qt, Signal, QEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QFrame,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app_types import EventEntry, EventTable
from core.llm_provider import LlmProvider
from core_logic import build_expression, load_tables, roll_scene_concept
from selection_pipeline import build_context_block, prepare_selection, render_prompt
from ui.tag_autocomplete import TagAutocompleteRegistry
from ui.main_window import ensure_ai_rewrite_tab, ensure_campaign_tab, ensure_tag_browser_tab
from ui.club_tab import ClubTab
from ui.guest_tab import GuestTab, NpcPickerDialog
from app.guest_list import generate_guest_list_v2
from app.obsidian_open import open_in_obsidian
from core.club_cache import CacheUnavailableError, activate_cache_root, recognized_legacy_cache
from core.club_generation import ClubGenerationService, club_cache_owner_versions

CONFIG_DIR = APP_DIR / "config"
GUEST_PRESETS_PATH = CONFIG_DIR / "guest_presets.yaml"
CAMPAIGN_STATE_PATH = CONFIG_DIR / "campaign_state.yaml"


def _load_guest_presets(path: Path) -> dict[str, dict[str, list[str]]]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw_cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as exc:
        print(f"[ERROR] Failed to parse guest presets '{path}': {exc}")
        return {}

    raw_presets = (raw_cfg.get("presets") or {}) if isinstance(raw_cfg, dict) else {}

    def _coerce_list(value: object) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            items = value
        else:
            items = []
        result: list[str] = []
        for item in items:
            text = str(item).strip()
            if text:
                result.append(text)
        return result

    presets: dict[str, dict[str, list[str]]] = {}
    for name, entry in raw_presets.items():
        if isinstance(entry, dict):
            choose_from = _coerce_list(entry.get("choose_from") or entry.get("tags"))
            must_have = _coerce_list(entry.get("must_have") or entry.get("anchors"))
            must_include = _coerce_list(entry.get("must_include"))
            prefer = _coerce_list(entry.get("prefer"))
            exclude = _coerce_list(entry.get("exclude"))
            eligible_tags = _coerce_list(entry.get("eligible_tags"))
            allow_guests = _coerce_list(entry.get("allow_guests"))
            prefer_guests = _coerce_list(entry.get("prefer_guests"))
            exclude_guests = _coerce_list(entry.get("exclude_guests"))
        elif isinstance(entry, (list, tuple)):
            choose_from = _coerce_list(entry)
            must_have = []
            must_include = []
            prefer = []
            exclude = []
            eligible_tags = []
            allow_guests = []
            prefer_guests = []
            exclude_guests = []
        else:
            choose_from = []
            must_have = []
            must_include = []
            prefer = []
            exclude = []
            eligible_tags = []
            allow_guests = []
            prefer_guests = []
            exclude_guests = []
        presets[name] = {
            "choose_from": choose_from,
            "must_have": must_have,
            "must_include": must_include,
            "prefer": prefer,
            "exclude": exclude,
            "eligible_tags": eligible_tags,
            "allow_guests": allow_guests,
            "prefer_guests": prefer_guests,
            "exclude_guests": exclude_guests,
            # Legacy aliases for older UI/tests and adjacent tabs.
            "tags": choose_from,
            "anchors": must_have,
        }
    return presets


def _load_guest_display_names(path: Path) -> dict[str, str]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw_cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as exc:
        print(f"[ERROR] Failed to parse guest display names '{path}': {exc}")
        return {}

    raw_names = raw_cfg.get("display_names") if isinstance(raw_cfg, dict) else None
    if not isinstance(raw_names, dict):
        return {}
    result: dict[str, str] = {}
    for note_stem, display_name in raw_names.items():
        key = " ".join(str(note_stem).split()).casefold()
        value = " ".join(str(display_name).split())
        if key and value:
            result[key] = value
    return result


GUEST_PRESETS: dict[str, dict[str, list[str]]] = _load_guest_presets(GUEST_PRESETS_PATH)
GUEST_DISPLAY_NAMES: dict[str, str] = _load_guest_display_names(GUEST_PRESETS_PATH)

SYSTEM_PROMPT = "You are a helpful assistant."


class WorkerSignals(QObject):
    done = Signal(dict)
    error = Signal(str)
    progress = Signal(str)


class GenerateWorker(QtCore.QRunnable):
    def __init__(
        self,
        cfg: dict,
        table: EventTable | None,
        use_table_tags: bool,
        extra_groups_ui: list[tuple[list[str], str]],
        location_text: str,
        npc_count: int,
        lock_selection: dict | None,
        preview_only: bool,
        roll_concept: bool,
        prompt_template: str | None,
        locked_npc_files: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.table = table
        self.use_table_tags = use_table_tags
        self.extra_groups_ui = extra_groups_ui
        self.location_text = location_text
        self.npc_count = npc_count
        self.lock_selection = lock_selection
        self.preview_only = preview_only
        self.roll_concept = roll_concept
        self.prompt_template = prompt_template
        self.locked_npc_files = locked_npc_files or []
        self.signals = WorkerSignals()

    def run(self) -> None:  # type: ignore[override]
        try:
            rng = random.Random()
            rng.seed()
            if self.lock_selection and self.lock_selection.get("scene_concept") and not self.roll_concept:
                entry = EventEntry(
                    scene_concept=self.lock_selection["scene_concept"],
                    tags=self.lock_selection.get("base_tags", []),
                )
            else:
                if not self.table:
                    raise RuntimeError("No event table loaded.")
                entry = roll_scene_concept(self.table, rng)

            base_tags = entry.tags if self.use_table_tags else []
            base_tags_norm, groups_norm = build_expression(
                self.use_table_tags,
                base_tags,
                self.extra_groups_ui,
            )

            rg_path = self.cfg["ripgrep_path"]
            vault = self.cfg["vault_path"]
            sel = prepare_selection(
                rg_path,
                vault,
                base_tags_norm,
                groups_norm,
                self.npc_count,
                self.location_text,
                entry.scene_concept,
                locked_primary_files=self.locked_npc_files,
            )

            context = build_context_block(sel.primary_files, sel.lore_files)
            variables = {
                "location": sel.location,
                "scene_concept": sel.scene_concept,
                "npc_count": sel.npc_count,
                "context": context,
            }

            template_ref = self.prompt_template or self.cfg.get("prompt", {}).get("template")
            if not template_ref:
                raise RuntimeError("No prompt template configured.")
            prompt = render_prompt(template_ref, variables)
            print(f"[DEBUG] Final prompt to model:\n{prompt}\n{'=' * 40}")

            if self.preview_only:
                self.signals.done.emit({"mode": "preview", "selection": sel, "prompt": prompt})
                return

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            provider = LlmProvider(self.cfg)
            output = provider.generate_from_messages(messages, strip_response=False)

            self.signals.done.emit(
                {
                    "mode": "generate",
                    "selection": sel,
                    "prompt": prompt,
                    "output": output,
                }
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.signals.error.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Chronicle Lantern")
        self.resize(1100, 760)

        self.config_selection = load_startup_config(CONFIG_DIR)
        self.app_config_path = str(CONFIG_DIR / LOCAL_CONFIG_NAME)
        self.cfg = self.config_selection.config if self.config_selection.valid else {}
        self.setup_mode = self.config_selection.setup_required or not self.config_selection.valid
        self.tables = load_tables(str(CONFIG_DIR / "tables"))
        self.prompt_options = self._discover_prompt_templates()
        self.default_prompt_template = self._normalize_prompt_ref(self.cfg.get("prompt", {}).get("template"))
        self.guest_presets = GUEST_PRESETS
        self.guest_display_names = GUEST_DISPLAY_NAMES

        self.current_table_key: str | None = None
        self.lock_selection: dict | None = None
        self.tag_autocomplete = TagAutocompleteRegistry(
            (self.cfg.get("character_schema") or {}).get("default_character_tags")
        )
        self.locked_npc_files: list[str] = []

        root = QWidget(self)
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        self.tabs = QTabWidget(self)
        outer.addWidget(self.tabs)

        self.loading_overlay: QFrame | None = None
        self._cursor_override_active = False
        self._widget_enabled_snapshot: dict[QtWidgets.QWidget, bool] = {}

        self.generator_tab = QWidget(self.tabs)
        self.tabs.addTab(self.generator_tab, "Scene Generator")
        self._build_generator_tab(self.generator_tab)
        self.generator_tab.installEventFilter(self)

        self.tag_browser_tab = ensure_tag_browser_tab(
            self.tabs,
            parent=self,
            config=self.cfg,
        )

        self.guest_tab = GuestTab(
            self.guest_presets,
            display_names=self.guest_display_names,
            parent=self,
        )
        self.tabs.addTab(self.guest_tab, "Guest List")
        self.guest_tab._vault_path = self.cfg.get("vault_path")
        self.guest_tab._vault_name = self.cfg.get("vault_name")

        self.club_service = None
        if self.setup_mode:
            self.club_tab = self._setup_placeholder(
                "Club setup is unavailable until a valid local configuration selects a vault."
            )
        else:
            try:
                cache_root, cache_descriptor = activate_cache_root(club_cache_owner_versions())
                self.club_service = ClubGenerationService(
                    self.cfg,
                    cache_root=cache_root,
                    cache_descriptor=cache_descriptor,
                    vault_root=self.cfg.get("vault_path"),
                )
                self.club_tab = ClubTab(self.club_service, parent=self)
            except CacheUnavailableError:
                self.club_tab = self._setup_placeholder(
                    "Club generation is unavailable because its cache could not be initialized safely."
                )
        self.tabs.addTab(self.club_tab, "Club")

        self.campaign_tab = ensure_campaign_tab(
            self.tabs,
            parent=self,
            state_path=str(CAMPAIGN_STATE_PATH),
        )

        self.ai_rewrite_tab = ensure_ai_rewrite_tab(
            self.tabs,
            parent=self,
            config=self.cfg,
            config_path=self.app_config_path,
            guest_presets=self.guest_presets,
        )

        self.status = self.statusBar()
        if self.config_selection.error is not None:
            self.status.showMessage(
                f"Setup required: {self.config_selection.error.source} configuration is "
                f"{self.config_selection.error.category}."
            )
        elif self.setup_mode:
            self.status.showMessage("Setup required: create config/app.local.yaml or select a configuration.")
        elif recognized_legacy_cache(APP_DIR / ".club-cache"):
            self.status.showMessage("Ready. Legacy cache ignored.")
        else:
            self.status.showMessage("Ready.")
        self.pool = QtCore.QThreadPool.globalInstance()
        self._previous_tab_index = self.tabs.currentIndex()
        self._reverting_tab_change = False

        self._wire_generator_signals()
        self._wire_guest_tab()
        self._wire_tag_browser_tab()
        self._wire_tag_autocomplete()
        self._wire_campaign_tab()
        self._initialize_state()
        if self.setup_mode:
            for tab in (self.generator_tab, self.tag_browser_tab, self.guest_tab, self.club_tab, self.ai_rewrite_tab):
                tab.setEnabled(False)
        else:
            QtCore.QTimer.singleShot(500, self._refresh_tag_autocomplete_if_visible)
            QtCore.QTimer.singleShot(0, self._prompt_resume_on_launch)

    def _setup_placeholder(self, message: str) -> QWidget:
        tab = QWidget(self.tabs)
        layout = QVBoxLayout(tab)
        label = QLabel(message, tab)
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)
        return tab

    # ------------------------------------------------------------------
    def _build_generator_tab(self, tab: QWidget) -> None:
        layout = QVBoxLayout(tab)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Event Table:"))
        self.table_combo = QComboBox()
        for key, table in self.tables.items():
            self.table_combo.addItem(f"{table.name} ({key})", userData=key)
        row1.addWidget(self.table_combo, 1)
        self.btn_roll = QPushButton("Roll Concept")
        row1.addWidget(self.btn_roll)
        layout.addLayout(row1)

        row_prompt = QHBoxLayout()
        row_prompt.addWidget(QLabel("Prompt Template:"))
        self.prompt_combo = QComboBox()
        if self.prompt_options:
            for ref in self.prompt_options:
                self.prompt_combo.addItem(self._format_prompt_label(ref), userData=ref)
        else:
            self.prompt_combo.addItem("No templates found", userData=None)
            self.prompt_combo.setEnabled(False)
        row_prompt.addWidget(self.prompt_combo, 1)
        layout.addLayout(row_prompt)

        row_location = QHBoxLayout()
        row_location.addWidget(QLabel("Location:"))
        self.location_edit = QLineEdit()
        row_location.addWidget(self.location_edit, 2)
        row_location.addWidget(QLabel("NPC Count:"))
        self.npc_spin = QSpinBox()
        self.npc_spin.setRange(0, 8)
        self.npc_spin.setValue(3)
        row_location.addWidget(self.npc_spin)
        layout.addLayout(row_location)

        tag_row = QHBoxLayout()
        self.chk_use_table = QCheckBox("Use table tags")
        self.chk_use_table.setChecked(True)
        tag_row.addWidget(self.chk_use_table)
        tag_row.addStretch(1)
        layout.addLayout(tag_row)

        self.group_rows: list[tuple[QComboBox, QLineEdit]] = []
        self.group_container = QVBoxLayout()
        layout.addLayout(self.group_container)
        self._add_group_row()

        button_row = QHBoxLayout()
        self.btn_preview = QPushButton("Preview")
        self.btn_generate = QPushButton("Generate")
        self.btn_copy_md = QPushButton("Copy Output")
        button_row.addWidget(self.btn_preview)
        button_row.addWidget(self.btn_generate)
        button_row.addWidget(self.btn_copy_md)
        layout.addLayout(button_row)

        info_row = QHBoxLayout()
        self.lbl_concept = QLabel("Scene Concept: -")
        self.lbl_tags = QLabel("Active Tags: -")
        info_row.addWidget(self.lbl_concept, 1)
        info_row.addWidget(self.lbl_tags, 2)
        layout.addLayout(info_row)

        panels = QHBoxLayout()
        self.list_files = QListWidget()
        panels.addWidget(self.list_files, 1)
        self.output = QTextEdit()
        self.output.setAcceptRichText(False)
        panels.addWidget(self.output, 2)
        layout.addLayout(panels)

        self._build_npc_picker(layout)

        self._loading_control_widgets = [
            self.btn_preview,
            self.btn_generate,
            self.btn_roll,
            self.btn_copy_md,
            self.table_combo,
            self.prompt_combo,
            self.location_edit,
            self.npc_spin,
        ]

        self._init_loading_overlay(tab)

    def _build_npc_picker(self, layout: QVBoxLayout) -> None:
        picker_label = QLabel("Selected NPCs")
        layout.addWidget(picker_label)

        button_row = QHBoxLayout()
        self.btn_pick_npcs = QPushButton("Pick NPCs")
        self.btn_remove_npc = QPushButton("Remove Selected")
        self.btn_clear_npcs = QPushButton("Clear")
        button_row.addWidget(self.btn_pick_npcs)
        button_row.addWidget(self.btn_remove_npc)
        button_row.addWidget(self.btn_clear_npcs)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.locked_npc_table = QTableWidget(0, 2)
        self.locked_npc_table.setHorizontalHeaderLabels(["NPC", "Path"])
        self.locked_npc_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.locked_npc_table)

    def _add_group_row(self) -> None:
        row = QHBoxLayout()
        combo = QComboBox()
        combo.addItems(["OR", "AND"])
        tags_edit = QLineEdit()
        tags_edit.setPlaceholderText("tag1, tag2")
        remove_btn = QPushButton("Remove")

        def remove_row() -> None:
            if (combo, tags_edit) in self.group_rows:
                self.group_rows.remove((combo, tags_edit))
            for widget in (combo, tags_edit, remove_btn):
                widget.setParent(None)
            row.setParent(None)

        remove_btn.clicked.connect(remove_row)

        row.addWidget(QLabel("Group:"))
        row.addWidget(combo)
        row.addWidget(tags_edit, 1)
        row.addWidget(remove_btn)
        self.group_container.addLayout(row)
        self.group_rows.append((combo, tags_edit))
        if hasattr(self, "tag_autocomplete"):
            self.tag_autocomplete.bind(tags_edit)

    def _init_loading_overlay(self, parent: QWidget) -> None:
        overlay = QFrame(parent)
        overlay.setObjectName("loadingOverlay")
        overlay.setStyleSheet(
            "#loadingOverlay { background-color: rgba(0, 0, 0, 170); }"
            "#loadingLabel { color: white; font-size: 16px; font-weight: 500; }"
        )
        overlay_layout = QVBoxLayout(overlay)
        overlay_layout.setAlignment(Qt.AlignCenter)

        spinner_widget: QProgressBar | None = None
        if self.cfg.get("ui", {}).get("spinner", True):
            spinner_widget = QProgressBar(overlay)
            spinner_widget.setRange(0, 0)
            spinner_widget.setTextVisible(False)
            spinner_widget.setFixedWidth(220)
            overlay_layout.addWidget(spinner_widget)

        label = QLabel("Working...", overlay)
        label.setObjectName("loadingLabel")
        label.setAlignment(Qt.AlignCenter)
        overlay_layout.addWidget(label)

        self.loading_overlay = overlay
        self.loading_label = label
        self.loading_spinner = spinner_widget
        self.loading_overlay.hide()
        self._sync_loading_overlay()

    def _sync_loading_overlay(self) -> None:
        if not self.loading_overlay:
            return
        self.loading_overlay.setGeometry(self.generator_tab.rect())
        self.loading_overlay.raise_()

    def _set_loading_state(self, busy: bool, message: str | None = None) -> None:
        if not self.loading_overlay:
            return
        if message:
            self.loading_label.setText(message)
        self.loading_overlay.setVisible(busy)
        if busy:
            self.loading_overlay.raise_()
            if not self._cursor_override_active:
                QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
                self._cursor_override_active = True
        elif self._cursor_override_active:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._cursor_override_active = False
        controls = getattr(self, "_loading_control_widgets", [])
        if busy:
            self._widget_enabled_snapshot = {widget: widget.isEnabled() for widget in controls}
            for widget in controls:
                widget.setEnabled(False)
        else:
            snapshot = getattr(self, "_widget_enabled_snapshot", {})
            for widget in controls:
                prev = snapshot.get(widget, True)
                widget.setEnabled(prev)
            self._widget_enabled_snapshot = {}

    def _wire_generator_signals(self) -> None:
        self.btn_preview.clicked.connect(self.on_preview)
        self.btn_generate.clicked.connect(self.on_generate)
        self.btn_roll.clicked.connect(self.on_roll_only)
        self.btn_copy_md.clicked.connect(self.copy_output)
        self.table_combo.currentIndexChanged.connect(self._on_table_changed)
        self.btn_pick_npcs.clicked.connect(self.pick_scene_npcs)
        self.btn_remove_npc.clicked.connect(self.remove_selected_locked_npc)
        self.btn_clear_npcs.clicked.connect(self.clear_locked_npcs)
        self.locked_npc_table.itemDoubleClicked.connect(self._on_npc_path_activated)

    def eventFilter(self, obj: QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if obj is self.generator_tab and event.type() in (QEvent.Resize, QEvent.Show):
            self._sync_loading_overlay()
        return super().eventFilter(obj, event)

    def _wire_guest_tab(self) -> None:
        def _on_guest_generate(payload: dict) -> None:
            rg_path = self.cfg["ripgrep_path"]
            vault = self.cfg["vault_path"]

            print(
                "[SELECT] GuestListV2 request "
                f"preset={payload.get('preset_tags')} "
                f"text={payload.get('free_text')} "
                f"anchor={payload.get('anchor_tag')} "
                f"must_include={payload.get('must_include_tags')} "
                f"must_have={payload.get('must_have_tags')} "
                f"prefer={payload.get('prefer_tags')} "
                f"exclude={payload.get('exclude_tags')} "
                f"eligible={payload.get('eligible_tags')} "
                f"allow_guests={payload.get('allow_guests')} "
                f"prefer_guests={payload.get('prefer_guests')} "
                f"exclude_guests={payload.get('exclude_guests')} "
                f"count={payload.get('count')} "
                f"mode={payload.get('mode')} "
                f"host={payload.get('host_file')} "
                f"forced={len(payload.get('forced_files') or [])}"
            )

            count = int(payload.get("count", 0))
            if count < 1:
                print("[WARN] GuestListV2: count < 1 was requested; forcing to 1.")
                count = 1

            picks = generate_guest_list_v2(
                rg_path=rg_path,
                vault=vault,
                preset_tags=payload.get("preset_tags") or [],
                free_text=payload.get("free_text") or "",
                count=count,
                anchor_tag=(payload.get("anchor_tag") or None),
                extra_groups_ui=[],
                shuffle_seed=None,
                mode=payload.get("mode") or "random",
                host_file=payload.get("host_file") or None,
                forced_files=payload.get("forced_files") or [],
                must_include_tags=payload.get("must_include_tags") or [],
                must_have_tags=payload.get("must_have_tags") or [],
                prefer_tags=payload.get("prefer_tags") or [],
                exclude_tags=payload.get("exclude_tags") or [],
                eligible_tags=payload.get("eligible_tags") or [],
                allow_guests=payload.get("allow_guests") or [],
                prefer_guests=payload.get("prefer_guests") or [],
                exclude_guests=payload.get("exclude_guests") or [],
            )
            self.guest_tab.show_results(picks)

        self.guest_tab.generateRequested.connect(_on_guest_generate)
        self.guest_tab.clubRequested.connect(self._on_club_requested)

    def _on_club_requested(self, picks: list, options: dict) -> None:
        if self.club_service is None or not isinstance(self.club_tab, ClubTab):
            self.status.showMessage("Club setup is not complete.")
            return
        self.tabs.setCurrentWidget(self.club_tab)
        self.club_tab.create_event_from_picks(picks, host_path=(options or {}).get("host_file"))

    def _wire_tag_browser_tab(self) -> None:
        self.tag_browser_tab.addSceneGroupRequested.connect(self._add_tag_to_scene_group)
        self.tag_browser_tab.addGuestTagsRequested.connect(
            lambda tag: self._append_unique_tag_text(self.guest_tab.tagsEdit, tag)
        )
        self.tag_browser_tab.addGuestAnchorsRequested.connect(
            lambda tag: self._append_unique_tag_text(self.guest_tab.anchorEdit, tag)
        )
        self.tag_browser_tab.tagIndexUpdated.connect(self.tag_autocomplete.set_tags)

    def _wire_tag_autocomplete(self) -> None:
        for _combo, tags_edit in self.group_rows:
            self.tag_autocomplete.bind(tags_edit)
        self.tag_autocomplete.bind(self.guest_tab.tagsEdit)
        self.tag_autocomplete.bind(self.guest_tab.anchorEdit)

    def _wire_campaign_tab(self) -> None:
        self.campaign_tab.dirtyChanged.connect(self._on_campaign_dirty_changed)
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _refresh_tag_autocomplete_if_visible(self) -> None:
        if self.isVisible():
            self.tag_browser_tab.refresh_autocomplete(limit=20)

    def pick_scene_npcs(self) -> None:
        dialog = NpcPickerDialog(self.cfg.get("vault_path"), mode="guest", parent=self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        added = 0
        for path in dialog.selected_paths():
            if self._add_locked_npc(path):
                added += 1
        self.status.showMessage(f"Selected {len(self.locked_npc_files)} NPCs." if added else "No new NPCs selected.")

    def _add_locked_npc(self, path: str) -> bool:
        clean = path.strip()
        if not clean or clean in self.locked_npc_files:
            return False
        self.locked_npc_files.append(clean)
        self._populate_locked_npcs()
        return True

    def remove_selected_locked_npc(self) -> None:
        rows = sorted({item.row() for item in self.locked_npc_table.selectedItems()}, reverse=True)
        for row in rows:
            path_item = self.locked_npc_table.item(row, 1)
            if path_item and path_item.text() in self.locked_npc_files:
                self.locked_npc_files.remove(path_item.text())
        self._populate_locked_npcs()

    def clear_locked_npcs(self) -> None:
        self.locked_npc_files = []
        self._populate_locked_npcs()

    def _populate_locked_npcs(self) -> None:
        self.locked_npc_table.setRowCount(0)
        for path in self.locked_npc_files:
            row = self.locked_npc_table.rowCount()
            self.locked_npc_table.insertRow(row)
            self.locked_npc_table.setItem(row, 0, QTableWidgetItem(Path(path).stem))
            self.locked_npc_table.setItem(row, 1, QTableWidgetItem(path))

    def _on_npc_path_activated(self, item: QTableWidgetItem) -> None:
        row = item.row()
        path_item = item.tableWidget().item(row, 1) if item.tableWidget() else None
        if path_item is None:
            return
        if not open_in_obsidian(
            path_item.text(),
            vault_path=self.cfg.get("vault_path"),
            vault_name=self.cfg.get("vault_name"),
        ):
            print(f"[WARN] MainWindow: failed to open NPC in Obsidian: {path_item.text()}")

    def _add_tag_to_scene_group(self, tag: str) -> None:
        target: QLineEdit | None = None
        for _combo, tags_edit in self.group_rows:
            if not tags_edit.text().strip():
                target = tags_edit
                break
        if target is None:
            self._add_group_row()
            _combo, target = self.group_rows[-1]
        self._append_unique_tag_text(target, tag)

    @staticmethod
    def _append_unique_tag_text(edit: QLineEdit, tag: str) -> None:
        normalized = tag.strip().lstrip("#")
        if not normalized:
            return
        existing = [part.strip() for part in edit.text().replace(",", " ").split() if part.strip()]
        existing_keys = {part.lstrip("#").lower() for part in existing}
        if normalized.lower() not in existing_keys:
            existing.append(normalized)
        edit.setText(" ".join(existing))

    def _prompt_resume_on_launch(self) -> None:
        tab = getattr(self, "ai_rewrite_tab", None)
        if tab is not None:
            tab.prompt_unfinished_on_launch(self)

    def _on_campaign_dirty_changed(self, dirty: bool) -> None:
        idx = self.tabs.indexOf(self.campaign_tab)
        if idx >= 0:
            self.tabs.setTabText(idx, "Campaign *" if dirty else "Campaign")

    def _on_tab_changed(self, index: int) -> None:
        if self._reverting_tab_change:
            return
        previous = getattr(self, "_previous_tab_index", index)
        previous_widget = self.tabs.widget(previous) if previous >= 0 else None
        if previous_widget is self.campaign_tab and self.campaign_tab.is_dirty():
            if not self.campaign_tab.confirm_leave_with_unsaved_changes():
                self._reverting_tab_change = True
                self.tabs.setCurrentIndex(previous)
                self._reverting_tab_change = False
                return
        self._previous_tab_index = index

    def _initialize_state(self) -> None:
        default_key = self.cfg.get("ui", {}).get("default_table")
        resolved_key = self.resolve_table_key(default_key, self.tables) if self.tables else None
        if not resolved_key and self.tables:
            resolved_key = next(iter(self.tables))
        self.current_table_key = resolved_key
        if resolved_key:
            idx = self.table_combo.findData(resolved_key)
            if idx >= 0:
                self.table_combo.setCurrentIndex(idx)
        if self.prompt_options and self.default_prompt_template:
            idx = self.prompt_combo.findData(self.default_prompt_template)
            if idx >= 0:
                self.prompt_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_prompt_ref(ref: str | None) -> str | None:
        if not isinstance(ref, str):
            return None
        s = ref.strip()
        if not s:
            return None
        return Path(s).as_posix()

    @staticmethod
    def _format_prompt_label(ref: str) -> str:
        stem = Path(ref).stem.replace("_", " ").replace("-", " ").strip()
        label = stem.title() if stem else ref
        return f"{label} ({ref})" if label and label != ref else ref

    def _discover_prompt_templates(self) -> list[str]:
        prompt_cfg = self.cfg.get("prompt") or {}
        candidates: list[str] = []
        default = self._normalize_prompt_ref(prompt_cfg.get("template"))
        if default:
            candidates.append(default)
        extra = prompt_cfg.get("templates")
        if isinstance(extra, (list, tuple)):
            for item in extra:
                norm = self._normalize_prompt_ref(item)
                if norm:
                    candidates.append(norm)
        prompt_dir = APP_DIR / "templates" / "prompts"
        if prompt_dir.exists():
            for file in sorted(prompt_dir.glob("*.j2")):
                try:
                    rel = file.relative_to(APP_DIR).as_posix()
                except ValueError:
                    rel = file.as_posix()
                candidates.append(rel)
        unique: list[str] = []
        seen: set[str] = set()
        for ref in candidates:
            if ref not in seen:
                unique.append(ref)
                seen.add(ref)
        return unique

    def resolve_table_key(self, config_key: str | None, tables: dict) -> str | None:
        if not config_key or not tables:
            return None
        keys = list(tables.keys())
        if config_key in tables:
            return config_key
        lower_map = {k.lower(): k for k in keys}
        if config_key.lower() in lower_map:
            return lower_map[config_key.lower()]
        base, _ = os.path.splitext(config_key)
        for try_ext in ("", ".yaml", ".yml"):
            candidate = (base + try_ext).lower()
            if candidate in lower_map:
                return lower_map[candidate]
        base_name = os.path.splitext(config_key.lower())[0]
        for k in keys:
            if os.path.splitext(k.lower())[0] == base_name:
                return k
        close = difflib.get_close_matches(config_key, keys, n=1, cutoff=0.7)
        return close[0] if close else None

    # UI helpers -------------------------------------------------------
    def current_table(self) -> EventTable | None:
        key = self.table_combo.currentData()
        if key is None:
            return None
        return self.tables.get(key)

    def current_prompt_template(self) -> str | None:
        data = self.prompt_combo.currentData()
        if isinstance(data, str) and data:
            return data
        return self._normalize_prompt_ref(self.cfg.get("prompt", {}).get("template"))

    def _extra_groups_from_ui(self) -> list[tuple[list[str], str]]:
        groups: list[tuple[list[str], str]] = []
        for combo, tags_edit in self.group_rows:
            raw = tags_edit.text().strip()
            if not raw:
                continue
            tags = [t.strip() for t in re.split(r"[\s,]+", raw) if t.strip()]
            if not tags:
                continue
            groups.append((tags, combo.currentText()))
        return groups

    def _run_worker(self, preview_only: bool, roll_concept: bool) -> None:
        if preview_only and roll_concept:
            msg = "Rolling scene concept..."
        elif preview_only:
            msg = "Preparing preview..."
        else:
            msg = "Generating with AI..."
        self._set_loading_state(True, msg)

        worker = GenerateWorker(
            cfg=self.cfg,
            table=self.current_table(),
            use_table_tags=self.chk_use_table.isChecked(),
            extra_groups_ui=self._extra_groups_from_ui(),
            location_text=self.location_edit.text(),
            npc_count=int(self.npc_spin.value()),
            lock_selection=self.lock_selection,
            preview_only=preview_only,
            roll_concept=roll_concept,
            prompt_template=self.current_prompt_template(),
            locked_npc_files=list(self.locked_npc_files),
        )
        worker.signals.done.connect(self.on_done)
        worker.signals.error.connect(self.on_error)
        self.status.showMessage("Working...")
        self.pool.start(worker)

    # Slots ------------------------------------------------------------
    def on_preview(self) -> None:
        self._run_worker(preview_only=True, roll_concept=True)

    def on_generate(self) -> None:
        self._run_worker(preview_only=False, roll_concept=False)

    def on_roll_only(self) -> None:
        self.lock_selection = None
        self._run_worker(preview_only=True, roll_concept=True)

    def on_done(self, data: dict) -> None:
        self._set_loading_state(False)
        mode = data["mode"]
        sel = data["selection"]
        self.lock_selection = {
            "scene_concept": sel.scene_concept,
            "base_tags": sel.active_tags,
        }

        self.lbl_concept.setText(f"Scene Concept: {sel.scene_concept}")
        tags_text = ", ".join(sel.active_tags) if sel.active_tags else "-"
        self.lbl_tags.setText(f"Active Tags: {tags_text}")
        self.list_files.clear()
        self.list_files.addItem("--- Primary ---")
        for path in sel.primary_files:
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(path)
            self.list_files.addItem(item)
        if sel.lore_files:
            self.list_files.addItem("--- Lore ---")
            for path in sel.lore_files:
                self.list_files.addItem(path)

        if mode == "preview":
            self.output.setPlainText(data["prompt"])
        else:
            self.output.setPlainText(data["output"])
        self.status.showMessage("Done.")

    def on_error(self, msg: str) -> None:
        self._set_loading_state(False)
        self.status.showMessage("Error.")
        QtWidgets.QMessageBox.critical(self, "Error", msg)

    def copy_output(self) -> None:
        text = self.output.toPlainText()
        QtWidgets.QApplication.clipboard().setText(text)
        self.status.showMessage("Copied to clipboard.")

    def _on_table_changed(self) -> None:
        key = self.table_combo.currentData()
        if key is not None:
            self.current_table_key = key

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_loading_overlay()

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        if self.campaign_tab.is_dirty():
            if not self.campaign_tab.confirm_close_with_unsaved_changes():
                event.ignore()
                return
        super().closeEvent(event)


def main() -> None:
    enable_debug_console(APP_DIR / ".debug" / "scenesmith-debug.log")
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()



