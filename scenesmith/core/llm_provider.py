from __future__ import annotations

import os
from typing import Any, Dict

from model_adapters import get_adapter

SYSTEM_PROMPT = (
    "You generate high-quality Markdown. Output must be pure Markdown. "
    "Do not include image syntax (![[...]] or ![...](...))."
)

DEFAULT_REWRITE_TEMPLATE = (
    "File: {filename}\n"
    "Path: {path}\n"
    "Existing content (images stripped):\n---\n{existing_content}\n---\n"
    "Task: Produce a complete Markdown replacement for the non-image content. "
    "Keep headings coherent with the filename. No image syntax."
)


class LlmProvider:
    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config or {}
        model_cfg = self._config.get("model") or {}
        provider_key = _resolve_provider_key(model_cfg)
        self._adapter = _get_adapter(provider_key)
        self._model_cfg = model_cfg
        self._rewrite_template = self._resolve_rewrite_template()

    def _resolve_rewrite_template(self) -> str:
        prompt_section = self._config.get("prompt_template") or {}
        if isinstance(prompt_section, dict):
            template = prompt_section.get("rewrite")
            if isinstance(template, str) and template.strip():
                return template
        return DEFAULT_REWRITE_TEMPLATE

    def generate_markdown(
        self,
        *,
        filename: str,
        path: str,
        existing_non_image_text: str,
        context: Dict[str, Any] | None = None,
    ) -> str:
        existing = existing_non_image_text.strip()
        payload = {
            "filename": filename,
            "path": path,
            "existing_content": existing or "[EMPTY]",
        }
        if context:
            payload.update(context)
        user_prompt = self._rewrite_template.format_map(_SafeDict(payload))
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        return self.generate_from_messages(messages)


    def chat(self, prompt: str, *, system_instruction: str | None = None) -> str:
        system = (system_instruction or "").strip() or SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        return self.generate_from_messages(messages)

    def generate_from_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        strip_response: bool = True,
        request_options: dict[str, Any] | None = None,
    ) -> str:
        response = self._adapter.generate(messages, self._model_cfg, request_options=request_options)
        text = response or ""
        if strip_response:
            text = text.strip()
        usage = getattr(self._adapter, "last_usage", None)
        if usage:
            tokens_in = usage.get("prompt_tokens")
            tokens_out = usage.get("completion_tokens")
            print(
                f"[DEBUG] [LLM] model={self._model_cfg.get('name')} "
                f"tokens_in={tokens_in} tokens_out={tokens_out}"
            )
        else:
            print(f"[DEBUG] [LLM] model={self._model_cfg.get('name')}")
        return text


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class _MockAdapter:
    def __init__(self) -> None:
        self.last_usage: Dict[str, int] | None = {
            "prompt_tokens": 0,
            "completion_tokens": 5,
        }

    def generate(self, messages, model_cfg, *, request_options=None):
        return "[MOCK NEW CONTENT]\n\n- bullet 1\n- bullet 2"


def _resolve_provider_key(model_cfg: Dict[str, Any]) -> str:
    if os.getenv("LLM_MOCK"):
        return "__mock__"
    provider_key = model_cfg.get("provider") or model_cfg.get("name")
    if not provider_key:
        raise RuntimeError("[ERROR] Model provider/name missing in config.model")
    return provider_key


def _get_adapter(provider_key: str):
    if provider_key == "__mock__":
        return _MockAdapter()
    return get_adapter(provider_key)


__all__ = ["LlmProvider", "SYSTEM_PROMPT", "DEFAULT_REWRITE_TEMPLATE"]
