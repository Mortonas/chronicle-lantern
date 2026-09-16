from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from PySide6 import QtCore

from core.file_discovery import find_markdown_files
from core.llm_provider import LlmProvider
from core.markdown_rewrite import (
    RE_IMG_MARKDOWN,
    RE_IMG_OBSIDIAN,
    apply_image_preserving_rewrite,
    extract_non_image_text,
)


class RewriteSignals(QtCore.QObject):
    progress = QtCore.Signal(int, int)
    file_done = QtCore.Signal(str, int, int, int)
    warning = QtCore.Signal(str)
    error = QtCore.Signal(str)
    finished = QtCore.Signal(dict)


@dataclass
class RewriteConfig:
    mode: str
    path: Path
    dry_run: bool
    recurse: bool
    config: Dict


class _SharedState:
    def __init__(self, total: int, signals: RewriteSignals) -> None:
        self.total = total
        self.signals = signals
        self.lock = threading.Lock()
        self.processed = 0
        self.backup_created = False

    def mark_done(self, backup: bool) -> None:
        with self.lock:
            self.processed += 1
            if backup:
                self.backup_created = True
            current = self.processed
        self.signals.progress.emit(current, self.total)


class _RewriteFileTask(QtCore.QRunnable):
    def __init__(
        self,
        *,
        file_path: Path,
        cfg: RewriteConfig,
        max_bytes: int,
        cancel_event: threading.Event,
        shared: _SharedState,
        signals: RewriteSignals,
        backup_root: Path,
        dry_run_root: Path,
    ) -> None:
        super().__init__()
        self.file_path = file_path
        self.cfg = cfg
        self.max_bytes = max_bytes
        self.cancel_event = cancel_event
        self.shared = shared
        self.signals = signals
        self.backup_root = backup_root
        self.dry_run_root = dry_run_root
        self.vault_path = Path(cfg.config.get("vault_path", ".")).expanduser().resolve()

    def run(self) -> None:  # type: ignore[override]
        if self.cancel_event.is_set():
            return
        path = self.file_path
        try:
            if not path.exists():
                self._warn(f"[WARN] missing file: {path}")
                self.shared.mark_done(False)
                return

            size = path.stat().st_size
            if size > self.max_bytes:
                self._warn(f"[WARN] skipped large file: {path}")
                self.shared.mark_done(False)
                return

            text = path.read_text(encoding="utf-8", errors="replace")
            if "\x00" in text:
                self._warn(f"[WARN] skipped binary file: {path}")
                self.shared.mark_done(False)
                return

            if self.cancel_event.is_set():
                return

            provider = LlmProvider(self.cfg.config)
            existing = extract_non_image_text(text)
            llm_output = provider.generate_markdown(
                filename=path.name,
                path=str(path),
                existing_non_image_text=existing,
            )

            if self.cancel_event.is_set():
                return

            new_content = apply_image_preserving_rewrite(text, llm_output)
            images_preserved = self._count_images(text)
            relative = self._relative_to_vault(path)
            backup_flag = False

            if self.cfg.dry_run:
                destination = (self.dry_run_root / relative).with_suffix(".md")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(new_content, encoding="utf-8")
            else:
                backup_path = (self.backup_root / relative).with_suffix(".bak.md")
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_path.write_text(text, encoding="utf-8")
                path.write_text(new_content, encoding="utf-8")
                backup_flag = True

            self.signals.file_done.emit(
                str(path),
                images_preserved,
                len(text),
                len(new_content),
            )
            self.shared.mark_done(backup_flag)
        except Exception as exc:  # pylint: disable=broad-except
            self.signals.error.emit(f"{path}: {exc}")
            self.shared.mark_done(False)

    def _count_images(self, text: str) -> int:
        return sum(1 for line in text.splitlines() if RE_IMG_OBSIDIAN.match(line) or RE_IMG_MARKDOWN.match(line))

    def _relative_to_vault(self, path: Path) -> Path:
        try:
            return path.resolve().relative_to(self.vault_path)
        except Exception:  # pylint: disable=broad-except
            return Path(path.name)

    def _warn(self, message: str) -> None:
        self.signals.warning.emit(message)


class RewriteWorker(QtCore.QRunnable):
    def __init__(self, cfg: RewriteConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.signals = RewriteSignals()
        self._cancel = threading.Event()
        self._vault_path = Path(cfg.config.get("vault_path", ".")).expanduser().resolve()
        rewrite_settings = cfg.config.get("rewrite", {}) or {}
        self._max_bytes = int(rewrite_settings.get("max_bytes", 1_000_000))
        self._concurrency = max(1, int(rewrite_settings.get("concurrency", 3)))
        self._timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._backup_root = self._vault_path / ".ai-rewrite" / "backups" / self._timestamp
        self._dry_run_root = self._vault_path / ".ai-rewrite" / "dry-run"

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # type: ignore[override]
        try:
            files = self._gather_files()
            total = len(files)
            if not files:
                self.signals.warning.emit("No markdown files discovered for rewrite")
                self.signals.finished.emit(
                    {"processed": 0, "total": 0, "dry_run": self.cfg.dry_run, "cancelled": False}
                )
                return

            self.signals.progress.emit(0, total)
            shared = _SharedState(total, self.signals)

            pool = QtCore.QThreadPool()
            pool.setMaxThreadCount(self._concurrency)

            for file_path in files:
                if self._cancel.is_set():
                    break
                task = _RewriteFileTask(
                    file_path=file_path,
                    cfg=self.cfg,
                    max_bytes=self._max_bytes,
                    cancel_event=self._cancel,
                    shared=shared,
                    signals=self.signals,
                    backup_root=self._backup_root,
                    dry_run_root=self._dry_run_root,
                )
                pool.start(task)

            pool.waitForDone()

            summary = {
                "processed": shared.processed,
                "total": total,
                "dry_run": self.cfg.dry_run,
                "backup_dir": str(self._backup_root) if (shared.backup_created and not self.cfg.dry_run) else None,
                "cancelled": self._cancel.is_set(),
            }
            if self._cancel.is_set():
                self.signals.warning.emit("[WARN] Rewrite cancelled before completion")
            self.signals.finished.emit(summary)
        except Exception as exc:  # pylint: disable=broad-except
            self.signals.error.emit(str(exc))
            self.signals.finished.emit(
                {
                    "processed": 0,
                    "total": 0,
                    "dry_run": self.cfg.dry_run,
                    "backup_dir": None,
                    "cancelled": self._cancel.is_set(),
                }
            )

    def _gather_files(self) -> List[Path]:
        mode = self.cfg.mode
        target = self.cfg.path
        if mode == "file":
            if target.is_file():
                return [target.resolve()]
            self.signals.warning.emit(f"Selected file does not exist: {target}")
            return []
        if mode == "folder":
            return [p.resolve() for p in find_markdown_files(target, self.cfg.recurse)]
        raise ValueError(f"Unknown mode: {mode}")


__all__ = ["RewriteWorker", "RewriteSignals", "RewriteConfig"]







