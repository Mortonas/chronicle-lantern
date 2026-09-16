from __future__ import annotations

from pathlib import Path
import time

import pytest

from config_io import load_app_config
from core import llm_provider
import model_adapters
from model_adapters import DeepSeekAdapter, LiteralSecret, OpenAICompatAdapter, get_adapter, with_literal_api_key


DEFAULT_RESPONSE_DATA = {
    "choices": [{"message": {"content": "generated text"}}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 4},
}
MESSAGES = [{"role": "user", "content": "hello"}]
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC_DEEPSEEK_KEY = "sk-test-UNMISTAKABLE-DEEPSEEK-SECRET-1234567890"
FORBIDDEN_MARKERS = (
    "PROMPT_MARKER_DO_NOT_LEAK",
    "sk-test-key-material-do-not-leak",
    "RAW_PROVIDER_BODY_DO_NOT_LEAK",
)
SYNTHETIC_KEY_FRAGMENTS = (
    SYNTHETIC_DEEPSEEK_KEY,
    SYNTHETIC_DEEPSEEK_KEY[:8],
    "UNMISTAKABLE",
    "DEEPSEEK-SECRET",
    "1234567890",
)


class FakeResponse:
    def __init__(self, status_code=200, data=DEFAULT_RESPONSE_DATA, text="OK", error=None):
        self.status_code = status_code
        self._data = data
        self.text = text
        self._error = error

    def json(self):
        return self._data

    def raise_for_status(self):
        if self._error is not None:
            raise self._error


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    def blocked_client(*args, **kwargs):
        raise AssertionError("unexpected real httpx.Client construction")

    monkeypatch.setattr(model_adapters.httpx, "Client", blocked_client)


def install_fake_client(monkeypatch, response=None, post_error=None):
    calls = []
    response = response or FakeResponse()

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout
            calls.append({"timeout": timeout})

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json, headers):
            calls[-1].update({"url": url, "json": json, "headers": headers})
            if post_error is not None:
                raise post_error
            return response

    monkeypatch.setattr(model_adapters.httpx, "Client", FakeClient)
    return calls


def clear_provider_env(monkeypatch):
    for name in (
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_APIKEY",
        "DEEPSEEK_KEY",
        "OPENAI_API_KEY",
        "CUSTOM_DEEPSEEK",
        "ALT_DEEPSEEK",
        "TOKEN_ENV",
    ):
        monkeypatch.delenv(name, raising=False)


def assert_no_synthetic_key_material(*texts):
    combined = "\n".join(str(text) for text in texts)
    leaked = any(fragment in combined for fragment in SYNTHETIC_KEY_FRAGMENTS)
    assert not leaked, "synthetic credential material leaked"


def assert_no_forbidden_markers(*texts):
    combined = "\n".join(str(text) for text in texts)
    leaked = [marker for marker in FORBIDDEN_MARKERS if marker in combined]
    assert not leaked, f"forbidden marker leaked: {leaked}"


def assert_synthetic_key_sent_to_fake_client(calls):
    headers = calls[0]["headers"]
    sent = headers.get("Authorization") == f"Bearer {SYNTHETIC_DEEPSEEK_KEY}"
    assert sent, "synthetic credential was not sent to fake client"


def test_get_adapter_selects_active_providers():
    assert isinstance(get_adapter("deepseek"), DeepSeekAdapter)
    assert isinstance(get_adapter("openai_compat"), OpenAICompatAdapter)

    with pytest.raises(ValueError, match="Unknown model provider"):
        get_adapter("missing")


def test_with_literal_api_key_copies_config_and_wraps_secret():
    params = {"temperature": 0.7}
    original = {"api_key": "env:DEEPSEEK_API_KEY", "name": "deepseek-v4-pro", "params": params}

    injected = with_literal_api_key(original, "literal-key")

    assert injected is not original
    assert injected["api_key"] == LiteralSecret("literal-key")
    assert injected["params"] is params
    assert original["api_key"] == "env:DEEPSEEK_API_KEY"


