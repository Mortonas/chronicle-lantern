from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from config_io import load_app_config
import model_adapters
import adapters.model_adapter as legacy_adapter


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC_LEGACY_KEY = "legacy-test-secret-1234567890"
SYNTHETIC_KEY_FRAGMENTS = (
    SYNTHETIC_LEGACY_KEY,
    SYNTHETIC_LEGACY_KEY[:8],
    "legacy-test-secret",
    "1234567890",
)
_DEFAULT_RESPONSE_DATA = object()


class FakeResponse:
    def __init__(self, status_code=200, data=_DEFAULT_RESPONSE_DATA, text="OK", error=None):
        self.status_code = status_code
        self._data = (
            {"choices": [{"message": {"content": "legacy chat result"}}]}
            if data is _DEFAULT_RESPONSE_DATA
            else data
        )
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
    if hasattr(legacy_adapter, "httpx"):
        monkeypatch.setattr(
            legacy_adapter.httpx,
            "post",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("legacy /v1/query transport should not be called")
            ),
            raising=False,
        )


def install_fake_active_transport(monkeypatch, response=None, post_error=None):
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


def assert_no_synthetic_key_material(*texts):
    combined = "\n".join(str(text) for text in texts)
    leaked = any(fragment in combined for fragment in SYNTHETIC_KEY_FRAGMENTS)
    assert not leaked, "synthetic credential material leaked"


def assert_sanitized_deepseek_response_error(excinfo):
    text = str(excinfo.value)
    assert text.startswith("DeepSeek response did not include non-empty choices[0].message.content.")
    assert_no_synthetic_key_material(text)
    assert "Authorization" not in text
    assert "Bearer" not in text
    assert "raw-provider-payload" not in text


def test_legacy_module_imports_and_constructor_are_lightweight():
    assert legacy_adapter.ModelAdapter.__name__ == "ModelAdapter"
    assert legacy_adapter.DeepSeekAdapter.__name__ == "DeepSeekAdapter"
    assert legacy_adapter.DeepSeekAdapter.__module__ == "adapters.model_adapter"
    assert issubclass(legacy_adapter.DeepSeekAdapter, legacy_adapter.ModelAdapter)
    assert inspect.isabstract(legacy_adapter.ModelAdapter)
    assert not hasattr(legacy_adapter, "get_adapter")
    assert not hasattr(legacy_adapter.DeepSeekAdapter, "generate")

    adapter = legacy_adapter.DeepSeekAdapter(SYNTHETIC_LEGACY_KEY)

    assert adapter.api_key == SYNTHETIC_LEGACY_KEY
    assert not hasattr(adapter, "last_usage")
    assert not hasattr(legacy_adapter, "_LegacyDeepSeekAdapter")
    assert type(adapter._adapter) is model_adapters.DeepSeekAdapter
    assert "_resolve_api_key" not in legacy_adapter.DeepSeekAdapter.__dict__


def test_legacy_public_signatures_remain_compatible():
    model_query = inspect.signature(legacy_adapter.ModelAdapter.query)
    deepseek_init = inspect.signature(legacy_adapter.DeepSeekAdapter)
    deepseek_query = inspect.signature(legacy_adapter.DeepSeekAdapter.query)

    assert str(model_query) == "(self, prompt: str) -> str"
    assert str(deepseek_init) == "(api_key: str)"
    assert str(deepseek_query) == "(self, prompt: str) -> str"

    with pytest.raises(TypeError):
        legacy_adapter.ModelAdapter()
    with pytest.raises(TypeError):
        legacy_adapter.DeepSeekAdapter()


