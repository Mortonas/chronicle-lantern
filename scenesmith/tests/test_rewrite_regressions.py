from __future__ import annotations

import json
import threading
from collections import defaultdict
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rewrite_session import RewriteSessionController
from ui.ai_rewrite_tab import AiRewriteTab
from PySide6 import QtWidgets


def _run_controller_flow(tmp_path: Path) -> dict:
    anchor = tmp_path / "vault"
    anchor.mkdir()
    files_payload = {
        "alpha.md": "alpha body\n",
        "beta.md": "B" * 64,
        "gamma.md": "G" * 96,
        "delta.md": "Standalone dry run file."
    }
    for name, content in files_payload.items():
        (anchor / name).write_text(content, encoding="utf-8")

    session_dir = anchor / ".ai-rewrite" / "sessions" / "report-folder"
    session_dir.mkdir(parents=True)

    log_messages: list[tuple[str, str]] = []
    state_transitions: list[str] = []
    progress_events: list[tuple[int, int, str]] = []
    file_done_events: list[tuple[str, str]] = []
    huge_prompts: list[dict] = []
    huge_policy_updates: list[dict] = []
    run_history: list[dict] = []

    attempt_counters = defaultdict(int)
    alpha_started = threading.Event()
    alpha_release = threading.Event()

    def process_file(path: Path):
        name = path.name
        attempt_counters[name] += 1
        run_history.append({"file": name, "attempt": attempt_counters[name]})
        if name == "alpha.md":
            alpha_started.set()
            alpha_release.wait(5)
            return "done"
        if name == "beta.md":
            if attempt_counters[name] < 2:
                raise RuntimeError("beta transient failure")
            return "done"
        if name == "gamma.md":
            raise RuntimeError("gamma unrecoverable failure")
        return "done"

    def log_callback(level: str, message: str) -> None:
        log_messages.append((level, message))

    def state_callback(state: str) -> None:
        state_transitions.append(state)

    def progress_callback(index: int, total: int, path: str) -> None:
        progress_events.append((index, total, path))

    def file_done_callback(rel_path: str, status: str) -> None:
        file_done_events.append((rel_path, status))

    config_data = {
        "id": "report-folder",
        "name": "Report Folder Run",
        "template": "templates/prompts/Hooks.j2",
        "template_name": "Hooks.j2",
        "dry_run": False,
        "include_subfolders": True,
        "retry_failed_on_resume": True,
        "huge_file_policy": {"asked": False, "mode": None},
        "max_retries": 2,
        "llm_timeout_seconds": 30,
        "retry_backoff_base_seconds": 0.05,
    }
    (session_dir / "config.yaml").write_text(json.dumps(config_data), encoding="utf-8")

    config_lock = threading.Lock()

    def huge_decider(rel_path: str, abs_path: Path, context_len: int, policy: dict) -> str:
        record = {
            "path": rel_path,
            "context": context_len,
            "policy_before": dict(policy),
            "call": len(huge_prompts) + 1,
        }
        huge_prompts.append(record)
        return "proceed_all" if record["call"] == 1 else "proceed"

    def huge_policy_updater(policy: dict) -> None:
        with config_lock:
            config_data["huge_file_policy"] = dict(policy)
            (session_dir / "config.yaml").write_text(json.dumps(config_data), encoding="utf-8")
        huge_policy_updates.append(dict(policy))

    controller = RewriteSessionController(
        session_dir=session_dir,
        anchor_path=anchor,
        retry_failed_on_resume=True,
        process_file=process_file,
        log_callback=log_callback,
        state_callback=state_callback,
        progress_callback=progress_callback,
        file_finished_callback=file_done_callback,
        huge_policy=config_data["huge_file_policy"],
        huge_file_decider=huge_decider,
        huge_policy_updater=huge_policy_updater,
        huge_context_limit=20,
        max_retries=int(config_data["max_retries"]),
        llm_timeout_seconds=int(config_data["llm_timeout_seconds"]),
        retry_backoff_base_seconds=float(config_data["retry_backoff_base_seconds"]),
    )

    work_files = [anchor / "alpha.md", anchor / "beta.md", anchor / "gamma.md"]
    controller.initialize_worklist(work_files)
    controller.start()
    assert alpha_started.wait(2), "alpha.md never started"
    controller.request_stop()
    alpha_release.set()
    controller.wait_until_idle(5)

    state_after_stop = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    ledger_path = session_dir / "ledger.jsonl"
    ledger_lines = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    after_stop = {
        "state": state_after_stop,
        "ledger_tail": ledger_lines[-5:],
        "state_transitions": list(state_transitions),
    }

    # Inject processing entry to simulate recovered file on resume
    ledger_lines.append(
        {
            "type": "file",
            "path": "beta.md",
            "status": "processing",
            "ts": datetime.now().isoformat(),
        }
    )
    ledger_path.write_text("\n".join(json.dumps(entry) for entry in ledger_lines) + "\n", encoding="utf-8")

    controller.continue_run()
    controller.wait_until_idle(5)

    state_after_resume = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    ledger_lines = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    resume_plan, resume_snapshot = controller.compute_resume_plan()

    after_resume = {
        "state": state_after_resume,
        "ledger_tail": ledger_lines[-8:],
        "resume_plan": resume_plan,
        "resume_snapshot": resume_snapshot,
        "ledger": ledger_lines,
    }

    # Dry-run single file session to validate huge policy persistence
    file_session_dir = anchor / ".ai-rewrite" / "sessions" / "report-file"
    file_session_dir.mkdir(parents=True, exist_ok=True)
    file_config = {
        "id": "report-file",
        "name": "Report Single File",
        "dry_run": True,
        "include_subfolders": False,
        "retry_failed_on_resume": True,
        "huge_file_policy": {"asked": False, "mode": None},
        "max_retries": 1,
        "llm_timeout_seconds": 20,
        "retry_backoff_base_seconds": 0.05,
    }
    (file_session_dir / "config.yaml").write_text(json.dumps(file_config), encoding="utf-8")

    file_controller = RewriteSessionController(
        session_dir=file_session_dir,
        anchor_path=anchor,
        retry_failed_on_resume=True,
        process_file=process_file,
        log_callback=log_callback,
        state_callback=state_callback,
        progress_callback=progress_callback,
        file_finished_callback=file_done_callback,
        huge_policy=file_config["huge_file_policy"],
        huge_file_decider=huge_decider,
        huge_policy_updater=lambda policy: None,
        huge_context_limit=20,
        max_retries=1,
        llm_timeout_seconds=20,
        retry_backoff_base_seconds=0.05,
    )
    file_controller.initialize_worklist([anchor / "delta.md"])
    file_controller.start()
    file_controller.wait_until_idle(5)

    file_state = json.loads((file_session_dir / "state.json").read_text(encoding="utf-8"))
    file_ledger = [json.loads(line) for line in (file_session_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    return {
        "after_stop": after_stop,
        "after_resume": after_resume,
        "file_run": {"state": file_state, "ledger": file_ledger},
        "log_messages": list(log_messages),
        "state_transitions": list(state_transitions),
        "progress_events": list(progress_events),
        "file_done_events": list(file_done_events),
        "huge_prompts": list(huge_prompts),
        "huge_policy_updates": list(huge_policy_updates),
        "run_history": list(run_history),
    }


def _collect_ui_summary(tmp_path: Path, qapp, monkeypatch) -> dict:
    work_root = tmp_path / "ui"
    anchor = work_root / "vault"
    anchor.mkdir(parents=True, exist_ok=True)
    (anchor / "doc.md").write_text("content", encoding="utf-8")

    template_root = work_root / "templates"
    template_root.mkdir(parents=True, exist_ok=True)
    (template_root / "demo.j2").write_text("Template body", encoding="utf-8")

    monkeypatch.setattr(AiRewriteTab, "TEMPLATE_ROOT", template_root)

    class CaptureTab(AiRewriteTab):
        def __init__(self) -> None:
            super().__init__(None)
            self.captured_logs: list[tuple[str, str]] = []

        def append_log(self, level: str, message: str) -> None:  # type: ignore[override]
            self.captured_logs.append((level, message))
            super().append_log(level, message)

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(lambda *args, **kwargs: QtWidgets.QMessageBox.Yes))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *args, **kwargs: None))
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *args, **kwargs: None))
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", staticmethod(lambda *args, **kwargs: None))
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: QtWidgets.QMessageBox.Yes)


    def _list_entries(root: Path) -> list[str]:
        if not root.exists():
            return []
        return [str(p) for p in root.rglob('*') if p.is_file()]

    tab = CaptureTab()
    config = {
        "vault_path": str(anchor),
        "rewrite": {
            "anchor_folder": str(anchor),
            "retention": {"max_sessions": 1, "max_backup_age_days": 0},
        },
    }
    tab.set_app_config(config)
    tab.chk_dry_run.setChecked(False)

    session = tab._start_session("folder", anchor)
    session_dir = Path(session["dir"])
    base_ai = anchor / ".ai-rewrite"
    session_backups = session_dir / "backups"
    global_backups = base_ai / "backups"

    # Preflight failure by blocking backup creation
    shutil.rmtree(session_backups, ignore_errors=True)
    session_backups.write_text("blocked", encoding="utf-8")
    start_logs = len(tab.captured_logs)
    tab._start_session_run(session, mode="folder", target=anchor)
    failure_logs = tab.captured_logs[start_logs:]
    assert tab._session_state == "paused"
    assert any("cannot create backups" in msg for level, msg in failure_logs if level == "ERROR")
    tab._session_controller = None
    session_backups.unlink()

    # Successful preflight
    preflight_ok = tab._ensure_backup_writable(session_dir, dry_run=False)

    # Prepare dry-run artifacts for cleanup
    session_dry = session_dir / "dry-run"
    session_dry.mkdir(parents=True, exist_ok=True)
    (session_dry / "scratch.md").write_text("temp", encoding="utf-8")
    manifest_payload = {"paths": ["report-folder/sample"], "session_id": session["id"]}
    (session_dry / "global_paths.json").write_text(json.dumps(manifest_payload), encoding="utf-8")
    global_dry = base_ai / "dry-run"
    (global_dry / session["id"]).mkdir(parents=True, exist_ok=True)
    (global_dry / session["id"] / "sample.md").write_text("dry", encoding="utf-8")

    tab._session_is_dry_run = False
    tab._current_session_dir = session_dir
    tab._current_session_id = session["id"]
    tab._vault_path = anchor
    tab._anchor_path = anchor
    tab._dry_cleanup_done = False

    start_logs = len(tab.captured_logs)
    tab._cleanup_dry_run_artifacts()
    cleanup_logs = tab.captured_logs[start_logs:]

    # Delete all backups with confirmations auto-accepted
    for target in (session_backups, global_backups):
        target.mkdir(parents=True, exist_ok=True)
        (target / "sample.txt").write_text("backup", encoding="utf-8")

    tab._session_state = "paused"

    start_logs = len(tab.captured_logs)
    tab._on_delete_all_backups()
    delete_logs = tab.captured_logs[start_logs:]

    # Build pruning fixtures
    unfinished_dir = base_ai / "sessions" / "unfinished"
    unfinished_dir.mkdir(parents=True, exist_ok=True)
    unfinished_config = {
        "id": "unfinished",
        "name": "Unfinished Session",
        "timestamp": datetime.now().isoformat(),
    }
    (unfinished_dir / "config.yaml").write_text(json.dumps(unfinished_config), encoding="utf-8")
    unfinished_ledger = [
        {"type": "start", "timestamp": unfinished_config["timestamp"], "session_id": "unfinished"},
        {"type": "file", "path": "todo.md", "status": "processing", "ts": unfinished_config["timestamp"]},
    ]
    (unfinished_dir / "ledger.jsonl").write_text("\n".join(json.dumps(entry) for entry in unfinished_ledger) + "\n", encoding="utf-8")

    obsolete_dir = base_ai / "sessions" / "obsolete"
    obsolete_dir.mkdir(parents=True, exist_ok=True)
    obsolete_timestamp = (datetime.now() - timedelta(days=5)).isoformat()
    obsolete_config = {"id": "obsolete", "name": "Obsolete", "timestamp": obsolete_timestamp}
    (obsolete_dir / "config.yaml").write_text(json.dumps(obsolete_config), encoding="utf-8")
    obsolete_ledger = [
        {"type": "start", "timestamp": obsolete_timestamp, "session_id": "obsolete"},
        {"type": "file", "path": "old.md", "status": "done", "ts": obsolete_timestamp},
    ]
    (obsolete_dir / "ledger.jsonl").write_text("\n".join(json.dumps(entry) for entry in obsolete_ledger) + "\n", encoding="utf-8")

    stale_backup = global_backups / "stale"
    stale_backup.mkdir(parents=True, exist_ok=True)
    (stale_backup / "copy.md").write_text("backup", encoding="utf-8")

    start_logs = len(tab.captured_logs)
    tab._on_prune_sessions()
    prune_logs = tab.captured_logs[start_logs:]

    return {
        "preflight_ok": preflight_ok,
        "preflight_failure_logs": failure_logs,
        "cleanup_logs": cleanup_logs,
        "delete_logs": delete_logs,
        "prune_logs": prune_logs,
        "session_backup_entries": _list_entries(session_backups),
        "global_backup_entries": _list_entries(global_backups),
        "unfinished_exists": unfinished_dir.exists(),
        "obsolete_exists": obsolete_dir.exists(),
        "stale_backup_exists": stale_backup.exists(),
    }


