from abc import ABC, abstractmethod
from pathlib import Path

try:
    from config_io import load_app_config as _load_app_config
    from model_adapters import DeepSeekAdapter as _ActiveDeepSeekAdapter
    from model_adapters import with_literal_api_key as _with_literal_api_key
except ModuleNotFoundError:
    from src.config_io import load_app_config as _load_app_config
    from src.model_adapters import DeepSeekAdapter as _ActiveDeepSeekAdapter
    from src.model_adapters import with_literal_api_key as _with_literal_api_key


_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "app.example.yaml"
LEGACY_DEFAULT_MODEL = "deepseek-chat"


def _legacy_model_cfg(api_key: str):
    model_cfg = dict((_load_app_config(str(_CONFIG_PATH)).get("model") or {}))
    model_cfg.pop("endpoint", None)
    model_cfg.pop("timeout", None)
    model_cfg.pop("params", None)
    model_cfg["name"] = model_cfg.get("name") or LEGACY_DEFAULT_MODEL
    return _with_literal_api_key(model_cfg, api_key)


class ModelAdapter(ABC):
    @abstractmethod
    def query(self, prompt: str) -> str:
        pass

class DeepSeekAdapter(ModelAdapter):
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._adapter = _ActiveDeepSeekAdapter()

    def query(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        return self._adapter.generate(messages, _legacy_model_cfg(self.api_key))

# Add more adapters as needed
