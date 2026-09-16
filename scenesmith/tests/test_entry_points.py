from __future__ import annotations

import importlib
import os
import runpy
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _clear_entry_modules() -> None:
    for name in list(sys.modules):
        if name == "main" or name == "_app_main" or name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def test_importing_root_main_has_no_launch_side_effects() -> None:
    _clear_entry_modules()

    root_main = importlib.import_module("main")

    assert callable(root_main.main)
    assert "app" not in sys.modules
    assert "_app_main" not in sys.modules
    assert not hasattr(root_main, "QApplication")
    assert not hasattr(root_main, "MainWindow")


def test_root_main_delegates_to_production_launcher(monkeypatch) -> None:
    _clear_entry_modules()
    root_main = importlib.import_module("main")
    app_package = importlib.import_module("app")
    calls: list[str] = []

    def fake_production_main() -> None:
        calls.append("called")

    monkeypatch.setitem(app_package.__dict__, "main", fake_production_main)

    root_main.main()

    assert calls == ["called"]
    assert hasattr(app_package, "__path__")
    assert "_app_main" not in sys.modules


def test_documented_src_app_path_remains_loadable() -> None:
    _clear_entry_modules()

    namespace = runpy.run_path(str(PROJECT_ROOT / "src" / "app.py"), run_name="_not_main_")

    assert callable(namespace["main"])
    assert namespace["MainWindow"].__name__ == "MainWindow"
    assert sys.modules["app"].__spec__.submodule_search_locations is not None


def test_guest_preset_loader_preserves_named_eligibility_fields(tmp_path: Path) -> None:
    _clear_entry_modules()
    namespace = runpy.run_path(str(PROJECT_ROOT / "src" / "app.py"), run_name="_not_main_")
    preset_path = tmp_path / "guest_presets.yaml"
    preset_path.write_text(
        """presets:
  Court:
    choose_from: [Council]
    eligible_tags: [Council]
    allow_guests: [Tyler, ' Anita   Wainwright ']
    prefer_guests: [Tyler]
    exclude_guests: [Helena]
display_names:
  ' Helena ': ' Portia '
""",
        encoding="utf-8",
    )

    presets = namespace["_load_guest_presets"](preset_path)

    assert presets["Court"]["eligible_tags"] == ["Council"]
    assert presets["Court"]["allow_guests"] == ["Tyler", "Anita   Wainwright"]
    assert presets["Court"]["prefer_guests"] == ["Tyler"]
    assert presets["Court"]["exclude_guests"] == ["Helena"]
    assert namespace["_load_guest_display_names"](preset_path) == {"helena": "Portia"}
