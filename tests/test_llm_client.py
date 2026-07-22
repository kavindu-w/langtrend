"""
Unit tests for langtrend/llm_client.py's payload building, per-model fallback
retry, and last_model_used tracking (no network — chat() is exercised via a
mocked requests.Session).

Run with: pytest tests/test_llm_client.py -v
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langtrend.llm_client import (
    LLMClientConfig,
    OpenAICompatClient,
    QuotaExhaustedError,
    LLMUnavailableError,
)


class _FakeResponse:
    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        pass


def _ok_response(model: str, content: str = "hi") -> _FakeResponse:
    body = {
        "model": model,
        "choices": [{"message": {"content": content}}],
    }
    return _FakeResponse(200, text=json.dumps(body))


def _error_response(status_code: int, text: str = "server error") -> _FakeResponse:
    return _FakeResponse(status_code, text=text)


def _config(**kwargs) -> LLMClientConfig:
    """LLMClientConfig with a high rpm/rph so tests don't hit the real
    throttle's sleep (default rpm=4 would add real 15s waits between calls)."""
    kwargs.setdefault("rpm", 1000)
    kwargs.setdefault("rph", 100000)
    return LLMClientConfig(**kwargs)


# ---------------------------------------------------------------------------
# chat() payload shape: always a single "model" key, never "models"
# ---------------------------------------------------------------------------

class TestChatPayloadShape:
    def _captured_payloads(self, config: LLMClientConfig, responses) -> list[dict]:
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session, patch("time.sleep"):
            mock_session.return_value.post.side_effect = responses
            client.chat([{"role": "user", "content": "hi"}])
        return [call.kwargs["json"] for call in mock_session.return_value.post.call_args_list]

    def test_no_fallback_configured_sends_model_key(self):
        config = _config(base_url="https://api.groq.com/openai/v1", model="openai/gpt-oss-120b")
        payloads = self._captured_payloads(config, [_ok_response("openai/gpt-oss-120b")])
        assert payloads[0]["model"] == "openai/gpt-oss-120b"
        assert "models" not in payloads[0]

    def test_fallback_models_never_produce_a_models_array(self):
        # Every attempt (including fallback ones) is a plain single-"model"
        # request — the OpenRouter-specific "models" array is never used,
        # so this works against any OpenAI-compatible provider.
        config = _config(
            base_url="https://api.cerebras.ai/v1",
            model="gpt-oss-120b",
            fallback_models=("llama-3.3-70b",),
        )
        payloads = self._captured_payloads(
            config,
            [_error_response(500), _error_response(500), _error_response(500),  # primary exhausts retries
             _ok_response("llama-3.3-70b")],  # fallback succeeds
        )
        assert all("models" not in p for p in payloads)
        assert [p["model"] for p in payloads] == ["gpt-oss-120b"] * 3 + ["llama-3.3-70b"]


# ---------------------------------------------------------------------------
# Per-model retry: each model in the chain gets its own _CHAT_RETRIES attempts
# ---------------------------------------------------------------------------

