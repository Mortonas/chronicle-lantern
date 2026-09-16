from __future__ import annotations

import json
import os
import random
import string
import sys
import threading
import shutil
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import yaml

from PySide6 import QtCore, QtGui, QtWidgets

APP_DIR = Path(__file__).resolve().parents[2]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config_io import save_app_config
from core.file_discovery import find_markdown_files
from core.markdown_rewrite import apply_image_preserving_rewrite, extract_non_image_text
from core.llm_provider import LlmProvider
from rewrite_session import RewriteSessionController

SYSTEM_INSTRUCTION = "Output must be pure Markdown. Do not include image syntax like ![[...]] or ![...](...)."


class PromptPreviewDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None, *, user_prompt: str, context_chars: int, prompt_chars: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Prompt Preview")
        self.resize(720, 540)

        layout = QtWidgets.QVBoxLayout(self)

        counts_label = QtWidgets.QLabel(
            f"Context characters: {context_chars} | Prompt characters: {prompt_chars}"
        )
        layout.addWidget(counts_label)

        system_label = QtWidgets.QLabel(SYSTEM_INSTRUCTION)
        system_label.setWordWrap(True)
        system_label.setStyleSheet("color: #586e75; font-style: italic;")
        layout.addWidget(system_label)

        self.text_edit = QtWidgets.QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setAcceptRichText(False)
        self.text_edit.setPlainText(user_prompt)
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.text_edit.setFont(fixed_font)
        layout.addWidget(self.text_edit, 1)

        button_row = QtWidgets.QHBoxLayout()
        self.btn_copy = QtWidgets.QPushButton("Copy")
        self.btn_save = QtWidgets.QPushButton("Save Preview")
        self.btn_close = QtWidgets.QPushButton("Close")
        button_row.addWidget(self.btn_copy)
        button_row.addWidget(self.btn_save)
        button_row.addStretch(1)
        button_row.addWidget(self.btn_close)
        layout.addLayout(button_row)

        self.btn_copy.clicked.connect(self._copy_to_clipboard)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_close.clicked.connect(self.accept)

    def _copy_to_clipboard(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self.text_edit.toPlainText())

    def _on_save(self) -> None:
        QtWidgets.QMessageBox.information(self, "Save Preview", "Saving previews will be added later.")




class HugeFileDecisionDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None, *, rel_path: str, abs_path: Path, context_len: int, threshold: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Huge File Detected")
        self._choice: str = "cancel"

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        file_name = Path(rel_path).name or rel_path
        description = QtWidgets.QLabel(
            f"<b>{file_name}</b> exceeds the context limit ({context_len} > {threshold})."
        )
        description.setTextFormat(QtCore.Qt.RichText)
        description.setWordWrap(True)
        layout.addWidget(description)

        detail = QtWidgets.QLabel(f"Full path: {abs_path}")
        detail.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        detail.setWordWrap(True)
        layout.addWidget(detail)

        hint = QtWidgets.QLabel("Choose how to handle this file:")
        layout.addWidget(hint)

        button_box = QtWidgets.QDialogButtonBox()

        self._add_action_button(button_box, "Skip this file", "skip_one")
        self._add_action_button(button_box, "Proceed this file", "proceed_one")
        self._add_action_button(button_box, "Proceed all huge files", "proceed_all")

        cancel_btn = button_box.addButton("Cancel run", QtWidgets.QDialogButtonBox.RejectRole)
        cancel_btn.clicked.connect(lambda: self._set_choice("cancel"))
        layout.addWidget(button_box)

    def _add_action_button(self, box: QtWidgets.QDialogButtonBox, label: str, mode: str) -> None:
        btn = box.addButton(label, QtWidgets.QDialogButtonBox.ActionRole)
        btn.clicked.connect(lambda _, m=mode: self._set_choice(m))

    def _set_choice(self, mode: str) -> None:
        self._choice = mode
        if mode == "cancel":
            self.reject()
        else:
            self.accept()

    def exec_choice(self) -> str:
        result = self.exec()
        if result == QtWidgets.QDialog.Accepted:
            return self._choice
        return "cancel"



@dataclass
class SessionInfo:
    session_id: str
    name: str
    directory: Path
    pending_paths: list[str]
    failed_paths: list[str]
    total_files: int
    last_activity: str
    last_activity_dt: datetime
    config: dict


