from __future__ import annotations

from pathlib import Path
import json
import yaml



import pytest

from core.rewrite_runner import RewriteConfig, RewriteWorker


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "1")


@pytest.fixture
def temp_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "text_only.md").write_text("Line A\nLine B\n", encoding="utf-8")
    (vault / "mixed.md").write_text(
        "![[image1.png]]\nAlpha\n![alt](img.jpg)\nBeta\n![[tail.webp]]\n",
        encoding="utf-8",
    )
    (vault / "images_only.md").write_text("![[only.png]]\n", encoding="utf-8")
    return vault


def _run_worker(cfg: RewriteConfig) -> dict:
    worker = RewriteWorker(cfg)
    captured: dict = {}

    worker.signals.progress.connect(lambda cur, total: captured.setdefault("progress", []).append((cur, total)))
    worker.signals.file_done.connect(
        lambda path, images, orig_chars, new_chars: captured.setdefault("files", []).append(
            (path, images, orig_chars, new_chars)
        )
    )
    worker.signals.warning.connect(lambda msg: captured.setdefault("warnings", []).append(msg))
    worker.signals.error.connect(lambda msg: captured.setdefault("errors", []).append(msg))
    worker.signals.finished.connect(lambda stats: captured.setdefault("finished", stats))

    worker.run()  # synchronous execution within tests
    return captured


def test_dry_run_preserves_originals(temp_vault: Path):
    config = {
        "vault_path": str(temp_vault),
        "rewrite": {"max_bytes": 1_000_000, "concurrency": 1},
    }
    cfg = RewriteConfig(mode="folder", path=temp_vault, dry_run=True, recurse=True, config=config)
    captured = _run_worker(cfg)

    dry_dir = temp_vault / ".ai-rewrite" / "dry-run"
    assert dry_dir.exists()

    original_text = (temp_vault / "text_only.md").read_text(encoding="utf-8")
    dry_text = (dry_dir / "text_only.md").read_text(encoding="utf-8")
    assert original_text == "Line A\nLine B\n"
    assert dry_text.startswith("[MOCK NEW CONTENT]")
    assert "Line" not in dry_text

    mixed_original = (temp_vault / "mixed.md").read_text(encoding="utf-8")
    mixed_dry = (dry_dir / "mixed.md").read_text(encoding="utf-8")
    assert "![[image1.png]]" in mixed_dry
    assert "![[tail.webp]]" in mixed_dry
    assert mixed_original != mixed_dry

    finished = captured["finished"]
    assert finished["dry_run"] is True
    assert finished["processed"] == 3


def test_live_run_writes_backups_and_rewrites(temp_vault: Path):
    config = {
        "vault_path": str(temp_vault),
        "rewrite": {"max_bytes": 1_000_000, "concurrency": 2},
    }
    cfg = RewriteConfig(mode="folder", path=temp_vault, dry_run=False, recurse=True, config=config)
    captured = _run_worker(cfg)

    backup_dir = Path(captured["finished"].get("backup_dir") or "")
    assert backup_dir.exists()

    backup_file = backup_dir / "text_only.bak.md"
    assert backup_file.exists()
    assert backup_file.read_text(encoding="utf-8") == "Line A\nLine B\n"

    rewritten = (temp_vault / "text_only.md").read_text(encoding="utf-8")
    assert rewritten.startswith("[MOCK NEW CONTENT]")
    assert "Line" not in rewritten

    mixed_rewritten = (temp_vault / "mixed.md").read_text(encoding="utf-8")
    assert "![[image1.png]]" in mixed_rewritten
    assert "![[tail.webp]]" in mixed_rewritten


