from __future__ import annotations

import importlib
from pathlib import Path

from PySide6.QtWidgets import QDialog


def _app_module():
    app_package = importlib.import_module("app")
    return app_package._load_app_module()


def test_main_window_npc_picker_add_remove_clear(qapp, tmp_path: Path) -> None:
    from app import MainWindow

    window = MainWindow()
    try:
        npc = tmp_path / "npc.md"
        npc.write_text("#scholars", encoding="utf-8")

        assert window._add_locked_npc(str(npc)) is True
        assert window._add_locked_npc(str(npc)) is False

        assert window.locked_npc_files == [str(npc)]
        assert window.locked_npc_table.rowCount() == 1

        window.locked_npc_table.selectRow(0)
        window.remove_selected_locked_npc()
        assert window.locked_npc_files == []

        window._add_locked_npc(str(npc))
        window.clear_locked_npcs()
        assert window.locked_npc_files == []
        assert window.locked_npc_table.rowCount() == 0
    finally:
        window.close()


def test_main_window_pick_scene_npcs_uses_shared_modal(monkeypatch, qapp, tmp_path: Path) -> None:
    from app import MainWindow

    npc = tmp_path / "npc.md"
    npc.write_text("#scholars", encoding="utf-8")
    calls: list[dict] = []

    class FakeNpcPickerDialog:
        def __init__(self, vault_path, *, mode, parent=None):
            calls.append({"vault_path": vault_path, "mode": mode, "parent": parent})

        def exec(self):
            return QDialog.Accepted

        def selected_paths(self):
            return [str(npc), str(npc)]

    window = MainWindow()
    try:
        module = _app_module()
        monkeypatch.setattr(module, "NpcPickerDialog", FakeNpcPickerDialog)

        window.pick_scene_npcs()

        assert calls[0]["mode"] == "guest"
        assert calls[0]["vault_path"] == window.cfg.get("vault_path")
        assert window.locked_npc_files == [str(npc)]
        assert window.locked_npc_table.rowCount() == 1
    finally:
        window.close()


def test_generate_worker_passes_locked_npcs_to_selection(monkeypatch, qapp, tmp_path: Path) -> None:
    module = _app_module()
    from app_types import EventEntry, EventTable, SelectionResult

    locked = tmp_path / "locked.md"
    locked.write_text("#scholars", encoding="utf-8")
    captured: dict = {}

    def fake_prepare_selection(*args, **kwargs):
        captured.update(kwargs)
        return SelectionResult(
            scene_concept="Locked-room omen",
            primary_files=[str(locked)],
            lore_files=[],
            npc_count=1,
            location="Chantry",
            active_tags=["scholars"],
            chosen_tags=[],
            npc_tag_map={},
        )

    monkeypatch.setattr(module, "prepare_selection", fake_prepare_selection)
    monkeypatch.setattr(module, "build_context_block", lambda primary, lore: "context")
    monkeypatch.setattr(module, "render_prompt", lambda template_ref, variables: "prompt")

    table = EventTable(
        name="Test",
        entries=[EventEntry(scene_concept="Locked-room omen", tags=["scholars"])],
        source_path="table.yaml",
        rel_path_from_project="table.yaml",
    )
    worker = module.GenerateWorker(
        cfg={
            "ripgrep_path": "rg",
            "vault_path": str(tmp_path),
            "model": {"provider": "deepseek"},
            "prompt": {"template": "template"},
        },
        table=table,
        use_table_tags=True,
        extra_groups_ui=[],
        location_text="Chantry",
        npc_count=1,
        lock_selection=None,
        preview_only=True,
        roll_concept=True,
        prompt_template=None,
        locked_npc_files=[str(locked)],
    )

    errors: list[str] = []
    worker.signals.error.connect(errors.append)
    worker.run()

    assert errors == []
    assert captured["locked_primary_files"] == [str(locked)]
