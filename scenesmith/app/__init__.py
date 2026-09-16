from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_PKG_DIR = Path(__file__).resolve().parent
_SRC_DIR = _PKG_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

_APP_MODULE: ModuleType | None = None
_EXPORT_NAMES = {"MainWindow", "GenerateWorker", "main"}


def _load_app_module() -> ModuleType:
    global _APP_MODULE
    if _APP_MODULE is not None:
        return _APP_MODULE
    spec = importlib.util.spec_from_file_location("_app_main", _SRC_DIR / "app.py")
    if spec is None or spec.loader is None:
        raise ImportError("Unable to load core app module.")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_app_main"] = module
    spec.loader.exec_module(module)
    _APP_MODULE = module
    return module


def __getattr__(name: str) -> Any:
    if name not in _EXPORT_NAMES:
        raise AttributeError(name)
    module = _load_app_module()
    return getattr(module, name)


def __dir__() -> list[str]:
    module = _load_app_module()
    return sorted(set(globals()) | set(dir(module)))


__all__ = sorted(_EXPORT_NAMES)