def test_literal_secret_repr_and_str_are_redacted():
    secret = LiteralSecret("literal-key")

    assert "literal-key" not in repr(secret)
    assert "literal-key" not in str(secret)
    assert "redacted" in repr(secret)


@pytest.mark.parametrize(
    ("cfg", "env", "expected"),
    [
        ({"api_key": "literal-key"}, {"DEEPSEEK_API_KEY": "default-key"}, "literal-key"),
        ({"api_key": "env:CUSTOM_DEEPSEEK"}, {"CUSTOM_DEEPSEEK": "custom-key"}, "custom-key"),
        ({"api_key": "${CUSTOM_DEEPSEEK}"}, {"CUSTOM_DEEPSEEK": "braced-key"}, "braced-key"),
        ({"api_key": "TOKEN_ENV"}, {"TOKEN_ENV": "suffix-key"}, "suffix-key"),
        ({"env_key": "ALT_DEEPSEEK"}, {"ALT_DEEPSEEK": "alt-key"}, "alt-key"),
        ({"api_key": "PUT_KEY_HERE_OR_USE_ENV"}, {"DEEPSEEK_APIKEY": "legacy-key"}, "legacy-key"),
        ({}, {"DEEPSEEK_KEY": "short-key"}, "short-key"),
    ],
)
def test_deepseek_api_key_resolution_order(monkeypatch, cfg, env, expected):
    clear_provider_env(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    calls = install_fake_client(monkeypatch)

    result = DeepSeekAdapter().generate(MESSAGES, cfg)

    assert result == "generated text"
    assert calls[0]["headers"] == {"Authorization": f"Bearer {expected}"}


def test_deepseek_literal_secret_bypasses_env_resolution(monkeypatch):
    clear_provider_env(monkeypatch)
    monkeypatch.setenv("LEGACY_SHOULD_NOT_BE_READ", "environment-key-must-not-be-used")
    calls = install_fake_client(monkeypatch)

    result = DeepSeekAdapter().generate(
        MESSAGES,
        {"api_key": LiteralSecret(" env:LEGACY_SHOULD_NOT_BE_READ ")},
    )

    assert result == "generated text"
    assert calls[0]["headers"] == {"Authorization": "Bearer env:LEGACY_SHOULD_NOT_BE_READ"}


@pytest.mark.parametrize("api_key", [LiteralSecret(""), LiteralSecret(None)])
def test_deepseek_literal_secret_missing_values_fail_before_client(monkeypatch, api_key):
    clear_provider_env(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key-must-not-be-used")

    with pytest.raises(RuntimeError, match="DeepSeek API key not provided"):
        DeepSeekAdapter().generate(MESSAGES, {"api_key": api_key})


def test_deepseek_request_defaults_endpoint_payload_timeout_and_usage(monkeypatch):
    clear_provider_env(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-env")
    calls = install_fake_client(monkeypatch)
    adapter = DeepSeekAdapter()

    result = adapter.generate(MESSAGES, {})

    assert result == "generated text"
    assert calls[0]["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert calls[0]["json"] == {
        "model": "deepseek-chat",
        "messages": MESSAGES,
        "stream": False,
    }
    assert calls[0]["timeout"].connect == 10.0
    assert calls[0]["timeout"].read == 180.0
    assert calls[0]["timeout"].write == 120.0
    assert calls[0]["timeout"].pool is None
    assert adapter.last_usage == {"prompt_tokens": 3, "completion_tokens": 4}


def test_deepseek_adapter_uses_explicit_model_without_enabling_example_provider(monkeypatch):
    clear_provider_env(monkeypatch)
    config = load_app_config(str(PROJECT_ROOT / "config" / "app.example.yaml"))
    model_cfg = dict(config["model"])
    assert model_cfg["provider"] is None
    assert model_cfg["name"] is None
    model_cfg["provider"] = "deepseek"
    model_cfg["name"] = "deepseek-test-model"
    model_cfg["api_key"] = "literal-key"
    calls = install_fake_client(monkeypatch)

    DeepSeekAdapter().generate(MESSAGES, model_cfg)

    assert model_cfg["provider"] == "deepseek"
    assert model_cfg["name"] == "deepseek-test-model"
    assert model_cfg["endpoint"] is None
    assert calls[0]["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert calls[0]["json"]["model"] == "deepseek-test-model"


def test_deepseek_delayed_success_returns_non_empty_content(monkeypatch):
    clear_provider_env(monkeypatch)
    calls = []

    class DelayedClient:
        def __init__(self, timeout):
            calls.append({"timeout": timeout})

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json, headers):
            time.sleep(0.01)
            calls[-1].update({"url": url, "json": json, "headers": headers})
            return FakeResponse(data={"choices": [{"message": {"content": "eventual scene"}}]})

    monkeypatch.setattr(model_adapters.httpx, "Client", DelayedClient)

    result = DeepSeekAdapter().generate(MESSAGES, {"api_key": "literal-key"})

    assert result == "eventual scene"
    assert calls[0]["timeout"].read == 180.0


def test_deepseek_success_does_not_emit_key_material(monkeypatch, capsys):
    clear_provider_env(monkeypatch)
    calls = install_fake_client(monkeypatch)

    result = DeepSeekAdapter().generate(MESSAGES, {"api_key": SYNTHETIC_DEEPSEEK_KEY})

    captured = capsys.readouterr()
    assert result == "generated text"
    assert_synthetic_key_sent_to_fake_client(calls)
    assert_no_synthetic_key_material(captured.out, captured.err)


def test_deepseek_debug_logging_summarizes_request_without_prompt_text(monkeypatch, capsys):
    clear_provider_env(monkeypatch)
    prompt_text = "raw club prompt that should never be printed"
    calls = install_fake_client(monkeypatch)

    DeepSeekAdapter().generate(
        [{"role": "user", "content": prompt_text}],
        {"api_key": SYNTHETIC_DEEPSEEK_KEY, "params": {"max_tokens": 123}},
    )

    captured = capsys.readouterr()
    assert_synthetic_key_sent_to_fake_client(calls)
    assert "DeepSeek request:" in captured.out
    assert "content_chars" in captured.out
    assert "max_tokens" in captured.out
    assert prompt_text not in captured.out
    assert "'messages':" not in captured.out
    assert_no_synthetic_key_material(captured.out, captured.err)


def test_deepseek_request_options_override_config_defaults_and_log_allowlisted_summary(monkeypatch, capsys):
    clear_provider_env(monkeypatch)
    calls = install_fake_client(monkeypatch)

    DeepSeekAdapter().generate(
        MESSAGES,
        {
            "api_key": SYNTHETIC_DEEPSEEK_KEY,
            "name": "deepseek-v4-pro",
            "params": {
                "response_format": {"type": "text"},
                "thinking": {"type": "enabled"},
                "max_tokens": 123,
            },
        },
        request_options={
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        },
    )

    assert calls[0]["json"]["response_format"] == {"type": "json_object"}
    assert calls[0]["json"]["thinking"] == {"type": "disabled"}
    assert calls[0]["json"]["max_tokens"] == 123
    captured = capsys.readouterr()
    assert "request_options=response_format=json_object,thinking=disabled" in captured.out
    assert "'response_format':" not in captured.out
    assert "'thinking':" not in captured.out
    assert_no_synthetic_key_material(captured.out, captured.err)


def test_request_options_reject_unknown_keys_before_http(monkeypatch):
    clear_provider_env(monkeypatch)
    calls = install_fake_client(monkeypatch)

    with pytest.raises(ValueError, match="Unsupported request option"):
        DeepSeekAdapter().generate(
            MESSAGES,
            {"api_key": "literal-key"},
            request_options={"response_format": {"type": "json_object"}, "prompt": "do not send"},
        )

    assert calls == []


def test_deepseek_thinking_option_requires_native_support_or_explicit_capability(monkeypatch):
    clear_provider_env(monkeypatch)
    calls = install_fake_client(monkeypatch)

    DeepSeekAdapter().generate(
        MESSAGES,
        {
            "api_key": "literal-key",
            "name": "deepseek-v4-pro",
            "endpoint": "https://proxy.test/v1",
        },
        request_options={
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        },
    )

    assert calls[0]["json"]["response_format"] == {"type": "json_object"}
    assert "thinking" not in calls[0]["json"]

    calls = install_fake_client(monkeypatch)
    DeepSeekAdapter().generate(
        MESSAGES,
        {
            "api_key": "literal-key",
            "name": "provider-hosted-deepseek",
            "endpoint": "https://proxy.test/v1",
            "capabilities": {"deepseek_thinking": True},
        },
        request_options={"thinking": {"type": "disabled"}},
    )

    assert calls[0]["json"]["thinking"] == {"type": "disabled"}


def test_openai_compat_request_options_forward_json_mode_but_not_deepseek_thinking(monkeypatch):
    clear_provider_env(monkeypatch)
    calls = install_fake_client(monkeypatch)

    OpenAICompatAdapter().generate(
        MESSAGES,
        {"endpoint": "https://proxy.test"},
        request_options={
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        },
    )

    assert calls[0]["json"]["response_format"] == {"type": "json_object"}
    assert "thinking" not in calls[0]["json"]


def assert_sanitized_deepseek_response_error(excinfo):
    text = str(excinfo.value)
    assert text.startswith("DeepSeek response did not include non-empty choices[0].message.content.")
    assert_no_synthetic_key_material(text)
    assert_no_forbidden_markers(text)
    assert "raw-provider-payload" not in text
    assert "Authorization" not in text
    assert "Bearer" not in text


@pytest.mark.parametrize("content", ["", "   \n"])
def test_deepseek_success_rejects_empty_or_whitespace_content(monkeypatch, content):
    clear_provider_env(monkeypatch)
    forbidden = " ".join(FORBIDDEN_MARKERS)
    install_fake_client(
        monkeypatch,
        response=FakeResponse(
            data={
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": content,
                            "reasoning_content": f"safe reasoning summary, not {forbidden}",
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                "raw": f"raw-provider-payload {SYNTHETIC_DEEPSEEK_KEY} {forbidden}",
            }
        ),
    )

    with pytest.raises(RuntimeError) as excinfo:
        DeepSeekAdapter().generate(MESSAGES, {"api_key": SYNTHETIC_DEEPSEEK_KEY})

    assert_sanitized_deepseek_response_error(excinfo)
    assert "finish_reason=stop" in str(excinfo.value)
    assert "reasoning_content_present=True" in str(excinfo.value)
    assert "prompt_tokens=10" in str(excinfo.value)


@pytest.mark.parametrize(
    "data",
    [
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": ["not", "text"]}}]},
        {"choices": [{"message": "not-a-message-object"}]},
        {"choices": []},
        {"choices": "not-a-list"},
        {"choices": [{"delta": {"content": "streamed"}}]},
        {"error": {"message": "raw-provider-payload"}},
        {},
        None,
    ],
)
def test_deepseek_schema_incompatible_success_raises_sanitized_runtime_error(monkeypatch, data):
    clear_provider_env(monkeypatch)
    forbidden = " ".join(FORBIDDEN_MARKERS)
    install_fake_client(
        monkeypatch,
        response=FakeResponse(data=data, text=f"raw-provider-payload {SYNTHETIC_DEEPSEEK_KEY} {forbidden}"),
    )

    with pytest.raises(RuntimeError) as excinfo:
        DeepSeekAdapter().generate(MESSAGES, {"api_key": SYNTHETIC_DEEPSEEK_KEY})

    assert_sanitized_deepseek_response_error(excinfo)


def test_deepseek_custom_endpoint_payload_params_and_timeout(monkeypatch):
    clear_provider_env(monkeypatch)
    calls = install_fake_client(monkeypatch)

    DeepSeekAdapter().generate(
        MESSAGES,
        {
            "api_key": "literal-key",
            "name": "deepseek-reasoner",
            "endpoint": "https://example.test/root/\\",
            "params": {"temperature": 0.2, "max_tokens": 123, "stream": True},
            "timeout": {"connect": 1, "read": 2, "write": 3, "pool": 4},
        },
    )

    assert calls[0]["url"] == "https://example.test/root/chat/completions"
    assert calls[0]["json"] == {
        "model": "deepseek-reasoner",
        "messages": MESSAGES,
        "stream": True,
        "temperature": 0.2,
        "max_tokens": 123,
    }
    assert calls[0]["timeout"].connect == 1.0
    assert calls[0]["timeout"].read == 2.0
    assert calls[0]["timeout"].write == 3.0
    assert calls[0]["timeout"].pool == 4.0


def test_deepseek_missing_key_fails_before_client(monkeypatch):
    clear_provider_env(monkeypatch)

    with pytest.raises(RuntimeError, match="DeepSeek API key not provided"):
        DeepSeekAdapter().generate(MESSAGES, {})


def test_deepseek_wraps_http_status_and_timeout_errors(monkeypatch):
    clear_provider_env(monkeypatch)
    status_error = model_adapters.httpx.HTTPStatusError(
        "bad status",
        request=model_adapters.httpx.Request("POST", "https://example.test"),
        response=model_adapters.httpx.Response(500),
    )
    calls = install_fake_client(monkeypatch, response=FakeResponse(status_code=500, text="bad", error=status_error))

    with pytest.raises(RuntimeError, match="DeepSeek request failed"):
        DeepSeekAdapter().generate(MESSAGES, {"api_key": "literal-key"})
    assert calls[0]["url"] == "https://api.deepseek.com/v1/chat/completions"

    timeout_error = model_adapters.httpx.TimeoutException("slow")
    install_fake_client(monkeypatch, post_error=timeout_error)
    with pytest.raises(RuntimeError, match="DeepSeek request timed out"):
        DeepSeekAdapter().generate(MESSAGES, {"api_key": "literal-key"})


def test_deepseek_failure_does_not_emit_key_material(monkeypatch, capsys):
    clear_provider_env(monkeypatch)
    forbidden = " ".join(FORBIDDEN_MARKERS)
    status_error = model_adapters.httpx.HTTPStatusError(
        f"bad status for {SYNTHETIC_DEEPSEEK_KEY} {forbidden}",
        request=model_adapters.httpx.Request("POST", "https://example.test"),
        response=model_adapters.httpx.Response(500),
    )
    calls = install_fake_client(
        monkeypatch,
        response=FakeResponse(
            status_code=500,
            text=f"provider echoed {SYNTHETIC_DEEPSEEK_KEY} {forbidden}",
            error=status_error,
        ),
    )

    with pytest.raises(RuntimeError) as excinfo:
        DeepSeekAdapter().generate(MESSAGES, {"api_key": SYNTHETIC_DEEPSEEK_KEY})

    captured = capsys.readouterr()
    assert str(excinfo.value) == "DeepSeek request failed."
    assert excinfo.value.__cause__ is None
    assert_synthetic_key_sent_to_fake_client(calls)
    assert_no_synthetic_key_material(captured.out, captured.err, excinfo.value)
    assert_no_forbidden_markers(captured.out, captured.err, excinfo.value)

    post_error = model_adapters.httpx.TransportError(f"transport included {SYNTHETIC_DEEPSEEK_KEY} {forbidden}")
    install_fake_client(monkeypatch, post_error=post_error)
    with pytest.raises(RuntimeError) as transport_excinfo:
        DeepSeekAdapter().generate(MESSAGES, {"api_key": SYNTHETIC_DEEPSEEK_KEY})

    captured = capsys.readouterr()
    assert str(transport_excinfo.value) == "DeepSeek request failed."
    assert transport_excinfo.value.__cause__ is None
    assert_no_synthetic_key_material(captured.out, captured.err, transport_excinfo.value)
    assert_no_forbidden_markers(captured.out, captured.err, transport_excinfo.value)


def test_openai_compat_key_headers_endpoint_payload_timeout_and_usage(monkeypatch):
    clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-env")
    calls = install_fake_client(monkeypatch)
    adapter = OpenAICompatAdapter()

    result = adapter.generate(
        MESSAGES,
        {
            "endpoint": "https://proxy.test/v1/",
            "params": {"temperature": 0.9, "max_tokens": 44},
        },
    )

    assert result == "generated text"
    assert calls[0]["url"] == "https://proxy.test/v1/chat/completions"
    assert calls[0]["headers"] == {"Authorization": "Bearer openai-env"}
    assert calls[0]["json"] == {
        "model": "gpt-3.5-turbo",
        "messages": MESSAGES,
        "temperature": 0.9,
        "max_tokens": 44,
    }
    assert calls[0]["timeout"] == 60
    assert adapter.last_usage == {"prompt_tokens": 3, "completion_tokens": 4}


def test_openai_compat_config_key_resolves_environment_reference_and_can_be_absent(monkeypatch):
    clear_provider_env(monkeypatch)
    monkeypatch.setenv("CHRONICLE_OPENAI_KEY", "resolved-openai-key")
    calls = install_fake_client(monkeypatch)

    OpenAICompatAdapter().generate(
        MESSAGES,
        {"endpoint": "https://proxy.test", "api_key": "env:CHRONICLE_OPENAI_KEY", "name": "custom-model"},
    )

    assert calls[0]["headers"] == {"Authorization": "Bearer resolved-openai-key"}
    assert calls[0]["json"]["model"] == "custom-model"

    calls = install_fake_client(monkeypatch)
    OpenAICompatAdapter().generate(MESSAGES, {"endpoint": "https://proxy.test"})

    assert calls[0]["headers"] == {}


def test_openai_compat_requires_endpoint_but_not_api_key(monkeypatch):
    clear_provider_env(monkeypatch)

    with pytest.raises(RuntimeError, match="endpoint missing"):
        OpenAICompatAdapter().generate(MESSAGES, {})


def test_openai_compat_exposes_http_status_errors_without_wrapping(monkeypatch):
    clear_provider_env(monkeypatch)
    status_error = model_adapters.httpx.HTTPStatusError(
        "bad status",
        request=model_adapters.httpx.Request("POST", "https://example.test"),
        response=model_adapters.httpx.Response(500),
    )
    install_fake_client(monkeypatch, response=FakeResponse(status_code=500, text="bad", error=status_error))

    with pytest.raises(model_adapters.httpx.HTTPStatusError):
        OpenAICompatAdapter().generate(MESSAGES, {"endpoint": "https://proxy.test"})


@pytest.mark.parametrize(
    "adapter,cfg",
    [
        (DeepSeekAdapter(), {"api_key": "literal-key"}),
        (OpenAICompatAdapter(), {"endpoint": "https://proxy.test"}),
    ],
)
def test_adapters_reject_non_list_messages(adapter, cfg):
    with pytest.raises(TypeError, match="messages must be a list"):
        adapter.generate("not-a-list", cfg)


@pytest.mark.parametrize(
    "adapter,cfg",
    [
        (DeepSeekAdapter(), {"api_key": "literal-key"}),
        (OpenAICompatAdapter(), {"endpoint": "https://proxy.test"}),
    ],
)
def test_adapters_preserve_malformed_response_exceptions_and_usage(monkeypatch, adapter, cfg):
    clear_provider_env(monkeypatch)
    install_fake_client(monkeypatch, response=FakeResponse(data={"usage": {"prompt_tokens": 1}}))

    if isinstance(adapter, DeepSeekAdapter):
        with pytest.raises(RuntimeError, match="DeepSeek response did not include non-empty"):
            adapter.generate(MESSAGES, cfg)
    else:
        with pytest.raises(KeyError):
            adapter.generate(MESSAGES, cfg)

    assert adapter.last_usage == {"prompt_tokens": 1}


def test_llm_provider_strips_whitespace_adapter_result_to_empty(monkeypatch):
    class WhitespaceAdapter:
        last_usage = {"prompt_tokens": 1, "completion_tokens": 1}

        def generate(self, messages, model_cfg, *, request_options=None):
            return "   \n"

    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setattr(llm_provider, "_get_adapter", lambda provider_key: WhitespaceAdapter())

    provider = llm_provider.LlmProvider({"model": {"provider": "deepseek", "name": "deepseek-reasoner"}})

    assert provider.chat("prompt") == ""


def test_llm_provider_generate_from_messages_can_preserve_raw_output(monkeypatch, capsys):
    class RawAdapter:
        last_usage = {"prompt_tokens": 5, "completion_tokens": 8}

        def generate(self, messages, model_cfg, *, request_options=None):
            assert messages == MESSAGES
            assert model_cfg == {"provider": "deepseek", "name": "deepseek-v4-pro"}
            assert request_options is None
            return "  raw scene output\n"

    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setattr(llm_provider, "_get_adapter", lambda provider_key: RawAdapter())

    provider = llm_provider.LlmProvider({"model": {"provider": "deepseek", "name": "deepseek-v4-pro"}})

    assert provider.generate_from_messages(MESSAGES, strip_response=False) == "  raw scene output\n"
    captured = capsys.readouterr()
    assert "[DEBUG] [LLM] model=deepseek-v4-pro tokens_in=5 tokens_out=8" in captured.out


def test_llm_provider_generate_from_messages_strips_by_default(monkeypatch):
    class RawAdapter:
        last_usage = None

        def generate(self, messages, model_cfg, *, request_options=None):
            return "  rewrite output\n"

    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setattr(llm_provider, "_get_adapter", lambda provider_key: RawAdapter())

    provider = llm_provider.LlmProvider({"model": {"provider": "deepseek", "name": "deepseek-v4-pro"}})

    assert provider.generate_from_messages(MESSAGES) == "rewrite output"


def test_llm_provider_forwards_request_options_to_adapter(monkeypatch):
    class RecordingAdapter:
        last_usage = None

        def __init__(self) -> None:
            self.calls = []

        def generate(self, messages, model_cfg, *, request_options=None):
            self.calls.append({"messages": messages, "model_cfg": model_cfg, "request_options": request_options})
            return "{\"ok\": true}"

    monkeypatch.delenv("LLM_MOCK", raising=False)
    adapter = RecordingAdapter()
    monkeypatch.setattr(llm_provider, "_get_adapter", lambda provider_key: adapter)
    provider = llm_provider.LlmProvider({"model": {"provider": "deepseek", "name": "deepseek-v4-pro"}})
    options = {"response_format": {"type": "json_object"}, "thinking": {"type": "disabled"}}

    assert provider.generate_from_messages(MESSAGES, request_options=options) == "{\"ok\": true}"
    assert adapter.calls == [
        {
            "messages": MESSAGES,
            "model_cfg": {"provider": "deepseek", "name": "deepseek-v4-pro"},
            "request_options": options,
        }
    ]


def test_llm_provider_chat_and_markdown_are_message_wrappers(monkeypatch):
    class UnusedAdapter:
        last_usage = None

        def generate(self, messages, model_cfg, *, request_options=None):
            raise AssertionError("wrapper should call generate_from_messages")

    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setattr(llm_provider, "_get_adapter", lambda provider_key: UnusedAdapter())
    provider = llm_provider.LlmProvider(
        {
            "model": {"provider": "deepseek", "name": "deepseek-v4-pro"},
            "prompt_template": {"rewrite": "File={filename}; Existing={existing_content}; Tone={tone}"},
        }
    )
    calls = []

    def fake_generate_from_messages(messages, *, strip_response=True, request_options=None):
        calls.append({"messages": messages, "strip_response": strip_response, "request_options": request_options})
        return "wrapped"

    monkeypatch.setattr(provider, "generate_from_messages", fake_generate_from_messages)

    assert provider.chat("hello", system_instruction="system") == "wrapped"
    assert provider.generate_markdown(
        filename="note.md",
        path="vault/note.md",
        existing_non_image_text="  body  ",
        context={"tone": "noir"},
    ) == "wrapped"

    assert calls == [
        {
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "hello"},
            ],
            "strip_response": True,
            "request_options": None,
        },
        {
            "messages": [
                {"role": "system", "content": llm_provider.SYSTEM_PROMPT},
                {"role": "user", "content": "File=note.md; Existing=body; Tone=noir"},
            ],
            "strip_response": True,
            "request_options": None,
        },
    ]
