from __future__ import annotations
from dataclasses import dataclass
import os
import re
import httpx
from typing import Protocol, Dict, List, Any
from urllib.parse import urlparse

SENSITIVE_OPTION_KEY_PARTS = ("api_key", "token", "secret", "password", "authorization", "auth")
REQUEST_OPTION_KEYS = {"response_format", "thinking"}
DEEPSEEK_NATIVE_HOSTS = {"api.deepseek.com"}
DEEPSEEK_THINKING_MODEL_FAMILIES = {
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash-vision-exp",
}


class ModelAdapter(Protocol):
    def generate(
        self,
        messages: List[Dict[str, Any]],
        model_cfg: Dict,
        *,
        request_options: Dict[str, Any] | None = None,
    ) -> str: ...


@dataclass(frozen=True, repr=False)
class LiteralSecret:
    value: str | None

    def __repr__(self) -> str:
        return "LiteralSecret(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


def with_literal_api_key(model_cfg: Dict[str, Any], api_key: str | None) -> Dict[str, Any]:
    resolved_cfg = dict(model_cfg or {})
    resolved_cfg["api_key"] = LiteralSecret(api_key)
    return resolved_cfg


def _resolve_reference_or_literal(value: Any, *, default_environment: str) -> str:
    if isinstance(value, LiteralSecret):
        return str(value.value or "").strip()
    raw = str(value or "").strip()
    if raw.lower().startswith("env:"):
        return os.getenv(raw[4:].strip(), "").strip()
    if raw.startswith("${") and raw.endswith("}"):
        return os.getenv(raw[2:-1].strip(), "").strip()
    if raw:
        return raw
    return os.getenv(default_environment, "").strip()


