from __future__ import annotations

import importlib

from app_types import EventEntry, EventTable, SelectionResult


def _app_module():
    app_package = importlib.import_module("app")
    return app_package._load_app_module()


def _worker(module, *, preview_only: bool):
    table = EventTable(
        name="Test Table",
        entries=[EventEntry(scene_concept="Locked-room omen", tags=["scholars"])],
        source_path="table.yaml",
        rel_path_from_project="table.yaml",
    )
    return module.GenerateWorker(
        cfg={
            "ripgrep_path": "rg.exe",
            "vault_path": "C:/vault",
            "model": {"provider": "deepseek", "name": "deepseek-v4-pro"},
            "prompt": {"template": "Scene={scene_concept}; Context={context}"},
        },
        table=table,
        use_table_tags=True,
        extra_groups_ui=[],
        location_text="Chantry",
        npc_count=2,
        lock_selection=None,
        preview_only=preview_only,
        roll_concept=True,
        prompt_template=None,
    )


def _install_selection_fakes(monkeypatch, module):
    selection = SelectionResult(
        scene_concept="Locked-room omen",
        primary_files=["C:/vault/npc.md"],
        lore_files=[],
        npc_count=2,
        location="Chantry",
        active_tags=["scholars"],
        chosen_tags=["scholars"],
        npc_tag_map={1: "scholars"},
    )

    monkeypatch.setattr(module, "prepare_selection", lambda *args, **kwargs: selection)
    monkeypatch.setattr(module, "build_context_block", lambda primary, lore: "context block")

    def fake_render_prompt(template_ref, variables):
        assert variables == {
            "location": "Chantry",
            "scene_concept": "Locked-room omen",
            "npc_count": 2,
            "context": "context block",
        }
        return "rendered scene prompt"

    monkeypatch.setattr(module, "render_prompt", fake_render_prompt)


def test_generate_worker_uses_llm_provider_and_preserves_raw_scene_output(monkeypatch, qapp):
    module = _app_module()
    _install_selection_fakes(monkeypatch, module)
    provider_calls = []

    class FakeProvider:
        def __init__(self, cfg):
            provider_calls.append({"cfg": cfg})

        def generate_from_messages(self, messages, *, strip_response=True):
            provider_calls[-1].update({"messages": messages, "strip_response": strip_response})
            return "  raw scene output\n"

    monkeypatch.setattr(module, "LlmProvider", FakeProvider)

    worker = _worker(module, preview_only=False)
    done = []
    errors = []
    worker.signals.done.connect(done.append)
    worker.signals.error.connect(errors.append)

    worker.run()

    assert errors == []
    assert len(done) == 1
    assert done[0]["mode"] == "generate"
    assert done[0]["prompt"] == "rendered scene prompt"
    assert done[0]["output"] == "  raw scene output\n"
    assert provider_calls[0]["messages"] == [
        {"role": "system", "content": module.SYSTEM_PROMPT},
        {"role": "user", "content": "rendered scene prompt"},
    ]
    assert provider_calls[0]["strip_response"] is False


def test_generate_worker_preview_does_not_initialize_provider(monkeypatch, qapp):
    module = _app_module()
    _install_selection_fakes(monkeypatch, module)

    class FailingProvider:
        def __init__(self, cfg):
            raise AssertionError("preview should not initialize LlmProvider")

    monkeypatch.setattr(module, "LlmProvider", FailingProvider)

    worker = _worker(module, preview_only=True)
    done = []
    errors = []
    worker.signals.done.connect(done.append)
    worker.signals.error.connect(errors.append)

    worker.run()

    assert errors == []
    assert len(done) == 1
    assert done[0]["mode"] == "preview"
    assert done[0]["prompt"] == "rendered scene prompt"