class AiRewriteTab(QtWidgets.QWidget):
    """UI surface for configuring the AI rewrite workflow without executing it yet."""

    TEMPLATE_ROOT = APP_DIR / "templates" / "Rewrite Prompts"

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        self._config_ref: Optional[dict] = None
        self._config_path: Optional[str] = None
        self._vault_path: Optional[Path] = None

        self._anchor_path: Optional[Path] = None
        self._selected_template_name: Optional[str] = None
        self._selected_template_path: Optional[Path] = None
        self._selected_file: Optional[Path] = None

        self._last_backup_dir: Optional[Path] = None
        self._has_backup: bool = False
        self._current_session_id: Optional[str] = None
        self._current_session_dir: Optional[Path] = None

        self._session_controller: Optional[RewriteSessionController] = None
        self._session_state: str = "idle"
        self._session_total_files: int = 0
        self._session_last_completed: Optional[str] = None
        self._session_next_pending: Optional[str] = None
        self._session_retry_failed: bool = False
        self._session_last_status: Optional[str] = None
        self._session_is_dry_run: bool = True
        self._dry_cleanup_done: bool = False
        self._session_config_lock = threading.Lock()
        self._current_session_config: Dict[str, Any] = {}
        self._current_huge_policy: Dict[str, Any] = {"asked": False, "mode": None}
        self._launch_resume_prompt_shown = False
        self._guest_presets: Dict[str, dict[str, list[str]]] = {}

        self._build_ui()
        self._wire_signals()
        self._load_templates(initial=True)
        self._update_action_states()

    # ------------------------------------------------------------------
    def set_app_config(self, config: dict, config_path: Optional[str] = None) -> None:
        self._config_ref = config
        self._config_path = config_path
        vault = (config or {}).get("vault_path")
        if vault:
            self._vault_path = Path(vault).expanduser().resolve()
        self._load_from_config()

    def set_guest_presets(self, presets: Optional[dict[str, dict[str, list[str]]]]) -> None:
        self._guest_presets = dict(presets or {})

    # UI construction ---------------------------------------------------
    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        self.warning_banner = QtWidgets.QLabel()
        self.warning_banner.setVisible(False)
        self.warning_banner.setObjectName("warningBanner")
        self.warning_banner.setStyleSheet(
            "#warningBanner { color: #b58900; background-color: #fdf6e3; border: 1px solid #b58900; padding: 6px; }"
        )
        layout.addWidget(self.warning_banner)

        anchor_row = QtWidgets.QHBoxLayout()
        anchor_label = QtWidgets.QLabel("Anchor:")
        self.lbl_anchor = QtWidgets.QLabel("(not set)")
        self.lbl_anchor.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        anchor_row.addWidget(anchor_label)
        anchor_row.addWidget(self.lbl_anchor, 1)
        self.btn_change_anchor = QtWidgets.QPushButton("Change...")
        self.btn_open_anchor = QtWidgets.QPushButton("Open")
        self.btn_open_anchor.setEnabled(False)
        anchor_row.addWidget(self.btn_change_anchor)
        anchor_row.addWidget(self.btn_open_anchor)
        layout.addLayout(anchor_row)

        session_row = QtWidgets.QHBoxLayout()
        session_row.addWidget(QtWidgets.QLabel("Session name:"))
        self.session_name_edit = QtWidgets.QLineEdit()
        self.session_name_edit.setPlaceholderText("Auto (uses session ID)")
        session_row.addWidget(self.session_name_edit, 1)
        layout.addLayout(session_row)

        template_row = QtWidgets.QHBoxLayout()
        template_row.addWidget(QtWidgets.QLabel("Rewrite template:"))
        self.template_combo = QtWidgets.QComboBox()
        self.template_combo.setEnabled(False)
        template_row.addWidget(self.template_combo, 1)
        self.btn_template_refresh = QtWidgets.QPushButton("Refresh")
        template_row.addWidget(self.btn_template_refresh)
        layout.addLayout(template_row)

        options_row = QtWidgets.QHBoxLayout()
        self.chk_dry_run = QtWidgets.QCheckBox("Dry run")
        self.chk_dry_run.setChecked(True)
        options_row.addWidget(self.chk_dry_run)
        self.chk_include_subfolders = QtWidgets.QCheckBox("Include subfolders")
        self.chk_include_subfolders.setChecked(True)
        options_row.addWidget(self.chk_include_subfolders)
        self.chk_retry_failed = QtWidgets.QCheckBox("Retry failed on resume")
        self.chk_retry_failed.setChecked(True)
        options_row.addWidget(self.chk_retry_failed)
        options_row.addStretch(1)
        layout.addLayout(options_row)

        file_row = QtWidgets.QHBoxLayout()
        self.lbl_selected_file = QtWidgets.QLabel("Selected file: (none)")
        self.lbl_selected_file.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        file_row.addWidget(self.lbl_selected_file, 1)
        self.btn_select_file = QtWidgets.QPushButton("Select File...")
        file_row.addWidget(self.btn_select_file)
        layout.addLayout(file_row)

        action_row = QtWidgets.QHBoxLayout()
        self.btn_rewrite_folder = QtWidgets.QPushButton("Rewrite Folder")
        self.btn_rewrite_file = QtWidgets.QPushButton("Rewrite File")
        self.btn_preview = QtWidgets.QPushButton("Preview Prompt")
        action_row.addWidget(self.btn_rewrite_folder)
        action_row.addWidget(self.btn_rewrite_file)
        action_row.addWidget(self.btn_preview)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        run_row = QtWidgets.QHBoxLayout()
        self.lbl_progress = QtWidgets.QLabel("Idle.")
        self.lbl_progress.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        run_row.addWidget(self.lbl_progress, 1)
        self.btn_stop = QtWidgets.QPushButton("Stop Run")
        self.btn_stop.setEnabled(False)
        self.btn_continue = QtWidgets.QPushButton("Continue Run")
        self.btn_continue.setEnabled(False)
        self.btn_session_manager = QtWidgets.QPushButton("Session Manager…")
        run_row.addWidget(self.btn_stop)
        run_row.addWidget(self.btn_continue)
        run_row.addWidget(self.btn_session_manager)
        layout.addLayout(run_row)

        self.log_output = QtWidgets.QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setAcceptRichText(False)
        self.log_output.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.log_output.setFont(fixed_font)
        layout.addWidget(self.log_output, 1)

        undo_row = QtWidgets.QHBoxLayout()
        self.btn_undo = QtWidgets.QPushButton("Undo Last Rewrite")
        self.btn_undo.setEnabled(False)
        undo_row.addWidget(self.btn_undo)
        self.btn_delete_backups = QtWidgets.QPushButton("Delete All Backups")
        self.btn_delete_backups.setEnabled(False)
        undo_row.addWidget(self.btn_delete_backups)
        self.btn_prune_sessions = QtWidgets.QPushButton("Prune Sessions")
        self.btn_prune_sessions.setEnabled(False)
        undo_row.addWidget(self.btn_prune_sessions)
        undo_row.addStretch(1)
        layout.addLayout(undo_row)

    def _wire_signals(self) -> None:
        self.btn_change_anchor.clicked.connect(self._on_change_anchor)
        self.btn_open_anchor.clicked.connect(self._on_open_anchor)
        self.btn_template_refresh.clicked.connect(lambda: self._load_templates(force_refresh=True))
        self.template_combo.currentIndexChanged.connect(self._on_template_changed)
        self.chk_dry_run.toggled.connect(self._on_dry_run_toggled)
        self.chk_include_subfolders.toggled.connect(self._on_include_subfolders_toggled)
        self.chk_retry_failed.toggled.connect(self._on_retry_failed_toggled)
        self.btn_select_file.clicked.connect(self._on_select_file)
        self.btn_rewrite_folder.clicked.connect(self._on_rewrite_folder)
        self.btn_rewrite_file.clicked.connect(self._on_rewrite_file)
        self.btn_preview.clicked.connect(self._on_preview)
        self.btn_undo.clicked.connect(self.undo_last_rewrite)
        self.btn_stop.clicked.connect(self._on_stop_run)
        self.btn_continue.clicked.connect(self._on_continue_run)
        self.btn_session_manager.clicked.connect(self._on_session_manager)
        self.btn_delete_backups.clicked.connect(self._on_delete_all_backups)
        self.btn_prune_sessions.clicked.connect(self._on_prune_sessions)

    # Configuration -----------------------------------------------------
    def _load_from_config(self) -> None:
        rewrite_cfg = self._rewrite_settings()
        anchor = rewrite_cfg.get("anchor_folder")
        self._anchor_path = Path(anchor) if isinstance(anchor, str) and anchor else None
        self._update_anchor_display()

        template_name = rewrite_cfg.get("template_name")
        self._selected_template_name = template_name if isinstance(template_name, str) and template_name else None

        dry_default = rewrite_cfg.get("dry_run_default")
        include_default = rewrite_cfg.get("include_subfolders_default")
        retry_default = rewrite_cfg.get("retry_failed_on_resume_default")
        self.chk_dry_run.blockSignals(True)
        self.chk_include_subfolders.blockSignals(True)
        self.chk_retry_failed.blockSignals(True)
        self.chk_dry_run.setChecked(True if dry_default is None else bool(dry_default))
        self.chk_include_subfolders.setChecked(True if include_default is None else bool(include_default))
        self.chk_retry_failed.setChecked(True if retry_default is None else bool(retry_default))
        self.chk_dry_run.blockSignals(False)
        self.chk_include_subfolders.blockSignals(False)
        self.chk_retry_failed.blockSignals(False)
        self._session_retry_failed = self.chk_retry_failed.isChecked()

        self._selected_file = None
        self._update_selected_file_label()
        self._load_templates(initial=True)
        self._update_action_states()

    # Template handling -------------------------------------------------
    def _load_templates(self, initial: bool = False, force_refresh: bool = False) -> None:
        templates: list[Path] = []
        if self.TEMPLATE_ROOT.exists():
            templates = sorted(self.TEMPLATE_ROOT.glob("*.j2"), key=lambda p: p.name.lower())

        if not templates:
            message = (
                f"Rewrite template directory missing: {self.TEMPLATE_ROOT}"
                if not self.TEMPLATE_ROOT.exists()
                else f"No .j2 templates found in {self.TEMPLATE_ROOT}"
            )
            self._show_template_warning(message)
            self.template_combo.clear()
            self.template_combo.setEnabled(False)
            self._selected_template_path = None
            self._selected_template_name = None
            self._update_action_states()
            return

        self._hide_template_warning()
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        for tpl in templates:
            self.template_combo.addItem(tpl.name, userData=str(tpl))
        self.template_combo.setEnabled(True)

        if self._selected_template_name:
            index = self.template_combo.findText(self._selected_template_name, QtCore.Qt.MatchFixedString)
            if index >= 0:
                self.template_combo.setCurrentIndex(index)
            else:
                self.template_combo.setCurrentIndex(0)
                self._selected_template_name = self.template_combo.currentText()
        else:
            self.template_combo.setCurrentIndex(0)
            self._selected_template_name = self.template_combo.currentText()

        data = self.template_combo.currentData()
        self._selected_template_path = Path(data) if data else None
        self.template_combo.blockSignals(False)

        if not initial or force_refresh:
            self._log_selection("template", self._selected_template_name or "")
            self._persist_rewrite_value("template_name", self._selected_template_name)
        self._update_action_states()

    def _show_template_warning(self, message: str) -> None:
        self.warning_banner.setText(f"[WARN] {message}")
        self.warning_banner.setVisible(True)

    def _hide_template_warning(self) -> None:
        self.warning_banner.setVisible(False)
        self.warning_banner.setText("")

    def _on_template_changed(self) -> None:
        if self.template_combo.count() == 0:
            return
        self._selected_template_name = self.template_combo.currentText()
        data = self.template_combo.currentData()
        self._selected_template_path = Path(data) if data else None
        self._log_selection("template", self._selected_template_name)
        self._persist_rewrite_value("template_name", self._selected_template_name)
        self._update_action_states()

    # Anchor handling ---------------------------------------------------
    def _on_change_anchor(self) -> None:
        start_dir = str(self._anchor_path) if self._anchor_path else os.path.expanduser("~")
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Anchor Folder", start_dir)
        if not directory:
            return
        path = Path(directory).expanduser().resolve()
        self._anchor_path = path
        self._log_selection("anchor", str(path))
        self._persist_rewrite_value("anchor_folder", str(path))
        self.btn_open_anchor.setEnabled(True)
        self._selected_file = None
        self._update_anchor_display()
        self._update_selected_file_label()
        self._update_action_states()

    def _on_open_anchor(self) -> None:
        if not self._anchor_path:
            QtWidgets.QMessageBox.information(self, "Open Anchor", "Choose an anchor folder first.")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self._anchor_path)))

    def _update_anchor_display(self) -> None:
        if self._anchor_path:
            self.lbl_anchor.setText(str(self._anchor_path))
            self.btn_open_anchor.setEnabled(True)
        else:
            self.lbl_anchor.setText("(not set)")
            self.btn_open_anchor.setEnabled(False)

    # File selection ----------------------------------------------------
    def _on_select_file(self) -> None:
        if not self._anchor_path:
            QtWidgets.QMessageBox.information(self, "Select Markdown File", "Choose an anchor folder first.")
            return
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Markdown File",
            str(self._anchor_path),
            "Markdown (*.md)",
        )
        if not file_path:
            return
        path = Path(file_path).resolve()
        self._selected_file = path
        self._log_selection("file", str(path))
        self._update_selected_file_label()
        self._update_action_states()

    def _update_selected_file_label(self) -> None:
        display = str(self._selected_file) if self._selected_file else "(none)"
        self.lbl_selected_file.setText(f"Selected file: {display}")

    # Session handling --------------------------------------------------
    def _generate_session_id(self) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
        return f"{stamp}-{rand}"

    def _rewrite_settings(self) -> Dict[str, Any]:
        return (self._config_ref or {}).get("rewrite") or {}

    def _anchor_storage_root(self) -> Path:
        rewrite_cfg = self._rewrite_settings()
        setting = rewrite_cfg.get("session_root")
        anchor = self._anchor_path
        if isinstance(setting, str):
            key = setting.strip()
            lowered = key.lower()
            if lowered in {"", "anchor", "vault", "default"}:
                setting = None
            elif lowered == "app":
                base = APP_DIR / ".ai-rewrite"
                if anchor:
                    return base / self._anchor_storage_segment(anchor)
                return base / "default"
            else:
                try:
                    base = Path(key).expanduser().resolve()
                except Exception:
                    base = Path(key).expanduser()
                if anchor:
                    return base / self._anchor_storage_segment(anchor)
                return base / "default"
        if anchor:
            return anchor / ".ai-rewrite"
        return APP_DIR / ".ai-rewrite"

    def _session_storage_paths(self) -> dict[str, Path]:
        root = self._anchor_storage_root()
        return {
            "root": root,
            "sessions": root / "sessions",
            "backups": root / "backups",
            "dry_run": root / "dry-run",
        }

    @staticmethod
    def _anchor_storage_segment(anchor: Path) -> str:
        try:
            resolved = anchor.expanduser().resolve()
        except Exception:
            resolved = anchor.expanduser()
        digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:8]
        label = resolved.name
        if not label:
            drive = getattr(resolved, 'drive', '')
            label = drive.replace(":", "") or "anchor"
        safe = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in label)
        return f"{safe or 'anchor'}-{digest}"

    def _start_session(self, mode: str, target: Path) -> Optional[dict]:
        if not self._anchor_path:
            return None
        session_id = self._generate_session_id()
        name = self.session_name_edit.text().strip() or session_id
        if not self.session_name_edit.text().strip():
            self.session_name_edit.setText(name)

        storage_paths = self._session_storage_paths()
        base_dir = storage_paths["root"]
        sessions_dir = storage_paths["sessions"]
        global_dry = storage_paths["dry_run"]
        global_backups = storage_paths["backups"]
        session_dir = sessions_dir / session_id
        for directory in (base_dir, global_dry, global_backups, sessions_dir, session_dir):
            directory.mkdir(parents=True, exist_ok=True)
        for sub in ("preview", "dry-run", "backups"):
            (session_dir / sub).mkdir(parents=True, exist_ok=True)

        rewrite_cfg = self._rewrite_settings()
        max_retries_cfg = self._coerce_int(rewrite_cfg.get("max_retries"), 2)
        timeout_cfg = self._coerce_int(rewrite_cfg.get("llm_timeout_seconds"), 180)
        backoff_cfg = self._coerce_float(rewrite_cfg.get("retry_backoff_base_seconds"), 1.0)
        snapshot = {
            "id": session_id,
            "name": name,
            "timestamp": datetime.now().isoformat(),
            "anchor": str(self._anchor_path),
            "template": str(self._selected_template_path) if self._selected_template_path else None,
            "template_name": self._selected_template_name,
            "dry_run": self.chk_dry_run.isChecked(),
            "include_subfolders": self.chk_include_subfolders.isChecked(),
            "retry_failed_on_resume": self.chk_retry_failed.isChecked(),
            "huge_file_policy": {"asked": False, "mode": None},
            "max_retries": max_retries_cfg,
            "llm_timeout_seconds": timeout_cfg,
            "retry_backoff_base_seconds": backoff_cfg,
            "mode": mode,
            "target": str(target),
            "max_bytes": rewrite_cfg.get("max_bytes"),
            "concurrency": rewrite_cfg.get("concurrency"),
            "system_instruction": SYSTEM_INSTRUCTION,
        }
        snapshot_path = session_dir / "config.yaml"
        with snapshot_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(snapshot, fh, sort_keys=False, allow_unicode=True)

        ledger_entry = {
            "type": "start",
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
            "name": name,
            "mode": mode,
            "target": str(target),
            "template": self._selected_template_name,
            "dry_run": self.chk_dry_run.isChecked(),
            "include_subfolders": self.chk_include_subfolders.isChecked(),
            "retry_failed_on_resume": self.chk_retry_failed.isChecked(),
        }
        ledger_path = session_dir / "ledger.jsonl"
        with ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ledger_entry, ensure_ascii=False) + "\n")

        self._current_session_id = session_id
        self._current_session_dir = session_dir
        self._log_selection("session", f"id={session_id} name={name}")
        self.append_log("INFO", f"Session {session_id} initialized at {session_dir}")
        return {"id": session_id, "dir": session_dir, "name": name}

    def _start_session_run(self, session: dict, *, mode: str, target: Path) -> None:
        if self._session_controller and self._session_state == "running":
            QtWidgets.QMessageBox.warning(
                self,
                "Rewrite Session",
                "A rewrite run is already in progress. Stop it before starting another run.",
            )
            return
        if not self._anchor_path:
            self.append_log("ERROR", "Anchor path unknown; cannot start rewrite run.")
            return

        files = self._gather_session_files(mode, target)
        if not files:
            self.append_log("WARN", "No eligible markdown files found for rewrite.")
            self._session_state = "paused"
            self.lbl_progress.setText("Paused — no pending files")
            self._update_session_controls()
            return

        retry_failed = self.chk_retry_failed.isChecked()
        self._session_retry_failed = retry_failed
        self._session_total_files = len(files)
        self._session_last_completed = None
        self._session_last_status = None

        session_dir = Path(session["dir"])
        session_config = self._load_session_config_data(session_dir)
        self._current_session_config = session_config
        huge_policy = self._normalize_huge_policy(session_config.get("huge_file_policy"))
        self._current_huge_policy = huge_policy

        dry_run = bool(session_config.get("dry_run", self.chk_dry_run.isChecked()))
        self._session_is_dry_run = dry_run
        self._dry_cleanup_done = False
        session_config["dry_run"] = dry_run
        self._write_session_config_data(session_dir, session_config)
        if dry_run:
            self._record_dry_run_manifest(session_dir, files)

        if not self._ensure_backup_writable(session_dir, dry_run=dry_run):
            self._session_state = "paused"
            self._session_total_files = 0
            self._session_next_pending = None
            self.lbl_progress.setText(f"Paused — backups unavailable")
            self._update_session_controls()
            return

        policy_updater = self._make_huge_policy_updater(session_dir)
        rewrite_defaults = self._rewrite_settings()
        max_retries = self._coerce_int(session_config.get("max_retries"), self._coerce_int(rewrite_defaults.get("max_retries"), 2))
        timeout_seconds = self._coerce_int(session_config.get("llm_timeout_seconds"), self._coerce_int(rewrite_defaults.get("llm_timeout_seconds"), 180))
        backoff_seconds = self._coerce_float(session_config.get("retry_backoff_base_seconds"), self._coerce_float(rewrite_defaults.get("retry_backoff_base_seconds"), 1.0))

        controller = RewriteSessionController(
            session_dir=session_dir,
            anchor_path=self._anchor_path,
            process_file=self._build_process_file(session_dir),
            retry_failed_on_resume=retry_failed,
            log_callback=self._session_log_callback,
            state_callback=self._session_state_callback,
            progress_callback=self._session_progress_callback,
            file_finished_callback=self._session_file_done_callback,
            huge_policy=huge_policy,
            huge_file_decider=self._huge_file_decider,
            huge_policy_updater=policy_updater,
            huge_context_limit=20,
            max_retries=max_retries,
            llm_timeout_seconds=timeout_seconds,
            retry_backoff_base_seconds=backoff_seconds,
        )
        controller.initialize_worklist(files)
        self._session_controller = controller
        self._session_next_pending = controller.next_file()

        self.lbl_progress.setText(f"Preparing run — {len(files)} files queued")
        self._session_state = "running"
        self._update_session_controls()
        self.append_log(
            "INFO",
            "Session %s running (%d files, retry_failed=%s)"
            % (session["id"], len(files), retry_failed),
        )
        try:
            controller.start()
        except RuntimeError as exc:
            self.append_log("ERROR", f"Failed to start session run: {exc}")
            self._session_state = "paused"
            self._update_session_controls()

    def _load_session_config_data(self, session_dir: Path) -> Dict[str, Any]:
        config_path = session_dir / "config.yaml"
        if not config_path.exists():
            return {}
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                return {}
            return data
        except Exception:  # pylint: disable=broad-except
            return {}

    def _write_session_config_data(self, session_dir: Path, data: Dict[str, Any]) -> None:
        config_path = session_dir / "config.yaml"
        config_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    def _normalize_huge_policy(self, policy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(policy, dict):
            return {"asked": False, "mode": None}
        return {"asked": bool(policy.get("asked", False)), "mode": policy.get("mode")}

    def _make_huge_policy_updater(self, session_dir: Path) -> Callable[[Dict[str, Any]], None]:
        def _updater(policy: Dict[str, Any]) -> None:
            normalized = self._normalize_huge_policy(policy)
            with self._session_config_lock:
                data = self._load_session_config_data(session_dir)
                data["huge_file_policy"] = normalized
                self._write_session_config_data(session_dir, data)
                if self._current_session_dir == session_dir:
                    self._current_session_config = data
                    self._current_huge_policy = normalized
        return _updater

    def _huge_file_decider(self, rel_path: str, abs_path: Path, context_len: int, policy: Dict[str, Any]) -> str:
        result: Dict[str, str] = {"mode": "proceed"}
        done = threading.Event()

        def ask() -> None:
            try:
                dialog = HugeFileDecisionDialog(
                    self,
                    rel_path=rel_path,
                    abs_path=abs_path,
                    context_len=context_len,
                    threshold=20,
                )
                choice = dialog.exec_choice()
            except Exception:  # pylint: disable=broad-except
                choice = "proceed"
            self._log_selection("huge_file", f"{rel_path} -> {choice}")
            result["mode"] = choice or "proceed"
            done.set()

        QtCore.QTimer.singleShot(0, ask)
        if not done.wait(timeout=60):
            return "proceed"
        return result.get("mode", "proceed")

    def _gather_session_files(self, mode: str, target: Path) -> list[Path]:
        if mode == "file":
            path = target.resolve()
            if not path.exists():
                self.append_log("ERROR", f"Selected file does not exist: {path}")
                return []
            return [path]
        target_path = target.resolve()
        if not target_path.exists():
            self.append_log("ERROR", f"Target folder does not exist: {target_path}")
            return []
        include = self.chk_include_subfolders.isChecked()
        candidates = [p.resolve() for p in find_markdown_files(target_path, include)]
        return sorted(candidates, key=lambda p: self._relative_session_path(p).lower())

    def _relative_session_path(self, path: Path) -> str:
        anchor = self._anchor_path
        if anchor:
            try:
                rel = path.resolve().relative_to(anchor.resolve())
                return rel.as_posix()
            except Exception:  # pylint: disable=broad-except
                pass
        return path.name

    def _session_log_callback(self, level: str, message: str) -> None:
        QtCore.QMetaObject.invokeMethod(
            self,
            "_handle_session_log",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, str(level)),
            QtCore.Q_ARG(str, str(message)),
        )

    def _session_state_callback(self, state: str) -> None:
        QtCore.QMetaObject.invokeMethod(
            self,
            "_handle_session_state_change",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, str(state)),
        )

    def _session_progress_callback(self, index: int, total: int, path: str) -> None:
        QtCore.QMetaObject.invokeMethod(
            self,
            "_handle_session_progress",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(int, int(index)),
            QtCore.Q_ARG(int, int(total)),
            QtCore.Q_ARG(str, str(path)),
        )

    def _session_file_done_callback(self, rel_path: str, status: str) -> None:
        QtCore.QMetaObject.invokeMethod(
            self,
            "_handle_session_file_finished",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, str(rel_path)),
            QtCore.Q_ARG(str, str(status)),
        )

    def _build_process_file(self, session_dir: Path) -> Callable[[Path], tuple[str, dict[str, Any]] | str]:
        anchor_root = self._anchor_path
        if anchor_root is None:
            try:
                anchor_root = session_dir.parents[2]
            except IndexError:
                anchor_root = session_dir
        vault_root = self._vault_path or anchor_root
        session_id = self._current_session_id or session_dir.name
        storage_paths = self._session_storage_paths()
        global_backup_root = storage_paths["backups"] / session_id
        global_dry_root = storage_paths["dry_run"]
        preview_root = session_dir / "preview"
        session_backup_root = session_dir / "backups"
        session_dry_root = session_dir / "dry-run"

        def _ensure_preview_dir(rel_obj: Path) -> Path:
            if str(rel_obj.parent) in ("", "."):
                target = preview_root
            else:
                target = preview_root / rel_obj.parent
            target.mkdir(parents=True, exist_ok=True)
            return target

        def process(path: Path):
            rel_path = self._relative_session_path(path)
            self._session_log_callback("INFO", f"Processing {rel_path}")
            try:
                stat_info = path.stat()
            except FileNotFoundError:
                self._session_log_callback("WARN", f"Missing file: {path}")
                return "skipped"
            except OSError as exc:
                self._session_log_callback("ERROR", f"Unable to stat {path}: {exc}")
                return "failed", {"message": str(exc)}

            with self._session_config_lock:
                session_cfg = dict(self._current_session_config or {})

            dry_run = bool(session_cfg.get("dry_run", True))
            max_bytes = self._coerce_int(session_cfg.get("max_bytes"), None)
            if isinstance(max_bytes, int) and max_bytes > 0 and stat_info.st_size > max_bytes:
                self._session_log_callback("WARN", f"Skipped {rel_path}: size {stat_info.st_size} > {max_bytes}")
                return "skipped"

            template_path_value = session_cfg.get("template")
            if not template_path_value:
                self._session_log_callback("ERROR", f"No template configured; cannot process {rel_path}")
                return "failed", {"message": "missing template"}

            template_path = Path(template_path_value)
            if not template_path.exists():
                self._session_log_callback("ERROR", f"Template not found: {template_path}")
                return "failed", {"message": "template missing"}

            try:
                original_text = path.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                self._session_log_callback("ERROR", f"Failed to read {path}: {exc}")
                return "failed", {"message": str(exc)}

            if "\x00" in original_text:
                self._session_log_callback("WARN", f"Skipped {rel_path}: binary content detected")
                return "skipped"

            try:
                template_text = template_path.read_text(encoding="utf-8")
            except Exception as exc:
                self._session_log_callback("ERROR", f"Failed to read template {template_path}: {exc}")
                return "failed", {"message": str(exc)}

            context_text = extract_non_image_text(original_text)
            rendered_prompt, missing_placeholder = self._render_prompt_text(template_text, context_text)
            rel_obj = Path(rel_path)
            preview_dir = _ensure_preview_dir(rel_obj)
            prompt_path = preview_dir / f"{rel_obj.name}.prompt.txt"
            try:
                prompt_path.write_text(rendered_prompt, encoding="utf-8")
            except Exception as exc:
                self._session_log_callback("DEBUG", f"Prompt preview write failed for {rel_path}: {exc}")

            context_chars = len(context_text)
            prompt_chars = len(rendered_prompt)
            self._session_log_callback(
                "DEBUG",
                f"context_chars={context_chars} prompt_chars={prompt_chars} file={rel_path}",
            )
            if missing_placeholder:
                self._session_log_callback("WARN", f"Template missing {{context}}; appended context for {rel_path}")

            try:
                provider = LlmProvider(self._config_ref or {})
            except Exception as exc:
                self._session_log_callback("ERROR", f"Failed to initialize model adapter: {exc}")
                return "failed", {"message": str(exc)}

            system_instruction = str(session_cfg.get("system_instruction") or SYSTEM_INSTRUCTION)
            try:
                llm_output = provider.chat(rendered_prompt, system_instruction=system_instruction)
            except Exception as exc:
                self._session_log_callback("ERROR", f"LLM failure for {rel_path}: {exc}")
                return "failed", {"message": str(exc)}

            response_path = preview_dir / f"{rel_obj.name}.response.md"
            try:
                response_path.write_text(llm_output, encoding="utf-8")
            except Exception as exc:
                self._session_log_callback("DEBUG", f"Response preview write failed for {rel_path}: {exc}")

            new_text = apply_image_preserving_rewrite(original_text, llm_output)
            self._session_log_callback(
                "DEBUG",
                f"rewrite_chars {len(original_text)} -> {len(new_text)} for {rel_path}",
            )

            if dry_run:
                try:
                    session_out = session_dry_root / rel_obj
                    session_out.parent.mkdir(parents=True, exist_ok=True)
                    session_out.write_text(new_text, encoding="utf-8")
                    if global_dry_root:
                        global_out = global_dry_root / rel_obj
                        global_out.parent.mkdir(parents=True, exist_ok=True)
                        global_out.write_text(new_text, encoding="utf-8")
                except Exception as exc:
                    self._session_log_callback("ERROR", f"Dry-run write failed for {rel_path}: {exc}")
                    return "failed", {"message": str(exc)}
                self._session_log_callback("INFO", f"Dry-run content saved for {rel_path}")
                return "done"

            try:
                local_backup = (session_backup_root / rel_obj).with_suffix(".bak.md")
                local_backup.parent.mkdir(parents=True, exist_ok=True)
                local_backup.write_text(original_text, encoding="utf-8")
                if global_backup_root:
                    global_backup = (global_backup_root / rel_obj).with_suffix(".bak.md")
                    global_backup.parent.mkdir(parents=True, exist_ok=True)
                    global_backup.write_text(original_text, encoding="utf-8")
            except Exception as exc:
                self._session_log_callback("ERROR", f"Backup failed for {rel_path}: {exc}")
                return "failed", {"message": str(exc)}

            try:
                path.write_text(new_text, encoding="utf-8")
            except Exception as exc:
                self._session_log_callback("ERROR", f"Failed to write rewritten content for {rel_path}: {exc}")
                return "failed", {"message": str(exc)}

            self._session_log_callback("INFO", f"Rewrite completed for {rel_path}")
            if global_backup_root:
                self._queue_backup_state_update(global_backup_root)
            else:
                self._queue_backup_state_update(session_backup_root)

            return "done"

        return process

    def _queue_backup_state_update(self, directory: Optional[Path]) -> None:
        if directory is None:
            return
        QtCore.QMetaObject.invokeMethod(
            self,
            "_handle_backup_created",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, str(directory)),
        )

    @QtCore.Slot(str)
    def _handle_backup_created(self, path_str: str) -> None:
        if not path_str:
            return
        self._last_backup_dir = Path(path_str)
        self.set_backup_available(True)
        self._update_action_states()
    @QtCore.Slot(str, str)
    def _handle_session_log(self, level: str, message: str) -> None:
        self.append_log(level, message)

    @QtCore.Slot(str)
    def _handle_session_state_change(self, state: str) -> None:
        self._session_state = state
        controller = self._session_controller
        next_rel = controller.next_file() if controller else None
        self._session_next_pending = next_rel
        if state == "running":
            if not self.lbl_progress.text().startswith("Processing file"):
                self.lbl_progress.setText("Running — preparing next file...")
        elif state == "paused":
            if next_rel:
                self.lbl_progress.setText(f"Paused — next: {Path(next_rel).name}")
            else:
                self.lbl_progress.setText("Paused — no pending files")
        elif state == "completed":
            self._cleanup_dry_run_artifacts()
            last_name = Path(self._session_last_completed).name if self._session_last_completed else "none"
            self.lbl_progress.setText(f"Completed — last: {last_name}")
        elif state == "aborted":
            self.lbl_progress.setText("Aborted.")
        else:
            self.lbl_progress.setText("Idle.")
        self._update_session_controls()

    @QtCore.Slot(int, int, str)
    def _handle_session_progress(self, index: int, total: int, path: str) -> None:
        self._session_state = "running"
        self._session_total_files = max(self._session_total_files, total)
        self.lbl_progress.setText(f"Processing file {index} of {total} — {Path(path).name}")
        self._update_session_controls()

    @QtCore.Slot(str, str)
    def _handle_session_file_finished(self, rel_path: str, status: str) -> None:
        self._session_last_completed = rel_path
        self._session_last_status = status
        controller = self._session_controller
        if controller:
            self._session_next_pending = controller.next_file()
        if status == "failed":
            self.append_log("WARN", f"Rewrite failed: {rel_path}")

    def _update_session_controls(self) -> None:
        state = self._session_state
        if state == "running":
            self.btn_stop.setEnabled(True)
            self.btn_continue.setEnabled(False)
        elif state == "paused":
            controller = self._session_controller
            has_next = bool(controller and controller.next_file())
            self.btn_stop.setEnabled(False)
            self.btn_continue.setEnabled(has_next)
        elif state in {"completed", "aborted"}:
            self.btn_stop.setEnabled(False)
            self.btn_continue.setEnabled(False)
        else:
            self.btn_stop.setEnabled(False)
            self.btn_continue.setEnabled(False)
    # Checkbox handlers -------------------------------------------------
    def _on_dry_run_toggled(self, checked: bool) -> None:
        self._persist_rewrite_value("dry_run_default", bool(checked))
        self._log_selection("dry_run_default", str(bool(checked)))

    def _on_include_subfolders_toggled(self, checked: bool) -> None:
        self._persist_rewrite_value("include_subfolders_default", bool(checked))
        self._log_selection("include_subfolders_default", str(bool(checked)))

    def _on_retry_failed_toggled(self, checked: bool) -> None:
        self._persist_rewrite_value("retry_failed_on_resume_default", bool(checked))
        self._log_selection("retry_failed_on_resume_default", str(bool(checked)))
        self._session_retry_failed = bool(checked)

    # Actions -----------------------------------------------------------
    def _on_rewrite_folder(self) -> None:
        if not self._anchor_path or not self._selected_template_path:
            QtWidgets.QMessageBox.information(self, "Rewrite Folder", "Select an anchor and template first.")
            return
        session = self._start_session("folder", self._anchor_path)
        if session is None:
            return
        self._start_session_run(session, mode="folder", target=self._anchor_path)

    def _on_rewrite_file(self) -> None:
        if not self._anchor_path or not self._selected_template_path:
            QtWidgets.QMessageBox.information(self, "Rewrite File", "Select an anchor and template first.")
            return
        if not self._selected_file:
            self._on_select_file()
        if not self._selected_file:
            return
        session = self._start_session("file", self._selected_file)
        if session is None:
            return
        self._start_session_run(session, mode="file", target=self._selected_file)


    def _on_stop_run(self) -> None:
        controller = self._session_controller
        if not controller:
            self.append_log("DEBUG", "Stop requested but no active session.")
            return
        if self._session_state != "running":
            self.append_log("DEBUG", "Stop ignored; session not running.")
            return
        controller.request_stop()
        self.append_log("SELECT", "session stop requested")
        self.lbl_progress.setText("Stop requested — finishing current file...")

    def _on_continue_run(self) -> None:
        controller = self._session_controller
        if not controller:
            self.append_log("DEBUG", "Continue requested but no active session.")
            return
        if self._session_state != "paused":
            self.append_log("DEBUG", "Continue ignored; session not paused.")
            return
        self.append_log("SELECT", "session continue")
        self.lbl_progress.setText("Resuming run...")
        controller.continue_run()

    def _render_prompt_text(self, template_text: str, context_text: str) -> tuple[str, bool]:
        if '{context}' in template_text:
            return template_text.replace('{context}', context_text), False
        rendered = template_text.rstrip() + '\n\n---\n' + context_text
        return rendered, True

    def _on_preview(self) -> None:
        if not self._anchor_path or not self._selected_template_path or not self._selected_file:
            QtWidgets.QMessageBox.information(
                self,
                "Preview Prompt",
                "Select an anchor, template, and markdown file first.",
            )
            return
        try:
            file_text = Path(self._selected_file).read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Preview Prompt", f"Failed to read file: {exc}")
            return

        context_text = extract_non_image_text(file_text)
        context_chars = len(context_text)

        template_path = self._selected_template_path
        if template_path is None or not template_path.exists():
            QtWidgets.QMessageBox.warning(self, "Preview Prompt", "Template file is missing.")
            return
        try:
            template_text = template_path.read_text(encoding="utf-8")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Preview Prompt", f"Failed to read template: {exc}")
            return

        rendered_prompt, missing_context_placeholder = self._render_prompt_text(template_text, context_text)

        prompt_chars = len(rendered_prompt)
        self.append_log(
            "DEBUG",
            "context_chars=%s prompt_chars=%s template=%s"
            % (context_chars, prompt_chars, self._selected_template_name),
        )

        if missing_context_placeholder:
            self.append_log("WARN", "Template missing {context}; appended context at the end.")

        dialog = PromptPreviewDialog(
            self,
            user_prompt=rendered_prompt,
            context_chars=context_chars,
            prompt_chars=prompt_chars,
        )
        dialog.exec()

    # Logging helpers ---------------------------------------------------
    def append_log(self, level: str, message: str) -> None:
        level_norm = (level or "INFO").upper()
        self.log_output.append(f"[{level_norm}] {message}")

    def clear_logs(self) -> None:
        self.log_output.clear()

    def _log_selection(self, label: str, value: str) -> None:
        self.append_log("SELECT", f"{label}={value}")

    # Undo --------------------------------------------------------------
    def set_backup_available(self, available: bool) -> None:
        self._has_backup = bool(available)
        self.btn_undo.setEnabled(self._has_backup)

    def undo_last_rewrite(self) -> None:
        backup_dir = self._resolve_backup_dir()
        if not backup_dir:
            self.append_log("DEBUG", "No backup available to undo")
            self.set_backup_available(False)
            return
        if not self._vault_path:
            self.append_log("ERROR", "Vault path unknown; cannot undo")
            return
        restored = 0
        failed = 0
        for bak in backup_dir.rglob("*.bak.md"):
            try:
                rel = bak.relative_to(backup_dir)
                rel_str = rel.as_posix()
                if rel_str.endswith(".bak.md"):
                    rel_str = rel_str[:-7] + ".md"
                target_path = self._vault_path / Path(rel_str)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(bak.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
                restored += 1
            except Exception as exc:  # pylint: disable=broad-except
                failed += 1
                self.append_log("ERROR", f"Undo failed for {bak}: {exc}")
        self.append_log("INFO", f"Undo completed: restored {restored} files; failures {failed}")
        self.set_backup_available(False)
        self._last_backup_dir = None
    def _on_delete_all_backups(self) -> None:
        if self._session_state == "running":
            QtWidgets.QMessageBox.warning(
                self,
                "Delete All Backups",
                "Stop the current session before deleting backups.",
            )
            return

        storage_paths = self._session_storage_paths()
        base_ai = storage_paths["root"]
        global_backups = storage_paths["backups"]
        sessions_dir = storage_paths["sessions"]

        first_result = QtWidgets.QMessageBox.question(
            self,
            "Delete All Backups",
            "This will permanently remove all AI rewrite backups. Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if first_result != QtWidgets.QMessageBox.Yes:
            self.append_log("DEBUG", "Delete backups cancelled at first confirmation.")
            return

        confirm_box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Warning,
            "Confirm Delete All Backups",
            "This action cannot be undone. Delete all backups now?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            self,
        )
        confirm_box.setDefaultButton(QtWidgets.QMessageBox.No)
        if confirm_box.exec() != QtWidgets.QMessageBox.Yes:
            self.append_log("DEBUG", "Delete backups cancelled at final confirmation.")
            return

        targets: list[Path] = []
        global_target = None
        if global_backups.exists():
            targets.append(global_backups)
            global_target = global_backups

        session_parent = sessions_dir if sessions_dir.exists() else None
        if session_parent:
            for session_dir in session_parent.iterdir():
                if not session_dir.is_dir():
                    continue
                backups_dir = session_dir / "backups"
                if backups_dir.exists():
                    targets.append(backups_dir)

        if not targets:
            QtWidgets.QMessageBox.information(
                self,
                "Delete All Backups",
                "No backup directories were found to delete.",
            )
            self.append_log("INFO", "Delete backups: no backup directories found.")
            return

        stats = {"files": 0, "dirs": 0}
        failures: list[str] = []

        for backups_dir in targets:
            files, dirs = self._count_tree_entries(backups_dir)
            try:
                shutil.rmtree(backups_dir)
                stats["files"] += files
                stats["dirs"] += dirs + 1
                parent = backups_dir.parent
                if parent:
                    if global_target and backups_dir == global_target:
                        self._prune_empty_dirs(parent, base_ai)
                    elif session_parent and session_parent.exists():
                        self._prune_empty_dirs(parent, session_parent)
            except Exception as exc:  # pylint: disable=broad-except
                failures.append(f"{backups_dir}: {exc}")

        summary = (
            f"Deleted backups: directories={len(targets)}; files_removed={stats['files']}; "
            f"dirs_removed={stats['dirs']}"
        )
        self.append_log("INFO", summary)
        if failures:
            for message in failures:
                self.append_log("WARN", f"Backup deletion issue: {message}")
            QtWidgets.QMessageBox.warning(
                self,
                "Delete All Backups",
                "Backups deleted with some errors. Check the log for details.",
            )
        else:
            QtWidgets.QMessageBox.information(
                self,
                "Delete All Backups",
                "All backups were deleted successfully.",
            )

        self.set_backup_available(False)
        self._last_backup_dir = None
        self._update_action_states()

    def _on_prune_sessions(self) -> None:
        if self._session_state == "running":
            QtWidgets.QMessageBox.warning(
                self,
                "Prune Sessions",
                "Stop the current session before pruning.",
            )
            return

        policy = self._retention_policy()
        max_sessions = policy["max_sessions"]
        max_backup_age_days = policy["max_backup_age_days"]

        storage_paths = self._session_storage_paths()
        base_ai = storage_paths["root"]
        sessions_dir = storage_paths["sessions"]
        global_backups_dir = storage_paths["backups"]

        sessions_info = self._collect_session_infos()
        info_by_id = {info.session_id: info for info in sessions_info}
        keep_ids = {info.session_id for info in sessions_info[:max_sessions]} if max_sessions else set()

        pruned_sessions: list[str] = []
        kept_sessions: list[tuple[str, str]] = []
        session_errors: list[str] = []

        if sessions_dir.exists():
            for session_dir in sorted((d for d in sessions_dir.iterdir() if d.is_dir()), key=lambda p: p.name.lower()):
                session_id = session_dir.name
                info = info_by_id.get(session_id)
                if info and info.pending_paths:
                    kept_sessions.append((session_id, "unfinished"))
                    continue
                if info and session_id in keep_ids:
                    kept_sessions.append((session_id, "within retention limit"))
                    continue
                if info is None:
                    kept_sessions.append((session_id, "metadata unavailable"))
                    continue
                try:
                    shutil.rmtree(session_dir)
                    pruned_sessions.append(session_id)
                except Exception as exc:  # pylint: disable=broad-except
                    session_errors.append(f"{session_dir}: {exc}")

        remaining_session_dirs = [d for d in sessions_dir.iterdir() if d.is_dir()] if sessions_dir.exists() else []

        now = datetime.now()
        cutoff = now - timedelta(days=max_backup_age_days) if max_backup_age_days > 0 else None
        backup_removed: list[tuple[Path, int, int]] = []
        backup_kept: list[tuple[Path, str]] = []
        backup_failures: list[str] = []

        if global_backups_dir.exists():
            backup_dirs = [d for d in global_backups_dir.iterdir() if d.is_dir()]
            referenced_backups: set[Path] = set()
            if backup_dirs and remaining_session_dirs:
                backup_strings = {backup_dir: str(backup_dir) for backup_dir in backup_dirs}
                for session_dir in remaining_session_dirs:
                    blobs: list[str] = []
                    for name in ("config.yaml", "state.json", "ledger.jsonl"):
                        candidate = session_dir / name
                        if candidate.exists():
                            try:
                                blobs.append(candidate.read_text(encoding="utf-8", errors="ignore"))
                            except Exception:  # pylint: disable=broad-except
                                continue
                    if not blobs:
                        continue
                    blob = "\n".join(blobs)
                    for backup_dir, as_text in backup_strings.items():
                        if backup_dir in referenced_backups:
                            continue
                        if as_text in blob:
                            referenced_backups.add(backup_dir)
            if self._last_backup_dir:
                referenced_backups.add(self._last_backup_dir)

            for backup_dir in backup_dirs:
                if backup_dir in referenced_backups:
                    backup_kept.append((backup_dir, "referenced by session"))
                    continue
                if cutoff is not None:
                    age = now - datetime.fromtimestamp(backup_dir.stat().st_mtime)
                    if age <= timedelta(days=max_backup_age_days):
                        backup_kept.append((backup_dir, f"age {age.days}d"))
                        continue
                try:
                    files, dirs = self._count_tree_entries(backup_dir)
                    shutil.rmtree(backup_dir)
                    backup_removed.append((backup_dir, files, dirs))
                    self._prune_empty_dirs(backup_dir.parent, global_backups_dir)
                except Exception as exc:  # pylint: disable=broad-except
                    backup_failures.append(f"{backup_dir}: {exc}")

        if pruned_sessions:
            self.append_log("INFO", f"Pruned sessions: removed {len(pruned_sessions)} (kept {len(kept_sessions)})")
            for session_id in pruned_sessions:
                self.append_log("DEBUG", f"Removed session {session_id}")
        else:
            self.append_log("INFO", "Pruned sessions: none removed")
        for session_id, reason in kept_sessions:
            self.append_log("DEBUG", f"Kept session {session_id}: {reason}")
        for message in session_errors:
            self.append_log("WARN", f"Session pruning issue: {message}")

        if backup_removed:
            self.append_log("INFO", f"Pruned backups: removed {len(backup_removed)} directories older than {max_backup_age_days} days")
            for path_entry, files, dirs in backup_removed:
                self.append_log("DEBUG", f"Removed backup {path_entry} (files={files}, dirs={dirs})")
        else:
            self.append_log("INFO", "Pruned backups: none removed")
        for path_entry, reason in backup_kept:
            self.append_log("DEBUG", f"Kept backup {path_entry}: {reason}")
        for message in backup_failures:
            self.append_log("WARN", f"Backup pruning issue: {message}")

        if self._last_backup_dir and not self._last_backup_dir.exists():
            self._last_backup_dir = None
            self.set_backup_available(False)

        self._update_action_states()

    def _resolve_backup_dir(self) -> Optional[Path]:
        if self._last_backup_dir and self._last_backup_dir.exists():
            return self._last_backup_dir
        storage_paths = self._session_storage_paths()
        base = storage_paths["backups"]
        if not base.exists():
            return None
        candidates = [d for d in base.iterdir() if d.is_dir()]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.name)


    # Session discovery -------------------------------------------------
    def prompt_unfinished_on_launch(self, parent: Optional[QtWidgets.QWidget]) -> None:
        if self._launch_resume_prompt_shown:
            return
        self._launch_resume_prompt_shown = True
        sessions = self._collect_session_infos(only_unfinished=True)
        if not sessions:
            return
        info = sessions[0]
        box = QtWidgets.QMessageBox(parent or self)
        box.setWindowTitle("Resume Session")
        box.setText(f"Resume last unfinished session {info.name} ({info.session_id})?")
        resume_btn = box.addButton("Resume", QtWidgets.QMessageBox.AcceptRole)
        box.addButton("Dismiss", QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(resume_btn)
        box.exec()
        choice = "resume" if box.clickedButton() == resume_btn else "dismiss"
        self._append_session_event(info.directory, {"session": "resume_prompt", "choice": choice})
        if choice == "resume":
            self._resume_existing_session(info, source="prompt")

    def _on_session_manager(self) -> None:
        sessions = self._collect_session_infos()
        if not sessions:
            QtWidgets.QMessageBox.information(self, "Session Manager", "No sessions found for the current anchor.")
            return
        dialog = SessionManagerDialog(self, sessions)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            info = dialog.selected_session
            if info is not None:
                self._resume_existing_session(info, source="manager")

    def _collect_session_infos(self, *, only_unfinished: bool = False) -> list[SessionInfo]:
        anchor = self._resolve_anchor_root()
        if anchor is None:
            return []
        sessions_dir = self._session_storage_paths()["sessions"]
        if not sessions_dir.exists():
            return []
        sessions: list[SessionInfo] = []
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            config = self._load_session_config_data(session_dir)
            if not config:
                continue
            session_id = config.get("id") or session_dir.name
            name = config.get("name") or session_id
            ledger = self._read_session_ledger(session_dir)
            if not ledger:
                continue
            latest: Dict[str, str] = {}
            last_ts = config.get("timestamp") or ""
            for entry in ledger:
                if not isinstance(entry, dict):
                    continue
                ts = entry.get("ts") or entry.get("timestamp")
                if ts:
                    last_ts = ts
                if entry.get("type") == "file" and entry.get("path"):
                    latest[str(entry.get("path"))] = str(entry.get("status"))
            pending_paths = [path for path, status in latest.items() if status in ("pending", "processing")]
            failed_paths = [path for path, status in latest.items() if status == "failed"]
            if only_unfinished and not pending_paths:
                continue
            state_data = self._load_session_state(session_dir)
            files_state = state_data.get("files") or list(latest.keys())
            total_files = int(state_data.get("total_files") or len(files_state))
            try:
                activity_dt = datetime.fromisoformat(last_ts) if last_ts else datetime.min
            except Exception:
                activity_dt = datetime.min
            sessions.append(
                SessionInfo(
                    session_id=session_id,
                    name=name,
                    directory=session_dir,
                    pending_paths=pending_paths,
                    failed_paths=failed_paths,
                    total_files=total_files,
                    last_activity=last_ts,
                    last_activity_dt=activity_dt,
                    config=config,
                )
            )
        sessions.sort(key=lambda info: info.last_activity_dt, reverse=True)
        return sessions

    def _resolve_anchor_root(self) -> Optional[Path]:
        if self._anchor_path:
            return self._anchor_path
        rewrite_cfg = self._rewrite_settings()
        anchor_value = rewrite_cfg.get("anchor_folder")
        if not anchor_value:
            return None
        try:
            return Path(anchor_value).expanduser().resolve()
        except Exception:
            return Path(anchor_value).expanduser()


    def _append_session_event(self, session_dir: Path, entry: Dict[str, Any]) -> None:
        if not session_dir.exists():
            return
        payload = dict(entry)
        payload.setdefault("ts", datetime.now().isoformat())
        ledger_path = session_dir / "ledger.jsonl"
        with ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


    def _read_session_ledger(self, session_dir: Path) -> list[dict]:
        ledger_path = session_dir / "ledger.jsonl"
        if not ledger_path.exists():
            return []
        entries: list[dict] = []
        for raw in ledger_path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                entries.append(data)
        return entries

    def _ensure_backup_writable(self, session_dir: Path, *, dry_run: bool) -> bool:
        if dry_run:
            return True
        storage_paths = self._session_storage_paths()
        destinations = [session_dir / "backups", storage_paths["backups"]]
        token_chars = string.ascii_lowercase + string.digits
        for dest in destinations:
            try:
                dest.mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # pylint: disable=broad-except
                self._report_backup_failure(dest, exc)
                return False

            probe = dest / f".write-test-{''.join(random.choices(token_chars, k=8))}"
            try:
                probe.write_text("ok", encoding="utf-8")
            except Exception as exc:  # pylint: disable=broad-except
                self._report_backup_failure(dest, exc)
                return False
            finally:
                try:
                    probe.unlink(missing_ok=True)
                except Exception:  # pylint: disable=broad-except
                    pass
        return True

    def _report_backup_failure(self, path: Path, exc: Exception) -> None:
        self.append_log("ERROR", "cannot create backups - aborting")
        self.append_log("DEBUG", f"Backup check failed at {path}: {exc}")
        QtWidgets.QMessageBox.critical(
            self,
            "Rewrite Session",
            f"Cannot create backups at {path}.\n\n{exc}",
        )




    def _record_dry_run_manifest(self, session_dir: Path, files: list[Path]) -> None:
        rel_paths = sorted({self._relative_session_path(p) for p in files})
        if not rel_paths:
            return
        manifest_dir = session_dir / "dry-run"
        try:
            manifest_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = manifest_dir / "global_paths.json"
            payload = {"paths": rel_paths, "session_id": self._current_session_id}
            manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # pylint: disable=broad-except
            self.append_log("DEBUG", f"Failed to record dry-run manifest: {exc}")

    def _cleanup_dry_run_artifacts(self) -> None:
        if self._dry_cleanup_done:
            return
        if self._session_is_dry_run:
            self._dry_cleanup_done = True
            return
        session_dir = self._current_session_dir
        if not session_dir:
            self._dry_cleanup_done = True
            return

        local_dir = session_dir / "dry-run"
        manifest_path = local_dir / "global_paths.json"
        manifest_rel_paths: list[str] = []
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8")) or {}
                paths = data.get("paths", []) if isinstance(data, dict) else []
                manifest_rel_paths = [str(p) for p in paths if isinstance(p, str)]
            except Exception as exc:  # pylint: disable=broad-except
                self.append_log("DEBUG", f"Failed to read dry-run manifest: {exc}")

        storage_paths = self._session_storage_paths()
        global_root = storage_paths["dry_run"]

        removed_local = 0
        if local_dir.exists():
            try:
                removed_local = sum(1 for p in local_dir.rglob("*") if p.is_file())
            except Exception:  # pylint: disable=broad-except
                removed_local = 0
            try:
                shutil.rmtree(local_dir)
            except Exception as exc:  # pylint: disable=broad-except
                self.append_log("WARN", f"Dry-run cleanup issue: session dry-run {local_dir}: {exc}")
                removed_local = 0

        removed_global = 0
        if manifest_rel_paths:
            for rel in manifest_rel_paths:
                rel_path = Path(rel)
                candidates = [global_root / rel_path]
                if rel_path.suffix != ".md":
                    candidates.append((global_root / rel_path).with_suffix(".md"))
                seen: list[Path] = []
                for candidate in candidates:
                    if candidate not in seen:
                        seen.append(candidate)
                for candidate in seen:
                    try:
                        if candidate.exists():
                            if candidate.is_dir():
                                try:
                                    removed_global += sum(1 for p in candidate.rglob("*") if p.is_file())
                                except Exception:  # pylint: disable=broad-except
                                    pass
                                shutil.rmtree(candidate)
                            else:
                                candidate.unlink()
                                removed_global += 1
                            self._prune_empty_dirs(candidate.parent, global_root)
                            break
                    except Exception as exc:  # pylint: disable=broad-except
                        self.append_log("WARN", f"Dry-run cleanup issue: global dry-run {candidate}: {exc}")
                        break
        elif self._current_session_id:
            candidate_dir = global_root / self._current_session_id
            if candidate_dir.exists():
                try:
                    removed_global = sum(1 for p in candidate_dir.rglob("*") if p.is_file())
                except Exception:  # pylint: disable=broad-except
                    removed_global = 0
                try:
                    shutil.rmtree(candidate_dir)
                except Exception as exc:  # pylint: disable=broad-except
                    self.append_log("WARN", f"Dry-run cleanup issue: global dry-run {candidate_dir}: {exc}")
                    removed_global = 0

        summary = f"Dry-run cleanup: local={removed_local} from {local_dir}"
        summary += f"; global={removed_global} under {global_root}"
        self.append_log("INFO", summary)
        self._dry_cleanup_done = True

    def _retention_policy(self) -> dict[str, int]:
        defaults = {"max_sessions": 10, "max_backup_age_days": 90}
        rewrite_cfg = self._rewrite_settings()
        policy_cfg = rewrite_cfg.get("retention") or {}

        max_sessions = self._coerce_int(policy_cfg.get("max_sessions"), defaults["max_sessions"])
        max_backup_age_days = self._coerce_int(policy_cfg.get("max_backup_age_days"), defaults["max_backup_age_days"])

        return {
            "max_sessions": max(0, max_sessions),
            "max_backup_age_days": max(0, max_backup_age_days),
        }

    def _count_tree_entries(self, root: Path) -> tuple[int, int]:
        files = 0
        dirs = 0

        for _current, dirnames, filenames in os.walk(root):
            files += len(filenames)
            dirs += len(dirnames)
        return files, dirs

    def _prune_empty_dirs(self, start: Path, root: Path) -> None:
        try:
            root_resolved = root.resolve()
        except Exception:  # pylint: disable=broad-except
            root_resolved = root
        current = start
        while True:
            try:
                current_resolved = current.resolve()
            except Exception:  # pylint: disable=broad-except
                current_resolved = current
            if current_resolved == root_resolved:
                break
            if not current.exists():
                parent = current.parent
                if parent == current:
                    break
                current = parent
                continue
            try:
                entries = list(current.iterdir())
            except Exception:  # pylint: disable=broad-except
                break
            if entries:
                break
            try:
                current.rmdir()
            except Exception:  # pylint: disable=broad-except
                break
            parent = current.parent
            if parent == current:
                break
            current = parent

    def _load_session_state(self, session_dir: Path) -> Dict[str, Any]:
        state_path = session_dir / "state.json"
        if not state_path.exists():
            return {}
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}


    def _resume_existing_session(self, info: SessionInfo, *, source: str) -> None:
        if self._session_controller and self._session_state == "running":
            QtWidgets.QMessageBox.warning(
                self, "Resume Session", "A session is already running. Stop it before resuming another session."
            )
            return
        session_dir = info.directory
        if not session_dir.exists():
            QtWidgets.QMessageBox.warning(self, "Resume Session", "Session directory is missing.")
            return
        config = dict(info.config or {})
        anchor_value = config.get("anchor") or config.get("anchor_folder")
        if anchor_value:
            try:
                anchor_path = Path(anchor_value).expanduser().resolve()
            except Exception:
                anchor_path = Path(anchor_value).expanduser()
            self._anchor_path = anchor_path
            self._update_anchor_display()
        self._selected_template_name = config.get("template_name")
        self._load_templates(initial=False, force_refresh=True)
        template_path = config.get("template")
        if template_path:
            candidate = Path(template_path)
            if candidate.exists():
                self._selected_template_path = candidate
        self.chk_dry_run.blockSignals(True)
        self.chk_dry_run.setChecked(bool(config.get("dry_run", True)))
        self.chk_dry_run.blockSignals(False)
        self.chk_include_subfolders.blockSignals(True)
        self.chk_include_subfolders.setChecked(bool(config.get("include_subfolders", True)))
        self.chk_include_subfolders.blockSignals(False)
        self.chk_retry_failed.blockSignals(True)
        retry_failed = bool(config.get("retry_failed_on_resume", False))
        self.chk_retry_failed.setChecked(retry_failed)
        self.chk_retry_failed.blockSignals(False)
        self._session_retry_failed = retry_failed
        self.session_name_edit.setText(config.get("name") or info.session_id)
        self._current_session_id = info.session_id
        self._current_session_dir = session_dir
        self._current_session_config = config
        self._current_huge_policy = self._normalize_huge_policy(config.get("huge_file_policy"))
        self._update_action_states()

        state_data = self._load_session_state(session_dir)
        self._session_total_files = int(state_data.get("total_files") or info.total_files or 0)
        self._session_last_completed = state_data.get("last_completed")

        dry_run = bool(config.get("dry_run", True))
        self._session_is_dry_run = dry_run
        self._dry_cleanup_done = False
        config["dry_run"] = dry_run
        self._write_session_config_data(session_dir, config)
        if not self._ensure_backup_writable(session_dir, dry_run=dry_run):
            self._session_state = "paused"
            self._session_next_pending = None
            self.lbl_progress.setText(f"Paused — backups unavailable")
            self._update_session_controls()
            return

        rewrite_defaults = self._rewrite_settings()
        max_retries = self._coerce_int(config.get("max_retries"), self._coerce_int(rewrite_defaults.get("max_retries"), 2))
        timeout_seconds = self._coerce_int(
            config.get("llm_timeout_seconds"),
            self._coerce_int(rewrite_defaults.get("llm_timeout_seconds"), 180),
        )
        backoff_seconds = self._coerce_float(
            config.get("retry_backoff_base_seconds"),
            self._coerce_float(rewrite_defaults.get("retry_backoff_base_seconds"), 1.0),
        )

        controller = RewriteSessionController(
            session_dir=session_dir,
            anchor_path=self._anchor_path or session_dir.parent.parent,
            process_file=self._build_process_file(session_dir),
            retry_failed_on_resume=retry_failed,
            log_callback=self._session_log_callback,
            state_callback=self._session_state_callback,
            progress_callback=self._session_progress_callback,
            file_finished_callback=self._session_file_done_callback,
            huge_policy=config.get("huge_file_policy"),
            huge_file_decider=self._huge_file_decider,
            huge_policy_updater=self._make_huge_policy_updater(session_dir),
            huge_context_limit=20,
            max_retries=max_retries,
            llm_timeout_seconds=timeout_seconds,
            retry_backoff_base_seconds=backoff_seconds,
        )
        self._session_controller = controller
        next_rel = controller.next_file() or (
            info.pending_paths[0]
            if info.pending_paths
            else (info.failed_paths[0] if info.failed_paths and retry_failed else None)
        )
        if not next_rel:
            QtWidgets.QMessageBox.information(self, "Resume Session", "No pending files remain in this session.")
            self._session_state = "paused"
            self._update_session_controls()
            return

        self._append_session_event(session_dir, {"session": "resume", "source": source})

        self._session_next_pending = next_rel
        self._session_state = "running"
        self.lbl_progress.setText(f"Resuming run — next: {Path(next_rel).name}")
        self._update_session_controls()
        self.append_log("INFO", f"Resuming session {info.session_id} from {session_dir}")
        try:
            controller.continue_run()
        except RuntimeError as exc:
            self.append_log("ERROR", f"Failed to resume session: {exc}")
            self._session_state = "paused"
            self._update_session_controls()

    # Utility -----------------------------------------------------------
    @staticmethod
    def _coerce_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_float(value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _persist_rewrite_value(self, key: str, value) -> None:
        if self._config_ref is None:
            return
        rewrite_cfg = self._config_ref.setdefault("rewrite", {})
        rewrite_cfg[key] = value
        if self._config_path:
            save_app_config(self._config_path, self._config_ref)

    def _update_action_states(self) -> None:
        anchor_ok = self._anchor_path is not None
        template_ok = self._selected_template_path is not None
        file_ok = self._selected_file is not None

        can_run_common = anchor_ok and template_ok
        self.btn_rewrite_folder.setEnabled(can_run_common)
        self.btn_rewrite_file.setEnabled(can_run_common and file_ok)
        self.btn_preview.setEnabled(can_run_common and file_ok)
        self.btn_select_file.setEnabled(anchor_ok)
        self.btn_delete_backups.setEnabled(bool(self._vault_path or self._anchor_path))
        self.btn_prune_sessions.setEnabled(bool(self._vault_path or self._anchor_path))
        self.btn_delete_backups.setEnabled(bool(self._vault_path or self._anchor_path))

    def run_ai_rewrite(self, payload: dict) -> None:
        # Legacy placeholder for compatibility.
        self.append_log("DEBUG", f"runAiRewrite payload queued: {payload}")


































































class SessionManagerDialog(QtWidgets.QDialog):
    def __init__(self, parent: Optional[QtWidgets.QWidget], sessions: list[SessionInfo]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Session Manager")
        self.resize(420, 320)
        self._selected: SessionInfo | None = None

        layout = QtWidgets.QVBoxLayout(self)
        self.list_widget = QtWidgets.QListWidget(self)
        for info in sessions:
            pending = len(info.pending_paths)
            failed = len(info.failed_paths)
            total = info.total_files
            label = f"{info.name} ({info.session_id}) — pending {pending}/{total}"
            if failed:
                label += f", failed {failed}"
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, info)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        self.btn_resume = QtWidgets.QPushButton("Resume")
        self.btn_close = QtWidgets.QPushButton("Close")
        button_row.addWidget(self.btn_resume)
        button_row.addWidget(self.btn_close)
        layout.addLayout(button_row)

        self.btn_resume.setEnabled(False)
        self.list_widget.itemSelectionChanged.connect(self._update_button_state)
        self.list_widget.itemDoubleClicked.connect(self._accept_current)
        self.btn_resume.clicked.connect(self._accept_current)
        self.btn_close.clicked.connect(self.reject)
        self._update_button_state()

    @property
    def selected_session(self) -> SessionInfo | None:
        return self._selected

    def _current_info(self) -> SessionInfo | None:
        item = self.list_widget.currentItem()
        return item.data(QtCore.Qt.UserRole) if item else None

    def _update_button_state(self) -> None:
        info = self._current_info()
        can_resume = bool(info and info.pending_paths)
        self.btn_resume.setEnabled(can_resume)

    def _accept_current(self) -> None:
        info = self._current_info()
        if not info:
            return
        can_resume = bool(info.pending_paths)
        if not can_resume:
            return
        self._selected = info
        self.accept()


