def test_undo_restores_latest_backup(temp_vault: Path, qtbot):
    config = {
        "vault_path": str(temp_vault),
        "rewrite": {"max_bytes": 1_000_000, "concurrency": 1},
    }
    cfg = RewriteConfig(mode="folder", path=temp_vault, dry_run=False, recurse=True, config=config)
    captured = _run_worker(cfg)

    backup_dir = Path(captured["finished"].get("backup_dir") or "")
    assert backup_dir.exists()

    from ui.ai_rewrite_tab import AiRewriteTab

    tab = AiRewriteTab()
    qtbot.addWidget(tab)
    tab.set_app_config(config, config_path=str(temp_vault / "config.yaml"))

    tab._last_backup_dir = backup_dir  # pylint: disable=protected-access
    tab._vault_path = temp_vault  # pylint: disable=protected-access

    tab.undo_last_rewrite()

    logs = tab.log_output.toPlainText()
    assert "Undo completed" in logs
    assert "restored 0" not in logs

    restored = (temp_vault / "text_only.md").read_text(encoding="utf-8")
    assert restored == "Line A\nLine B\n"
    assert not tab._has_backup  # pylint: disable=protected-access




def test_session_creation_creates_structure(tmp_path, qtbot, monkeypatch):
    from ui.ai_rewrite_tab import AiRewriteTab

    anchor = tmp_path / "anchor"
    anchor.mkdir()
    template_root = tmp_path / "templates"
    template_root.mkdir()
    template_file = template_root / "example.j2"
    template_file.write_text("Hello\n{context}\n", encoding="utf-8")

    original_root = AiRewriteTab.TEMPLATE_ROOT
    AiRewriteTab.TEMPLATE_ROOT = template_root
    try:
        config_path = tmp_path / "app_config.yaml"
        config_path.write_text("{}", encoding="utf-8")
        config = {
            "vault_path": str(anchor),
            "rewrite": {
                "anchor_folder": str(anchor),
                "template_name": template_file.name,
                "dry_run_default": True,
                "include_subfolders_default": False,
                "max_bytes": 1_000_000,
                "concurrency": 1,
            },
        }

        tab = AiRewriteTab()
        qtbot.addWidget(tab)
        tab.set_app_config(config, config_path=str(config_path))
        tab.session_name_edit.setText("My Session")

        tab._anchor_path = anchor
        tab._update_anchor_display()
        tab._selected_template_name = template_file.name
        tab._selected_template_path = template_file
        tab._update_action_states()

        sample_file = anchor / "note.md"
        sample_file.write_text("Hello world\n", encoding="utf-8")
        tab._selected_file = sample_file
        tab._update_selected_file_label()

        assert tab._anchor_path == anchor
        assert tab._selected_template_path == template_file

        tab._on_rewrite_file()

        logs = tab.log_output.toPlainText()
        assert "session" in logs.lower()
        session_line = next((line for line in logs.splitlines() if "Session " in line), None)
        assert session_line is not None
        session_path_str = session_line.split(" at ", 1)[-1]
        session_path = Path(session_path_str.strip())
        assert session_path.exists()

        sessions_dir = anchor / ".ai-rewrite" / "sessions"
        session_dirs = list(sessions_dir.iterdir())
        assert len(session_dirs) == 1
        session_dir = session_dirs[0]

        snapshot = yaml.safe_load((session_dir / "config.yaml").read_text(encoding="utf-8"))
        assert snapshot["name"] == "My Session"
        assert snapshot["anchor"] == str(anchor)
        assert snapshot["retry_failed_on_resume"] is True
        assert snapshot.get("huge_file_policy") == {"asked": False, "mode": None}
        assert snapshot.get("max_retries") == 2
        assert snapshot.get("llm_timeout_seconds") == 180
        assert snapshot.get("retry_backoff_base_seconds") == 1.0

        ledger_lines = (session_dir / "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert ledger_lines
        start_entry = json.loads(ledger_lines[0])
        assert start_entry["type"] == "start"
        assert start_entry["mode"] == "file"
        assert start_entry["retry_failed_on_resume"] is True

        for sub in ("preview", "dry-run", "backups"):
            assert (session_dir / sub).is_dir()
    finally:
        AiRewriteTab.TEMPLATE_ROOT = original_root




