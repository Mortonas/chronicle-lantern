from __future__ import annotations

import json
import threading
import yaml
from datetime import datetime
from pathlib import Path

from rewrite_session import RewriteSessionController, TERMINAL_STATUSES


def test_controller_pause_and_resume(tmp_path):
    anchor = tmp_path / "vault"
    anchor.mkdir()
    first = anchor / "first.md"
    first.write_text("first", encoding="utf-8")
    second = anchor / "second.md"
    second.write_text("second", encoding="utf-8")

    session_dir = anchor / ".ai-rewrite" / "sessions" / "sess1"
    session_dir.mkdir(parents=True)

    start_events = [threading.Event(), threading.Event()]
    release_events = [threading.Event(), threading.Event()]
    processed: list[Path] = []
    logs: list[tuple[str, str]] = []
    states: list[str] = []

    def process_file(path: Path) -> str:
        idx = len(processed)
        start_events[idx].set()
        release_events[idx].wait()
        processed.append(path)
        return "done"

    controller = RewriteSessionController(
        session_dir=session_dir,
        anchor_path=anchor,
        retry_failed_on_resume=True,
        process_file=process_file,
        log_callback=lambda level, message: logs.append((level, message)),
        state_callback=lambda state: states.append(state),
    )
    controller.initialize_worklist([second, first])
    controller.start()

    assert start_events[0].wait(1), "first file never started"
    controller.request_stop()
    release_events[0].set()
    controller.wait_until_idle(2)

    assert controller.current_state() == "paused"
    assert controller.next_file() == "second.md"
    assert [p.name for p in processed] == ["first.md"]
    assert any(level == "SELECT" and "paused" in message for level, message in logs)

    ledger_path = session_dir / "ledger.jsonl"
    ledger_entries = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(entry.get("session") == "pause" for entry in ledger_entries)
    assert any(
        entry.get("type") == "file" and entry.get("path") == "first.md" and entry.get("status") == "done"
        for entry in ledger_entries
    )
    assert not any(
        entry.get("type") == "file" and entry.get("path") == "second.md" and entry.get("status") in {"done", "failed", "skipped"}
        for entry in ledger_entries
    )

    controller.continue_run()
    assert start_events[1].wait(1), "second file never started after continue"
    release_events[1].set()
    controller.wait_until_idle(2)

    assert controller.current_state() == "completed"
    assert controller.last_completed_file() == "second.md"
    ledger_entries = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(entry.get("session") == "continue" for entry in ledger_entries)
    assert any(
        entry.get("type") == "file" and entry.get("path") == "second.md" and entry.get("status") == "done"
        for entry in ledger_entries
    )
    assert [p.name for p in processed] == ["first.md", "second.md"]
    assert states.count("paused") == 1
    assert states[-1] == "completed"