def test_rewrite_controller_end_to_end(tmp_path):
    summary = _run_controller_flow(tmp_path)

    after_stop = summary["after_stop"]
    assert after_stop["state"]["state"] == "paused"
    assert after_stop["state"]["next_file"] == "beta.md"
    assert after_stop["state"]["processed"] == 1
    assert any(entry.get("session") == "pause" for entry in after_stop["ledger_tail"])

    after_resume = summary["after_resume"]
    assert any(entry.get("session") == "continue" for entry in after_resume["ledger"])
    assert after_resume["resume_plan"] == ["gamma.md"]
    assert after_resume["resume_snapshot"]["huge_file_policy"]["mode"] == "proceed_all"

    log_messages = summary["log_messages"]
    warn_messages = [msg for level, msg in log_messages if level == "WARN"]
    assert any("retry 1/2" in msg for msg in warn_messages)
    assert any("retry 2/2" in msg for msg in warn_messages)
    assert any("Recovered interrupted file" in msg for level, msg in log_messages if level == "WARN")

    error_messages = [msg for level, msg in log_messages if level == "ERROR"]
    assert any("gamma unrecoverable failure" in msg for msg in error_messages)
    assert any("failed after 2 retries" in msg for msg in error_messages)

    file_done_events = summary["file_done_events"]
    assert ("alpha.md", "done") in file_done_events
    assert ("beta.md", "done") in file_done_events
    assert ("gamma.md", "failed") in file_done_events

    huge_prompts = summary["huge_prompts"]
    assert huge_prompts[0]["path"] == "beta.md"
    assert summary["huge_policy_updates"] == [{"asked": True, "mode": "proceed_all"}]

    run_history = summary["run_history"]
    counts = defaultdict(int)
    for row in run_history:
        counts[row["file"]] += 1
    assert counts["alpha.md"] == 1
    assert counts["beta.md"] == 2
    assert counts["gamma.md"] == 3
    assert counts["delta.md"] == 1


def test_ui_safety_and_retention(tmp_path, qapp, monkeypatch):
    summary = _collect_ui_summary(tmp_path, qapp, monkeypatch)

    assert summary["preflight_ok"] is True
    assert any("cannot create backups" in msg for level, msg in summary["preflight_failure_logs"] if level == "ERROR")

    cleanup_entry = summary["cleanup_logs"][0]
    assert cleanup_entry[0] == "INFO"
    assert "Dry-run cleanup" in cleanup_entry[1]

    assert any(
        level == "INFO" and "Deleted backups: directories=2; files_removed=2; dirs_removed=2" in msg
        for level, msg in summary["delete_logs"]
    )
    assert not summary["session_backup_entries"]
    assert not summary["global_backup_entries"]

    assert any("Pruned sessions: removed" in msg for level, msg in summary["prune_logs"] if level == "INFO")
    assert summary["unfinished_exists"] is True
    assert summary["obsolete_exists"] is False
    assert summary["stale_backup_exists"] is False