def _post_chat_completion(client: httpx.Client, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> httpx.Response:
    return client.post(url, json=payload, headers=headers)


def _message_content_chars(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else ""
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += sum(len(str(item.get("text", ""))) for item in content if isinstance(item, dict))
    return total


def _safe_payload_options(payload: Dict[str, Any]) -> Dict[str, Any]:
    options: Dict[str, Any] = {}
    for key, value in payload.items():
        if key in {"model", "messages", "response_format", "thinking"}:
            continue
        lowered = str(key).lower()
        if any(part in lowered for part in SENSITIVE_OPTION_KEY_PARTS):
            options[key] = "<redacted>"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            options[key] = value
        else:
            options[key] = f"<{value.__class__.__name__}>"
    return options


def _request_options_summary(payload: Dict[str, Any]) -> str:
    parts: list[str] = []
    response_format = payload.get("response_format")
    if isinstance(response_format, dict):
        response_type = str(response_format.get("type") or "")
        if response_type:
            parts.append(f"response_format={response_type}")
    thinking = payload.get("thinking")
    if isinstance(thinking, dict):
        thinking_type = str(thinking.get("type") or "")
        if thinking_type:
            parts.append(f"thinking={thinking_type}")
    return ",".join(parts) if parts else "none"


def _print_request_summary(provider_name: str, payload: Dict[str, Any]) -> None:
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    print(
        f"[DEBUG] {provider_name} request: "
        f"model={payload.get('model')!r} "
        f"messages={len(messages)} "
        f"content_chars={_message_content_chars(messages)} "
        f"options={_safe_payload_options(payload)} "
        f"request_options={_request_options_summary(payload)}"
    )


def _validated_response_format(request_options: Dict[str, Any]) -> Dict[str, str] | None:
    if "response_format" not in request_options:
        return None
    response_format = request_options["response_format"]
    if not isinstance(response_format, dict) or response_format.get("type") != "json_object":
        raise ValueError("request_options.response_format must be {'type': 'json_object'}")
    return {"type": "json_object"}


def _validated_thinking(request_options: Dict[str, Any]) -> Dict[str, str] | None:
    if "thinking" not in request_options:
        return None
    thinking = request_options["thinking"]
    if not isinstance(thinking, dict) or thinking.get("type") != "disabled":
        raise ValueError("request_options.thinking must be {'type': 'disabled'}")
    return {"type": "disabled"}


def _reject_unknown_request_options(request_options: Dict[str, Any]) -> None:
    unknown = set(request_options) - REQUEST_OPTION_KEYS
    if unknown:
        names = ", ".join(sorted(str(key) for key in unknown))
        raise ValueError(f"Unsupported request option(s): {names}")


def _deepseek_capability_enabled(model_cfg: Dict[str, Any], key: str) -> bool:
    capabilities = model_cfg.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get(key) is True:
        return True
    return model_cfg.get(key) is True


def _is_native_deepseek_endpoint(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host in DEEPSEEK_NATIVE_HOSTS


def _is_verified_deepseek_thinking_model(model: Any) -> bool:
    name = str(model or "").strip()
    return name in DEEPSEEK_THINKING_MODEL_FAMILIES


def _deepseek_supports_thinking(model_cfg: Dict[str, Any], *, model: Any, base_url: str) -> bool:
    if _deepseek_capability_enabled(model_cfg, "deepseek_thinking"):
        return True
    if _deepseek_capability_enabled(model_cfg, "supports_deepseek_thinking"):
        return True
    return _is_native_deepseek_endpoint(base_url) and _is_verified_deepseek_thinking_model(model)


def _deepseek_request_options(
    request_options: Dict[str, Any] | None,
    *,
    model_cfg: Dict[str, Any],
    model: Any,
    base_url: str,
) -> Dict[str, Any]:
    if not request_options:
        return {}
    _reject_unknown_request_options(request_options)
    options: Dict[str, Any] = {}
    response_format = _validated_response_format(request_options)
    if response_format is not None:
        options["response_format"] = response_format
    thinking = _validated_thinking(request_options)
    if thinking is not None and _deepseek_supports_thinking(model_cfg, model=model, base_url=base_url):
        options["thinking"] = thinking
    return options


def _openai_compat_request_options(request_options: Dict[str, Any] | None) -> Dict[str, Any]:
    if not request_options:
        return {}
    _reject_unknown_request_options(request_options)
    options: Dict[str, Any] = {}
    response_format = _validated_response_format(request_options)
    if response_format is not None:
        options["response_format"] = response_format
    if "thinking" in request_options:
        _validated_thinking(request_options)
    return options


def _deepseek_malformed_response_message(data: Any) -> str:
    base = "DeepSeek response did not include non-empty choices[0].message.content."
    diagnostics = _deepseek_response_diagnostics(data)
    if not diagnostics:
        return base
    return f"{base} Diagnostics: {'; '.join(diagnostics)}."


def _safe_diag_value(value: str, *, limit: int) -> str:
    clean = " ".join(str(value or "").split())
    clean = re.sub(r"(?i)\bsk-[A-Za-z0-9._-]+", "sk-<redacted>", clean)
    clean = re.sub(r"\b[A-Z0-9_]*DO_NOT_LEAK[A-Z0-9_]*\b", "<redacted>", clean)
    if len(clean) > limit:
        clean = clean[: limit - 3].rstrip() + "..."
    return clean


def _deepseek_response_diagnostics(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    diagnostics: list[str] = []
    model = data.get("model")
    if isinstance(model, str) and model.strip():
        diagnostics.append(f"model={_safe_diag_value(model, limit=80)}")
    choices = data.get("choices")
    first_choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    finish_reason = first_choice.get("finish_reason") if isinstance(first_choice, dict) else None
    if isinstance(finish_reason, str) and finish_reason.strip():
        diagnostics.append(f"finish_reason={_safe_diag_value(finish_reason, limit=40)}")
    message = first_choice.get("message") if isinstance(first_choice, dict) and isinstance(first_choice.get("message"), dict) else {}
    content = message.get("content") if isinstance(message, dict) else None
    content_text = content.strip() if isinstance(content, str) else ""
    diagnostics.append(f"content_present={content is not None}")
    diagnostics.append(f"content_length={len(content_text)}")
    reasoning_content = message.get("reasoning_content") if isinstance(message, dict) else None
    reasoning_present = isinstance(reasoning_content, str) and bool(reasoning_content.strip())
    diagnostics.append(f"reasoning_content_present={reasoning_present}")
    usage = data.get("usage")
    if isinstance(usage, dict):
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int):
                diagnostics.append(f"{key}={value}")
    return diagnostics


class DeepSeekAdapter:
    def __init__(self) -> None:
        self._last_usage: Dict[str, Any] | None = None

    def _resolve_timeout(self, cfg: Any) -> httpx.Timeout:
        default = httpx.Timeout(connect=10.0, read=180.0, write=120.0, pool=None)
        if cfg is None:
            return default
        if isinstance(cfg, (int, float)):
            return httpx.Timeout(float(cfg))
        if isinstance(cfg, dict):
            kwargs: Dict[str, float] = {}
            for key in ("connect", "read", "write", "pool"):
                value = cfg.get(key)
                if value is not None:
                    kwargs[key] = float(value)
            if not kwargs:
                return default
            return httpx.Timeout(**kwargs)
        print("[WARN] model.timeout must be a mapping or number; using default.")
        return default

    def _resolve_api_key(self, model_cfg: Dict[str, Any]) -> str:
        api_key = model_cfg.get("api_key")
        if isinstance(api_key, LiteralSecret):
            if api_key.value is None:
                return ""
            return str(api_key.value).strip()

        sentinels = {"", "PUT_KEY_HERE_OR_USE_ENV"}
        raw = str(model_cfg.get("api_key") or "").strip()
        env_candidates: List[str] = []
        if raw and raw not in sentinels:
            lowered = raw.lower()
            if lowered.startswith("env:"):
                env_name = raw[4:].strip()
                if env_name:
                    env_candidates.append(env_name)
            elif raw.startswith("${") and raw.endswith("}"):
                env_name = raw[2:-1].strip()
                if env_name:
                    env_candidates.append(env_name)
            elif raw.endswith("_ENV"):
                env_candidates.append(raw)
            else:
                return raw
        alt_env = str(model_cfg.get("env_key") or "").strip()
        if alt_env:
            env_candidates.append(alt_env)
        env_candidates.extend([
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_APIKEY",
            "DEEPSEEK_KEY",
        ])
        for name in env_candidates:
            if not name:
                continue
            value = os.getenv(name)
            if value:
                return value.strip()
        return ""

    def _extract_content(self, data: Any) -> str:
        if not isinstance(data, dict):
            raise RuntimeError(_deepseek_malformed_response_message(data))
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(_deepseek_malformed_response_message(data))
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise RuntimeError(_deepseek_malformed_response_message(data))
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError(_deepseek_malformed_response_message(data))
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(_deepseek_malformed_response_message(data))
        return content

    def generate(
        self,
        messages: List[Dict[str, Any]],
        model_cfg: Dict,
        *,
        request_options: Dict[str, Any] | None = None,
    ) -> str:
        if not isinstance(messages, list):
            raise TypeError("messages must be a list of chat payloads")
        model_cfg = model_cfg or {}
        api_key = self._resolve_api_key(model_cfg)
        if not api_key:
            raise RuntimeError("DeepSeek API key not provided (config.model.api_key or DEEPSEEK_API_KEY).")
        model = model_cfg.get("name", "deepseek-chat")
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {"model": model, "messages": messages, "stream": False}
        params = model_cfg.get("params") or {}
        payload.update(params)
        base_url = model_cfg.get("endpoint") or "https://api.deepseek.com/v1"
        payload.update(_deepseek_request_options(request_options, model_cfg=model_cfg, model=model, base_url=base_url))
        url = base_url.rstrip("/\\") + "/chat/completions"
        _print_request_summary("DeepSeek", payload)
        timeout = self._resolve_timeout(model_cfg.get("timeout"))
        try:
            with httpx.Client(timeout=timeout) as client:
                r = _post_chat_completion(client, url, payload, headers)
                if r.status_code != 200:
                    print(f"[DEBUG] DeepSeek API error {r.status_code}")
                r.raise_for_status()
        except httpx.TimeoutException as exc:
            raise RuntimeError("DeepSeek request timed out. Consider increasing config.model.timeout or reducing max_tokens.") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("DeepSeek request failed.") from None
        data = r.json()
        self._last_usage = data.get("usage") if isinstance(data, dict) else None
        return self._extract_content(data)

    @property
    def last_usage(self) -> Dict[str, Any] | None:
        return self._last_usage


class OpenAICompatAdapter:
    """Generic OpenAI-compatible chat API adapter (for later endpoints like local proxies)."""

    def __init__(self) -> None:
        self._last_usage: Dict[str, Any] | None = None

    def generate(
        self,
        messages: List[Dict[str, Any]],
        model_cfg: Dict,
        *,
        request_options: Dict[str, Any] | None = None,
    ) -> str:
        if not isinstance(messages, list):
            raise TypeError("messages must be a list of chat payloads")
        api_key = _resolve_reference_or_literal(
            model_cfg.get("api_key"), default_environment="OPENAI_API_KEY"
        )
        endpoint = model_cfg.get("endpoint")
        if not endpoint:
            raise RuntimeError("OpenAI-compatible endpoint missing in config.model.endpoint")
        model = model_cfg.get("name", "gpt-3.5-turbo")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        payload = {"model": model, "messages": messages}
        params = model_cfg.get("params") or {}
        payload.update(params)
        payload.update(_openai_compat_request_options(request_options))
        with httpx.Client(timeout=60) as client:
            r = _post_chat_completion(client, endpoint.rstrip("/") + "/chat/completions", payload, headers)
            r.raise_for_status()
            data = r.json()
            self._last_usage = data.get("usage")
            return data["choices"][0]["message"]["content"]

    @property
    def last_usage(self) -> Dict[str, Any] | None:
        return self._last_usage


def get_adapter(provider: str) -> ModelAdapter:
    if provider == "deepseek":
        return DeepSeekAdapter()
    if provider == "openai_compat":
        return OpenAICompatAdapter()
    raise ValueError(f"Unknown model provider: {provider}")