def test_legacy_query_translates_prompt_to_active_chat_completions(monkeypatch):
    current_model = legacy_adapter.LEGACY_DEFAULT_MODEL
    calls = install_fake_active_transport(
        monkeypatch,
        response=FakeResponse(
            data={"choices": [{"message": {"content": "  generated unchanged\n"}}]},
        ),
    )

    result = legacy_adapter.DeepSeekAdapter(SYNTHETIC_LEGACY_KEY).query("legacy prompt")

    assert result == "  generated unchanged\n"
    assert calls[0]["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert calls[0]["url"] != "https://api.deepseek.com/v1/query"
    assert calls[0]["json"] == {
        "model": current_model,
        "messages": [{"role": "user", "content": "legacy prompt"}],
        "stream": False,
    }
    assert calls[0]["headers"] == {"Authorization": f"Bearer {SYNTHETIC_LEGACY_KEY}"}
    assert calls[0]["timeout"].connect == 10.0
    assert calls[0]["timeout"].read == 180.0
    assert calls[0]["timeout"].write == 120.0
    assert calls[0]["timeout"].pool is None


def test_legacy_query_passes_non_string_prompt_as_user_message_content(monkeypatch):
    calls = install_fake_active_transport(monkeypatch)
    prompt = {"not": "a string"}

    assert legacy_adapter.DeepSeekAdapter(SYNTHETIC_LEGACY_KEY).query(prompt) == "legacy chat result"
    assert calls[0]["json"]["messages"] == [{"role": "user", "content": prompt}]


def test_legacy_query_missing_credential_uses_active_sanitized_failure(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key-must-not-be-used")
    calls = install_fake_active_transport(monkeypatch)

    with pytest.raises(RuntimeError, match="DeepSeek API key not provided"):
        legacy_adapter.DeepSeekAdapter("").query("prompt")

    assert calls == []


def test_legacy_query_treats_constructor_credential_as_literal(monkeypatch):
    monkeypatch.setenv("LEGACY_SHOULD_NOT_BE_READ", "environment-key-must-not-be-used")
    calls = install_fake_active_transport(monkeypatch)

    assert legacy_adapter.DeepSeekAdapter("env:LEGACY_SHOULD_NOT_BE_READ").query("prompt") == "legacy chat result"

    assert calls[0]["headers"] == {"Authorization": "Bearer env:LEGACY_SHOULD_NOT_BE_READ"}


@pytest.mark.parametrize(
    "data",
    [
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "   \n"}}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": ["not", "text"]}}]},
        {"choices": [{"message": "not-a-message-object"}]},
        {"choices": []},
        {"choices": "not-a-list"},
        {"error": {"message": f"raw-provider-payload {SYNTHETIC_LEGACY_KEY}"}},
        {},
        None,
    ],
)
def test_legacy_query_response_shape_failures_are_active_sanitized_errors(monkeypatch, data):
    install_fake_active_transport(
        monkeypatch,
        response=FakeResponse(data=data, text=f"raw-provider-payload {SYNTHETIC_LEGACY_KEY}"),
    )

    with pytest.raises(RuntimeError) as excinfo:
        legacy_adapter.DeepSeekAdapter(SYNTHETIC_LEGACY_KEY).query("prompt")

    assert_sanitized_deepseek_response_error(excinfo)


def test_legacy_query_http_failure_crosses_boundary_as_sanitized_runtime_error(monkeypatch, capsys):
    status_error = model_adapters.httpx.HTTPStatusError(
        f"status included {SYNTHETIC_LEGACY_KEY}",
        request=model_adapters.httpx.Request(
            "POST",
            "https://api.deepseek.com/v1/chat/completions",
        ),
        response=model_adapters.httpx.Response(500),
    )
    calls = install_fake_active_transport(
        monkeypatch,
        response=FakeResponse(
            status_code=500,
            text=f"provider echoed {SYNTHETIC_LEGACY_KEY}",
            error=status_error,
        ),
    )

    with pytest.raises(RuntimeError) as excinfo:
        legacy_adapter.DeepSeekAdapter(SYNTHETIC_LEGACY_KEY).query("prompt")

    captured = capsys.readouterr()
    assert calls[0]["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert str(excinfo.value) == "DeepSeek request failed."
    assert excinfo.value.__cause__ is None
    assert_no_synthetic_key_material(captured.out, captured.err, excinfo.value)


def test_legacy_query_provider_failure_crosses_boundary_as_sanitized_runtime_error(monkeypatch, capsys):
    post_error = model_adapters.httpx.TransportError(f"transport included {SYNTHETIC_LEGACY_KEY}")
    install_fake_active_transport(monkeypatch, post_error=post_error)

    with pytest.raises(RuntimeError) as excinfo:
        legacy_adapter.DeepSeekAdapter(SYNTHETIC_LEGACY_KEY).query("prompt")

    captured = capsys.readouterr()
    assert str(excinfo.value) == "DeepSeek request failed."
    assert excinfo.value.__cause__ is None
    assert_no_synthetic_key_material(captured.out, captured.err, excinfo.value)


def test_legacy_query_timeout_uses_active_timeout_error(monkeypatch, capsys):
    timeout = model_adapters.httpx.TimeoutException(f"timeout included {SYNTHETIC_LEGACY_KEY}")
    install_fake_active_transport(monkeypatch, post_error=timeout)

    with pytest.raises(RuntimeError) as excinfo:
        legacy_adapter.DeepSeekAdapter(SYNTHETIC_LEGACY_KEY).query("prompt")

    captured = capsys.readouterr()
    assert str(excinfo.value) == (
        "DeepSeek request timed out. Consider increasing config.model.timeout or reducing max_tokens."
    )
    assert_no_synthetic_key_material(captured.out, captured.err, excinfo.value)
