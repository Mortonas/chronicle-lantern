from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import yaml

from core.markdown_rewrite import extract_non_image_text

FileStatus = str
SessionState = str
HugeDecision = str


TERMINAL_STATUSES: set[FileStatus] = {"done", "skipped", "failed"}
VALID_STATUSES: set[FileStatus] = {"pending", "processing"} | TERMINAL_STATUSES
HUGE_CONTEXT_LIMIT_DEFAULT = 20


class RewriteSessionController:
    """Orchestrates a rewrite session: queue, ledger, and pause/resume state."""

    def __init__(
        self,
        *,
        session_dir: Path,
        anchor_path: Path,
        retry_failed_on_resume: bool,
        process_file: Optional[Callable[[Path], FileStatus]] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
        state_callback: Optional[Callable[[SessionState], None]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        file_finished_callback: Optional[Callable[[str, FileStatus], None]] = None,
        huge_policy: Optional[Dict[str, Any]] = None,
        huge_file_decider: Optional[Callable[[str, Path, int, Dict[str, Any]], HugeDecision]] = None,
        huge_policy_updater: Optional[Callable[[Dict[str, Any]], None]] = None,
        huge_context_limit: int = HUGE_CONTEXT_LIMIT_DEFAULT,
        max_retries: int = 2,
        llm_timeout_seconds: int = 180,
        retry_backoff_base_seconds: float = 1.0,
    ) -> None:
        self.session_dir = session_dir
        self.anchor_path = anchor_path
        self.retry_failed_on_resume = retry_failed_on_resume

        self._process_file = process_file or self._default_process_file
        self._log_cb = log_callback or (lambda level, message: None)
        self._state_cb = state_callback or (lambda state: None)
        self._progress_cb = progress_callback or (lambda index, total, path: None)
        self._file_done_cb = file_finished_callback or (lambda rel_path, status: None)

        self.ledger_path = self.session_dir / "ledger.jsonl"
        self.state_path = self.session_dir / "state.json"

        self._huge_policy: Dict[str, Any] = self._normalize_huge_policy(huge_policy)
        self._huge_callback = huge_file_decider
        self._huge_policy_updater = huge_policy_updater or (lambda policy: None)
        self._huge_limit = max(0, int(huge_context_limit))

        self._max_retries = max(0, int(max_retries))
        self._llm_timeout = max(1, int(llm_timeout_seconds))
        self._retry_backoff_base = max(0.1, float(retry_backoff_base_seconds))

        self._lock = threading.Lock()
        self._ledger_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_requested = False
        self._state: Dict[str, object] = {}

        self._load_or_initialize_state()

    # ------------------------------------------------------------------
    def initialize_worklist(self, files: Iterable[Path]) -> None:
        rel_paths = [self._relativize(p) for p in files]
        rel_paths = sorted(dict.fromkeys(rel_paths), key=lambda s: s.lower())
        if not rel_paths:
            return

        entries = self._read_ledger()
        known_paths = {entry["path"] for entry in entries if entry.get("type") == "file"}
        timestamp = datetime.now().isoformat()
        for rel in rel_paths:
            if rel not in known_paths:
                self._append_ledger(
                    {
                        "type": "file",
                        "path": rel,
                        "status": "pending",
                        "ts": timestamp,
                    }
                )

        if not self._state.get("files"):
            self._state["files"] = rel_paths
            self._state["total_files"] = len(rel_paths)
            self._state["processed"] = 0
            self._state["last_completed"] = None
            self._state["next_file"] = rel_paths[0]
            self._state["state"] = self._state.get("state", "paused")
            self._state["retry_failed_on_resume"] = self.retry_failed_on_resume
            self._write_state()
        else:
            merged = dict.fromkeys(self._state.get("files", []) + rel_paths)
            merged_list = sorted(merged.keys(), key=lambda s: s.lower())
            self._state["files"] = merged_list
            self._state["total_files"] = len(merged_list)
            if not self._state.get("next_file"):
                self._state["next_file"] = merged_list[0] if merged_list else None
            self._state["retry_failed_on_resume"] = self.retry_failed_on_resume
            self._write_state()


    def _rebuild_worklist(self, *, include_failed: bool) -> list[str]:
        entries = self._read_ledger()
        latest: Dict[str, str] = {}
        for entry in entries:
            if entry.get("type") == "file" and entry.get("path"):
                latest[str(entry.get("path"))] = str(entry.get("status") or "")

        files: list[str] = list(self._state.get("files") or [])
        if not files:
            files = sorted(latest.keys(), key=lambda p: p.lower())
        else:
            for rel_path in sorted(latest.keys(), key=lambda p: p.lower()):
                if rel_path not in files:
                    files.append(rel_path)

        self._state["files"] = files
        self._state["total_files"] = len(files)

        worklist: list[str] = []
        for rel_path in files:
            status = latest.get(rel_path, "pending").lower()
            if status in {"pending", "processing"}:
                worklist.append(rel_path)
            elif status == "failed" and include_failed:
                worklist.append(rel_path)

        self._state["retry_failed_on_resume"] = include_failed
        self._state["next_file"] = worklist[0] if worklist else None
        self._write_state()
        return worklist

    def start(self) -> None:
        worklist = self._rebuild_worklist(include_failed=self.retry_failed_on_resume)
        if not worklist:
            self._set_state("completed")
            return
        self._start_run(worklist)

    def continue_run(self) -> None:
        worklist = self._rebuild_worklist(include_failed=self.retry_failed_on_resume)
        if not worklist:
            self._set_state("completed")
            return
        self._append_ledger({"session": "continue", "ts": datetime.now().isoformat()})
        self._start_run(worklist)

    def request_stop(self) -> None:
        with self._lock:
            self._stop_requested = True
            self._state["stop_requested"] = True
            self._write_state()

    # ------------------------------------------------------------------
    def current_state(self) -> SessionState:
        return str(self._state.get("state", "paused"))

    def last_completed_file(self) -> Optional[str]:
        last = self._state.get("last_completed")
        return str(last) if last else None

    def next_file(self) -> Optional[str]:
        nxt = self._state.get("next_file")
        return str(nxt) if nxt else None

    def tail_ledger(self, lines: int = 5) -> List[Dict[str, object]]:
        entries = self._read_ledger()
        return entries[-lines:]

    def wait_until_idle(self, timeout: Optional[float] = None) -> None:
        thread: Optional[threading.Thread]
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # Internal ---------------------------------------------------------
    def _start_run(self, worklist: List[str]) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("Session run already in progress")
            self._stop_requested = False
            self._state["stop_requested"] = False
            self._state["next_file"] = worklist[0] if worklist else None
            self._set_state("running")
            self._thread = threading.Thread(
                target=self._run_worklist,
                args=(worklist,),
                name=f"RewriteSession:{self.session_dir.name}",
                daemon=True,
            )
            self._thread.start()

    def _run_worklist(self, worklist: List[str]) -> None:
        total = int(self._state.get("total_files") or 0)
        processed_this_run = 0
        latest_status = self._latest_file_statuses()
        recovered_paths = self._identify_recovered_paths(worklist, latest_status)
        for rel_path in worklist:
            with self._lock:
                if self._stop_requested and processed_this_run > 0:
                    break
            status_lower = latest_status.get(rel_path, "").lower()
            if status_lower in {"done", "skipped"}:
                self._log_cb("DEBUG", f"resume-skip (already terminal): {rel_path}")
                self._state["next_file"] = self._peek_next(rel_path)
                self._state["current_file"] = None
                self._write_state()
                continue
            abs_path = (self.anchor_path / rel_path).resolve()

            decision, context_len, triggered = self._evaluate_huge_policy(rel_path, abs_path)
            if decision == "cancel" and triggered:
                self._log_cb("SELECT", f"Huge file cancel requested: {rel_path}")
                self._set_huge_policy({"asked": False, "mode": None})
                with self._lock:
                    self._stop_requested = True
                    self._state["stop_requested"] = True
                    self._state["next_file"] = rel_path
                    self._write_state()
                break
            if decision == "skip_one" and triggered:
                self._set_huge_policy({"asked": False, "mode": None})
                self._log_cb("INFO", f"Huge file skipped ({context_len} chars): {rel_path}")
                timestamp = datetime.now().isoformat()
                self._append_ledger(
                    {
                        "type": "file",
                        "path": rel_path,
                        "status": "skipped",
                        "reason": "huge-file",
                        "retries": 0,
                        "ts": timestamp,
                    }
                )
                latest_status[rel_path] = "skipped"
                processed = int(self._state.get("processed", 0)) + 1
                self._state["processed"] = processed
                self._state["last_completed"] = rel_path
                self._state["next_file"] = self._peek_next(rel_path)
                self._state["current_file"] = None
                self._write_state()
                self._file_done_cb(rel_path, "skipped")
                processed_this_run += 1
                continue

            if rel_path in recovered_paths:
                self._log_cb("WARN", f"Recovered interrupted file: {rel_path}")
                self._append_retry_entry(rel_path, 0, "recovered", "Recovered after interruption")
                recovered_paths.discard(rel_path)

            position = self._index_of(rel_path)
            failures = 0
            final_status: FileStatus | None = None
            final_reason: str | None = None
            final_message: str | None = None

            while True:
                attempt_index = failures + 1
                timestamp = datetime.now().isoformat()
                self._append_ledger(
                    {
                        "type": "file",
                        "path": rel_path,
                        "status": "processing",
                        "attempt": attempt_index,
                        "ts": timestamp,
                    }
                )
                latest_status[rel_path] = "processing"
                self._state["current_file"] = rel_path
                self._state["next_file"] = self._peek_next(rel_path)
                self._write_state()
                self._progress_cb(position, total, str(abs_path))

                status, info = self._call_process_with_timeout(abs_path)
                if status and status != "failed":
                    final_status = status
                    break

                reason_label = (info or {}).get("reason", "error")
                message = (info or {}).get("message", "model error")
                if status == "failed" and not info:
                    message = "model reported failure"

                failures += 1
                if failures <= self._max_retries:
                    self._log_cb("WARN", f"retry {failures}/{self._max_retries}: {message}")
                    self._append_retry_entry(rel_path, failures, reason_label, message)
                    self._backoff_sleep(failures)
                    continue

                final_status = "failed"
                final_reason = reason_label
                final_message = message
                self._log_cb("ERROR", f"File failed after {self._max_retries} retries: {rel_path} ({message})")
                break

            retries_used = min(failures, self._max_retries)
            final_entry = {
                "type": "file",
                "path": rel_path,
                "status": final_status,
                "ts": datetime.now().isoformat(),
                "retries": retries_used,
            }
            if final_status == "failed":
                final_entry["reason"] = final_reason or "error"
                if final_message:
                    final_entry["message"] = final_message
            self._append_ledger(final_entry)
            latest_status[rel_path] = str(final_entry.get("status") or "failed")

            if final_status in TERMINAL_STATUSES:
                processed = int(self._state.get("processed", 0)) + 1
                self._state["processed"] = processed
            self._state["last_completed"] = rel_path
            self._state["current_file"] = None
            self._state["next_file"] = self._peek_next(rel_path)
            self._write_state()
            self._file_done_cb(rel_path, final_status or "failed")
            processed_this_run += 1

            if decision in {"proceed_one", "cancel"} and triggered:
                self._set_huge_policy({"asked": False, "mode": None})

            with self._lock:
                if self._stop_requested:
                    break

        with self._lock:
            stop_requested = self._stop_requested
            self._stop_requested = False
            self._state["stop_requested"] = False

        if stop_requested:
            self._append_ledger({"session": "pause", "ts": datetime.now().isoformat(), "reason": "user"})
            self._state["state"] = "paused"
            self._state["stop_requested"] = False
            self._write_state()
            self._log_cb("SELECT", "session paused")
            with self._lock:
                self._thread = None
            self._state_cb("paused")
            return

        remaining = self._rebuild_worklist(include_failed=self.retry_failed_on_resume)
        if remaining:
            self._state["next_file"] = remaining[0]
            self._state["state"] = "paused"
        else:
            self._state["next_file"] = None
            self._state["state"] = "completed"
        self._write_state()
        with self._lock:
            self._thread = None
        self._state_cb(self.current_state())


    def _peek_next(self, current: str) -> Optional[str]:
        entries = self._read_ledger()
        latest: Dict[str, str] = {}
        for entry in entries:
            if entry.get("type") == "file" and entry.get("path"):
                latest[str(entry.get("path"))] = str(entry.get("status") or "")

        files: list[str] = list(self._state.get("files") or [])
        for rel_path in sorted(latest.keys(), key=lambda p: p.lower()):
            if rel_path not in files:
                files.append(rel_path)
        self._state["files"] = files
        self._state["total_files"] = len(files)

        try:
            start_index = files.index(current) + 1
        except ValueError:
            start_index = 0
        for rel_path in files[start_index:]:
            status = latest.get(rel_path, "pending").lower()
            if status in {"pending", "processing"}:
                return rel_path
            if self.retry_failed_on_resume and status == "failed":
                return rel_path
        return None

    def _index_of(self, rel_path: str) -> int:
        files: list[str] = list(self._state.get("files") or [])
        if rel_path not in files:
            files.append(rel_path)
            self._state["files"] = files
            self._state["total_files"] = len(files)
        try:
            return files.index(rel_path) + 1
        except ValueError:
            return 1

    def _append_retry_entry(self, rel_path: str, retry_count: int, reason: str, message: str) -> None:
        entry = {
            "type": "retry",
            "path": rel_path,
            "retry": int(retry_count),
            "reason": reason,
            "message": message,
            "ts": datetime.now().isoformat(),
        }
        self._append_ledger(entry)

    def _call_process_with_timeout(self, path: Path) -> tuple[FileStatus | None, Dict[str, Any] | None]:
        try:
            result = self._process_file(path)
        except Exception as exc:  # pylint: disable=broad-except
            self._log_cb("ERROR", f"Processing error for {path}: {exc}")
            return "failed", {"reason": "error", "message": str(exc)}

        info: Dict[str, Any] | None = None
        status: FileStatus | None
        if isinstance(result, tuple):
            status, info_value = result
            if isinstance(info_value, dict):
                info = info_value
            elif info_value is not None:
                info = {"message": str(info_value)}
        else:
            status = result

        status_str = str(status) if status is not None else None
        if status_str is None:
            return "failed", {"reason": "error", "message": "no status returned"}
        return status_str, info

    def _backoff_sleep(self, retry_count: int) -> None:
        delay = self._retry_backoff_base * max(retry_count, 1)
        time.sleep(min(delay, 0.25))

    def _evaluate_huge_policy(self, rel_path: str, abs_path: Path) -> tuple[HugeDecision, int, bool]:
        if self._huge_limit <= 0:
            return "proceed", 0, False
        context_len = self._measure_context(abs_path)
        if context_len is None or context_len <= self._huge_limit:
            return "proceed", context_len or 0, False

        mode = str(self._huge_policy.get("mode") or "")
        asked = bool(self._huge_policy.get("asked"))
        if asked and mode == "proceed_all":
            return mode, context_len, True

        decision: HugeDecision = "proceed"
        if self._huge_callback:
            snapshot = dict(self._huge_policy)
            try:
                decision = self._huge_callback(rel_path, abs_path, context_len, snapshot) or "proceed"
            except Exception as exc:  # pylint: disable=broad-except
                self._log_cb("ERROR", f"Huge file prompt failed for {abs_path}: {exc}")
                decision = "proceed"
        if decision == "proceed_all":
            self._set_huge_policy({"asked": True, "mode": "proceed_all"})
        else:
            self._set_huge_policy({"asked": False, "mode": None})
        return decision, context_len, True

    def _measure_context(self, path: Path) -> Optional[int]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # pylint: disable=broad-except
            return None
        try:
            context = extract_non_image_text(text)
        except Exception:  # pylint: disable=broad-except
            return len(text)
        return len(context)

    def _set_huge_policy(self, updates: Dict[str, Any]) -> None:
        changed = False
        for key, value in updates.items():
            if self._huge_policy.get(key) != value:
                self._huge_policy[key] = value
                changed = True
        if changed:
            self._huge_policy_updater(dict(self._huge_policy))


    def _normalize_huge_policy(self, policy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(policy, dict):
            return {"asked": False, "mode": None}
        return {"asked": bool(policy.get("asked", False)), "mode": policy.get("mode")}

    def compute_resume_plan(self) -> tuple[list[str], dict]:
        config = self._load_session_config()
        retry_failed = bool(config.get("retry_failed_on_resume", False))
        huge_policy = self._normalize_huge_policy(config.get("huge_file_policy"))

        latest: Dict[str, str] = {}
        for entry in self._read_ledger():
            if isinstance(entry, dict) and entry.get("type") == "file" and entry.get("path"):
                path_key = str(entry.get("path"))
                latest[path_key] = str(entry.get("status") or "")

        eligible: set[str] = set()
        recovered: set[str] = set()
        for rel_path, status in latest.items():
            lowered = status.lower()
            if lowered == "processing":
                eligible.add(rel_path)
                recovered.add(rel_path)
            elif lowered == "pending":
                eligible.add(rel_path)
            elif lowered == "failed" and retry_failed:
                eligible.add(rel_path)

        eligible_list = sorted(eligible, key=lambda p: p.lower())
        resume_snapshot = {
            "template": config.get("template"),
            "template_name": config.get("template_name"),
            "dry_run": bool(config.get("dry_run", True)),
            "include_subfolders": bool(config.get("include_subfolders", True)),
            "retry_failed_on_resume": retry_failed,
            "huge_file_policy": huge_policy,
            "max_retries": self._coerce_int(config.get("max_retries"), self._max_retries),
            "llm_timeout_seconds": self._coerce_int(config.get("llm_timeout_seconds"), self._llm_timeout),
            "retry_backoff_base_seconds": self._coerce_float(config.get("retry_backoff_base_seconds"), self._retry_backoff_base),
            "recovered_paths": sorted(recovered, key=lambda p: p.lower()),
        }
        return eligible_list, resume_snapshot

    def _latest_file_statuses(self) -> Dict[str, str]:
        latest: Dict[str, str] = {}
        for entry in self._read_ledger():
            if entry.get("type") == "file" and entry.get("path"):
                latest[str(entry.get("path"))] = str(entry.get("status") or "")
        return latest

    def _identify_recovered_paths(self, worklist: List[str], latest: Optional[Dict[str, str]] = None) -> set[str]:
        latest_map = latest if latest is not None else self._latest_file_statuses()
        return {path for path in worklist if latest_map.get(path, "").lower() == "processing"}

    def _load_session_config(self) -> Dict[str, Any]:
        config_path = self.session_dir / "config.yaml"
        if not config_path.exists():
            return {}
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:  # pylint: disable=broad-except
            return {}
        return data if isinstance(data, dict) else {}

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

    def _load_or_initialize_state(self) -> None:
        if self.state_path.exists():
            try:
                self._state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._state = {}
        if not self._state:
            self._state = {
                "state": "paused",
                "stop_requested": False,
                "files": [],
                "total_files": 0,
                "processed": 0,
                "last_completed": None,
                "next_file": None,
                "retry_failed_on_resume": self.retry_failed_on_resume,
            }
            self._write_state()

    def _set_state(self, state: SessionState) -> None:
        self._state["state"] = state
        self._write_state()
        self._state_cb(state)

    def _write_state(self) -> None:
        self.state_path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_ledger(self) -> List[Dict[str, object]]:
        if not self.ledger_path.exists():
            return []
        lines = []
        for raw in self.ledger_path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return lines

    def _append_ledger(self, entry: Dict[str, object]) -> None:
        with self._ledger_lock:
            with self.ledger_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _relativize(self, path: Path) -> str:
        try:
            rel = path.resolve().relative_to(self.anchor_path.resolve())
            return rel.as_posix()
        except Exception:  # pylint: disable=broad-except
            return path.name

    def _default_process_file(self, path: Path) -> FileStatus:
        self._log_cb("DEBUG", f"Simulated processing for {path}")
        return "done"


__all__ = ["RewriteSessionController", "TERMINAL_STATUSES", "VALID_STATUSES"]







