"""
Unit tests for langtrend/llm_client.py's payload building, OpenRouter fallback
gating, and last_model_used tracking (no network — chat() is exercised via a
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

from langtrend.llm_client import LLMClientConfig, OpenAICompatClient


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


def _config(**kwargs) -> LLMClientConfig:
    """LLMClientConfig with a high rpm/rph so tests don't hit the real
    throttle's sleep (default rpm=4 would add real 15s waits between calls)."""
    kwargs.setdefault("rpm", 1000)
    kwargs.setdefault("rph", 100000)
    return LLMClientConfig(**kwargs)


# ---------------------------------------------------------------------------
# LLMClientConfig.is_openrouter / is_local
# ---------------------------------------------------------------------------

class TestIsOpenrouter:
    def test_true_for_openrouter_base_url(self):
        config = _config(base_url="https://openrouter.ai/api/v1")
        assert config.is_openrouter() is True

    def test_false_for_groq(self):
        config = _config(base_url="https://api.groq.com/openai/v1")
        assert config.is_openrouter() is False

    def test_false_for_cerebras(self):
        config = _config(base_url="https://api.cerebras.ai/v1")
        assert config.is_openrouter() is False

    def test_false_for_local_ollama(self):
        config = _config(base_url="http://localhost:11434/v1")
        assert config.is_openrouter() is False


# ---------------------------------------------------------------------------
# chat() payload shape: "models" only sent for OpenRouter + fallback_models set
# ---------------------------------------------------------------------------

class TestChatPayloadShape:
    def _captured_payload(self, config: LLMClientConfig) -> dict:
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.post.return_value = _ok_response(config.model)
            client.chat([{"role": "user", "content": "hi"}])
        _, kwargs = mock_session.return_value.post.call_args
        return kwargs["json"]

    def test_no_fallback_configured_sends_model_key(self):
        config = _config(base_url="https://api.groq.com/openai/v1", model="openai/gpt-oss-120b")
        payload = self._captured_payload(config)
        assert payload["model"] == "openai/gpt-oss-120b"
        assert "models" not in payload

    def test_openrouter_with_fallback_sends_models_array_in_order(self):
        config = _config(
            base_url="https://openrouter.ai/api/v1",
            model="openai/gpt-oss-20b:free",
            fallback_models=("google/gemma-4-31b-it:free",),
        )
        payload = self._captured_payload(config)
        assert payload["models"] == ["openai/gpt-oss-20b:free", "google/gemma-4-31b-it:free"]
        assert "model" not in payload

    def test_non_openrouter_with_fallback_configured_falls_back_to_model_key(self):
        # This is the bug the code review caught: setting LLM_JUDGE_FALLBACK_MODELS
        # while pointed at a non-OpenRouter endpoint must NOT send "models" —
        # Cerebras/Groq/Ollama don't understand that field and would 400.
        config = _config(
            base_url="https://api.cerebras.ai/v1",
            model="gpt-oss-120b",
            fallback_models=("some-other-model",),
        )
        with pytest.warns(UserWarning, match="doesn't look like OpenRouter"):
            client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.post.return_value = _ok_response(config.model)
            client.chat([{"role": "user", "content": "hi"}])
        _, kwargs = mock_session.return_value.post.call_args
        payload = kwargs["json"]
        assert payload["model"] == "gpt-oss-120b"
        assert "models" not in payload

    def test_openrouter_without_fallback_configured_sends_model_key(self):
        config = _config(base_url="https://openrouter.ai/api/v1", model="openai/gpt-oss-20b:free")
        payload = self._captured_payload(config)
        assert payload["model"] == "openai/gpt-oss-20b:free"
        assert "models" not in payload

    def test_no_warning_when_fallback_configured_with_openrouter(self, recwarn):
        config = _config(
            base_url="https://openrouter.ai/api/v1",
            model="openai/gpt-oss-20b:free",
            fallback_models=("google/gemma-4-31b-it:free",),
        )
        OpenAICompatClient(config)
        assert len(recwarn) == 0


# ---------------------------------------------------------------------------
# last_model_used
# ---------------------------------------------------------------------------

class TestLastModelUsed:
    def test_defaults_to_config_model_before_any_call(self):
        config = _config(model="openai/gpt-oss-120b")
        client = OpenAICompatClient(config)
        assert client.last_model_used == "openai/gpt-oss-120b"

    def test_updates_from_response_model_field_after_successful_call(self):
        config = _config(
            base_url="https://openrouter.ai/api/v1",
            model="openai/gpt-oss-20b:free",
            fallback_models=("google/gemma-4-31b-it:free",),
        )
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            # OpenRouter served the request from the fallback, not the primary.
            mock_session.return_value.post.return_value = _ok_response("google/gemma-4-31b-it:free")
            client.chat([{"role": "user", "content": "hi"}])
        assert client.last_model_used == "google/gemma-4-31b-it:free"

    def test_tracks_the_most_recent_call_across_multiple_calls(self):
        config = _config(base_url="https://openrouter.ai/api/v1", model="a")
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.post.side_effect = [_ok_response("model-1"), _ok_response("model-2")]
            client.chat([{"role": "user", "content": "first"}])
            assert client.last_model_used == "model-1"
            client.chat([{"role": "user", "content": "second"}])
            assert client.last_model_used == "model-2"

    def test_falls_back_to_config_model_when_response_omits_model_field(self):
        config = _config(model="fallback-default")
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            body = {"choices": [{"message": {"content": "hi"}}]}  # no "model" key
            mock_session.return_value.post.return_value = _FakeResponse(200, text=json.dumps(body))
            client.chat([{"role": "user", "content": "hi"}])
        assert client.last_model_used == "fallback-default"