def test_huge_file_skip_policy(tmp_path):
    anchor = tmp_path / "vault"
    anchor.mkdir()
    huge_one = anchor / "first.md"
    huge_one.write_text("a" * 25, encoding="utf-8")
    huge_two = anchor / "second.md"
    huge_two.write_text("b" * 30, encoding="utf-8")
    normal = anchor / "third.md"
    normal.write_text("tiny", encoding="utf-8")

    session_dir = anchor / ".ai-rewrite" / "sessions" / "sess-skip"
    session_dir.mkdir(parents=True)

    call_count = 0
    processed: list[str] = []
    updates: list[dict] = []

    def process_file(path: Path) -> str:
        processed.append(path.name)
        return "done"

    def decider(rel_path: str, *_args) -> str:
        nonlocal call_count
        call_count += 1
        return "skip_one"

    controller = RewriteSessionController(
        session_dir=session_dir,
        anchor_path=anchor,
        retry_failed_on_resume=True,
        process_file=process_file,
        huge_policy={"asked": False, "mode": None},
        huge_file_decider=decider,
        huge_policy_updater=lambda policy: updates.append(dict(policy)),
        huge_context_limit=20,
    )
    controller.initialize_worklist([huge_one, huge_two, normal])
    controller.start()
    controller.wait_until_idle(2)

    ledger = [
        json.loads(line)
        for line in (session_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    skipped = [entry for entry in ledger if entry.get("status") == "skipped"]
    assert len(skipped) == 2
    assert all(entry.get("reason") == "huge-file" for entry in skipped)
    assert processed == ["third.md"]
    assert call_count == 2  # re-prompt for each huge file
    assert updates == []


def test_huge_file_proceed_one_prompts_again(tmp_path):
    anchor = tmp_path / "vault"
    anchor.mkdir()
    huge_one = anchor / "first.md"
    huge_one.write_text("a" * 30, encoding="utf-8")
    huge_two = anchor / "second.md"
    huge_two.write_text("b" * 40, encoding="utf-8")

    session_dir = anchor / ".ai-rewrite" / "sessions" / "sess-proceed"
    session_dir.mkdir(parents=True)

    decisions = ["proceed_one", "skip_one"]
    call_count = 0
    processed: list[str] = []

    def process_file(path: Path) -> str:
        processed.append(path.name)
        return "done"

    def decider(rel_path: str, *_args) -> str:
        nonlocal call_count
        choice = decisions[call_count]
        call_count += 1
        return choice

    controller = RewriteSessionController(
        session_dir=session_dir,
        anchor_path=anchor,
        retry_failed_on_resume=True,
        process_file=process_file,
        huge_policy={"asked": False, "mode": None},
        huge_file_decider=decider,
        huge_context_limit=20,
    )
    controller.initialize_worklist([huge_one, huge_two])
    controller.start()
    controller.wait_until_idle(2)

    assert call_count == 2  # proceed_one forced a second prompt
    ledger = [
        json.loads(line)
        for line in (session_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(entry.get("path") == "first.md" and entry.get("status") == "done" for entry in ledger)
    assert any(entry.get("path") == "second.md" and entry.get("status") == "skipped" for entry in ledger)
    assert processed == ["first.md"]






def test_compute_resume_plan_filters_statuses(tmp_path):
    anchor = tmp_path / "vault"
    anchor.mkdir()
    session_dir = anchor / ".ai-rewrite" / "sessions" / "sess-plan"
    session_dir.mkdir(parents=True)

    config = {
        "id": "sess-plan",
        "name": "Plan",
        "anchor": str(anchor),
        "template": "templates/rewrite.j2",
        "template_name": "rewrite.j2",
        "dry_run": False,
        "include_subfolders": False,
        "retry_failed_on_resume": True,
        "huge_file_policy": {"asked": True, "mode": "proceed_all"},
        "max_retries": 5,
        "llm_timeout_seconds": 200,
        "retry_backoff_base_seconds": 1.5,
    }
    (session_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    ledger_entries = [
        {"type": "file", "path": "B.md", "status": "done", "ts": datetime.now().isoformat()},
        {"type": "file", "path": "a.md", "status": "pending", "ts": datetime.now().isoformat()},
        {"type": "file", "path": "C.md", "status": "processing", "ts": datetime.now().isoformat()},
        {"type": "file", "path": "d.md", "status": "failed", "ts": datetime.now().isoformat()},
        {"type": "file", "path": "skip.md", "status": "skipped", "ts": datetime.now().isoformat()},
    ]
    ledger_path = session_dir / "ledger.jsonl"
    with ledger_path.open("w", encoding="utf-8") as fh:
        for entry in ledger_entries:
            fh.write(json.dumps(entry) + "\n")

    controller = RewriteSessionController(
        session_dir=session_dir,
        anchor_path=anchor,
        retry_failed_on_resume=True,
        process_file=lambda path: "done",
    )
    eligible, snapshot = controller.compute_resume_plan()
    assert eligible == ["a.md", "C.md", "d.md"]
    assert snapshot["retry_failed_on_resume"] is True
    assert snapshot["dry_run"] is False
    assert snapshot["include_subfolders"] is False
    assert snapshot["huge_file_policy"] == {"asked": True, "mode": "proceed_all"}
    assert snapshot["max_retries"] == 5
    assert snapshot["llm_timeout_seconds"] == 200
    assert snapshot["retry_backoff_base_seconds"] == 1.5
    assert snapshot["recovered_paths"] == ["C.md"]

    config["retry_failed_on_resume"] = False
    (session_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    eligible_after, snapshot_after = controller.compute_resume_plan()
    assert eligible_after == ["a.md", "C.md"]
    assert snapshot_after["retry_failed_on_resume"] is False

def test_recovered_processing_resumes_with_retry_entry(tmp_path):
    anchor = tmp_path / "vault"
    anchor.mkdir()
    target = anchor / "doc.md"
    target.write_text("hello", encoding="utf-8")

    session_dir = anchor / ".ai-rewrite" / "sessions" / "sess-rec"
    session_dir.mkdir(parents=True)

    state = {
        "files": ["doc.md"],
        "total_files": 1,
        "processed": 0,
        "last_completed": None,
        "next_file": "doc.md",
        "state": "paused",
        "retry_failed_on_resume": True,
    }
    (session_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    ledger_lines = [
        {"type": "start", "timestamp": datetime.now().isoformat(), "session_id": "sess-rec", "name": "Recovered", "mode": "file"},
        {"type": "file", "path": "doc.md", "status": "processing", "ts": datetime.now().isoformat()},
    ]
    ledger_path = session_dir / "ledger.jsonl"
    with ledger_path.open("w", encoding="utf-8") as fh:
        for entry in ledger_lines:
            fh.write(json.dumps(entry) + "\n")

    controller = RewriteSessionController(
        session_dir=session_dir,
        anchor_path=anchor,
        retry_failed_on_resume=True,
        process_file=lambda path: "done",
        max_retries=1,
        llm_timeout_seconds=30,
        retry_backoff_base_seconds=0.01,
    )
    controller.continue_run()
    controller.wait_until_idle(2)

    ledger = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    retry_entries = [entry for entry in ledger if entry.get("type") == "retry"]
    assert any(entry.get("reason") == "recovered" for entry in retry_entries)
    final_entry = next(entry for entry in ledger if entry.get("type") == "file" and entry.get("status") == "done" and entry.get("path") == "doc.md")
    assert final_entry.get("retries") == 0


def test_retry_eventual_success(tmp_path):
    anchor = tmp_path / "vault"
    anchor.mkdir()
    target = anchor / "file.md"
    target.write_text("content", encoding="utf-8")

    session_dir = anchor / ".ai-rewrite" / "sessions" / "sess-retry"
    session_dir.mkdir(parents=True)

    attempts = {"count": 0}
    logs: list[tuple[str, str]] = []

    def process_file(path: Path) -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("temporary failure")
        return "done"

    controller = RewriteSessionController(
        session_dir=session_dir,
        anchor_path=anchor,
        retry_failed_on_resume=True,
        process_file=process_file,
        log_callback=lambda level, message: logs.append((level, message)),
        max_retries=3,
        llm_timeout_seconds=30,
        retry_backoff_base_seconds=0.01,
    )
    controller.initialize_worklist([target])
    controller.start()
    controller.wait_until_idle(2)

    assert controller.current_state() == "completed"
    ledger = [
        json.loads(line)
        for line in (session_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    retry_entries = [entry for entry in ledger if entry.get("type") == "retry"]
    assert len(retry_entries) == 2
    assert retry_entries[0]["retry"] == 1
    assert retry_entries[1]["retry"] == 2
    final_entry = next(entry for entry in ledger if entry.get("path") == "file.md" and entry.get("status") in TERMINAL_STATUSES)
    assert final_entry["status"] == "done"
    assert final_entry.get("retries") == 2
    warn_messages = [msg for level, msg in logs if level == "WARN"]
    assert any("retry 1/3" in msg for msg in warn_messages)
    assert any("retry 2/3" in msg for msg in warn_messages)


def test_retry_failure_continues_with_next_file(tmp_path):
    anchor = tmp_path / "vault"
    anchor.mkdir()
    first = anchor / "first.md"
    first.write_text("first", encoding="utf-8")
    second = anchor / "second.md"
    second.write_text("second", encoding="utf-8")

    session_dir = anchor / ".ai-rewrite" / "sessions" / "sess-fail"
    session_dir.mkdir(parents=True)

    processed: list[str] = []
    logs: list[tuple[str, str]] = []

    def process_file(path: Path) -> str:
        if path.name == "first.md":
            raise RuntimeError("boom")
        processed.append(path.name)
        return "done"

    controller = RewriteSessionController(
        session_dir=session_dir,
        anchor_path=anchor,
        retry_failed_on_resume=True,
        process_file=process_file,
        log_callback=lambda level, message: logs.append((level, message)),
        max_retries=1,
        llm_timeout_seconds=30,
        retry_backoff_base_seconds=0.01,
    )
    controller.initialize_worklist([first, second])
    controller.start()
    controller.wait_until_idle(2)

    assert controller.current_state() == "paused"
    assert controller.next_file() == "first.md"
    assert controller.last_completed_file() == "second.md"
    assert processed == ["second.md"]

    ledger = [
        json.loads(line)
        for line in (session_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    first_final = next(entry for entry in ledger if entry.get("path") == "first.md" and entry.get("status") == "failed")
    assert first_final.get("retries") == 1
    assert first_final.get("reason") == "error"
    assert "boom" in first_final.get("message", "")
    second_final = next(entry for entry in ledger if entry.get("path") == "second.md" and entry.get("status") == "done")
    assert second_final.get("retries") == 0
    warn_messages = [msg for level, msg in logs if level == "WARN"]
    assert any("retry 1/1" in msg for msg in warn_messages)
    error_messages = [msg for level, msg in logs if level == "ERROR"]
    assert any("failed after 1 retries" in msg for msg in error_messages)