class TestPerModelFallbackRetry:
    def test_primary_success_never_touches_fallback(self):
        config = _config(model="primary", fallback_models=("fallback",))
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.post.return_value = _ok_response("primary")
            content = client.chat([{"role": "user", "content": "hi"}])
        assert content == "hi"
        assert mock_session.return_value.post.call_count == 1

    def test_primary_gets_full_retry_budget_before_falling_over(self):
        # 500 x3 on the primary (its whole _CHAT_RETRIES budget) before the
        # fallback model is ever tried — this is the behavior asked for:
        # 3 attempts per model, not 3 attempts total across the whole chain.
        config = _config(model="primary", fallback_models=("fallback",))
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session, patch("time.sleep"):
            mock_session.return_value.post.side_effect = [
                _error_response(500), _error_response(500), _error_response(500),
                _ok_response("fallback"),
            ]
            content = client.chat([{"role": "user", "content": "hi"}])
        assert content == "hi"
        assert client.last_model_used == "fallback"
        calls = mock_session.return_value.post.call_args_list
        assert [c.kwargs["json"]["model"] for c in calls] == ["primary", "primary", "primary", "fallback"]

    def test_malformed_response_retries_same_model_before_falling_over(self):
        # A malformed/unparseable response (e.g. missing "choices") must be
        # retried like any other transient failure — it should NOT skip
        # straight to the fallback model on the first occurrence.
        malformed = _FakeResponse(200, text=json.dumps({"no_choices_here": True}))
        config = _config(model="primary", fallback_models=("fallback",))
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session, patch("time.sleep"):
            mock_session.return_value.post.side_effect = [malformed, malformed, malformed, _ok_response("fallback")]
            content = client.chat([{"role": "user", "content": "hi"}])
        assert content == "hi"
        assert client.last_model_used == "fallback"
        calls = mock_session.return_value.post.call_args_list
        assert [c.kwargs["json"]["model"] for c in calls] == ["primary", "primary", "primary", "fallback"]

    def test_second_fallback_gets_its_own_retry_budget_too(self):
        config = _config(model="primary", fallback_models=("fb1", "fb2"))
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session, patch("time.sleep"):
            mock_session.return_value.post.side_effect = [
                _error_response(500), _error_response(500), _error_response(500),  # primary
                _error_response(500), _error_response(500), _error_response(500),  # fb1
                _ok_response("fb2"),
            ]
            content = client.chat([{"role": "user", "content": "hi"}])
        assert content == "hi"
        assert client.last_model_used == "fb2"
        calls = mock_session.return_value.post.call_args_list
        models_tried = [c.kwargs["json"]["model"] for c in calls]
        assert models_tried == ["primary"] * 3 + ["fb1"] * 3 + ["fb2"]

    def test_all_models_exhausted_raises_llm_unavailable(self):
        config = _config(model="primary", fallback_models=("fb1",))
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session, patch("time.sleep"):
            mock_session.return_value.post.side_effect = [_error_response(500)] * 6
            with pytest.raises(LLMUnavailableError):
                client.chat([{"role": "user", "content": "hi"}])
        assert mock_session.return_value.post.call_count == 6

    def test_quota_exhausted_on_non_final_model_falls_over_to_next_model(self):
        # Per-model quota (e.g. Groq's per-model TPD) exhausting on the
        # primary shouldn't stop the whole run if a fallback model still has
        # budget — only exhaustion on the LAST model in the chain should
        # propagate as QuotaExhaustedError.
        config = _config(model="primary", fallback_models=("fallback",))
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session, patch("time.sleep"):
            mock_session.return_value.post.side_effect = [
                _error_response(429, "Rate limit reached on requests per day (RPD): Limit 1000, Used 1000."),
                _ok_response("fallback"),
            ]
            content = client.chat([{"role": "user", "content": "hi"}])
        assert content == "hi"
        assert client.last_model_used == "fallback"

    def test_quota_exhausted_on_final_model_propagates(self):
        config = _config(model="primary")  # no fallback — primary is also the last model
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.post.return_value = _error_response(
                429, "Rate limit reached on requests per day (RPD): Limit 1000, Used 1000."
            )
            with pytest.raises(QuotaExhaustedError):
                client.chat([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# last_model_used
# ---------------------------------------------------------------------------

class TestLastModelUsed:
    def test_defaults_to_config_model_before_any_call(self):
        config = _config(model="openai/gpt-oss-120b")
        client = OpenAICompatClient(config)
        assert client.last_model_used == "openai/gpt-oss-120b"

    def test_updates_from_response_model_field_after_successful_call(self):
        config = _config(model="primary", fallback_models=("fallback",))
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session, patch("time.sleep"):
            mock_session.return_value.post.side_effect = [
                _error_response(500), _error_response(500), _error_response(500),
                _ok_response("fallback"),
            ]
            client.chat([{"role": "user", "content": "hi"}])
        assert client.last_model_used == "fallback"

    def test_tracks_the_most_recent_call_across_multiple_chat_invocations(self):
        config = _config(model="a")
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.post.side_effect = [_ok_response("model-1"), _ok_response("model-2")]
            client.chat([{"role": "user", "content": "first"}])
            assert client.last_model_used == "model-1"
            client.chat([{"role": "user", "content": "second"}])
            assert client.last_model_used == "model-2"

    def test_falls_back_to_attempted_model_when_response_omits_model_field(self):
        config = _config(model="fallback-default")
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            body = {"choices": [{"message": {"content": "hi"}}]}  # no "model" key
            mock_session.return_value.post.return_value = _FakeResponse(200, text=json.dumps(body))
            client.chat([{"role": "user", "content": "hi"}])
        assert client.last_model_used == "fallback-default"
