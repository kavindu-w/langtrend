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
import requests

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langtrend.llm_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    AuthenticationError,
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
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error: {self.text}")


def _ok_response(model: str, content: str = "hi") -> _FakeResponse:
    body = {
        "model": model,
        "choices": [{"message": {"content": content}}],
    }
    return _FakeResponse(200, text=json.dumps(body))


def _error_response(status_code: int, text: str = "server error") -> _FakeResponse:
    return _FakeResponse(status_code, text=text)


def _models_response(*model_ids: str, status_code: int = 200) -> _FakeResponse:
    """A provider's GET /models reply in the standard OpenAI catalog shape."""
    body = {"object": "list", "data": [{"id": m, "object": "model"} for m in model_ids]}
    return _FakeResponse(status_code, text=json.dumps(body))


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

    def test_unhandled_status_falls_over_to_next_model_without_burning_retries(self):
        # A 404 (e.g. a retired/misconfigured model slug) won't fix itself on
        # retry — it should raise LLMUnavailableError immediately so chat()'s
        # fallback loop moves to the next model, rather than a bare HTTPError
        # escaping chat() entirely and aborting on the first bad model.
        config = _config(model="primary", fallback_models=("fallback",))
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session, patch("time.sleep"):
            mock_session.return_value.post.side_effect = [
                _error_response(404, "no endpoints found for primary"),
                _ok_response("fallback"),
            ]
            content = client.chat([{"role": "user", "content": "hi"}])
        assert content == "hi"
        assert client.last_model_used == "fallback"
        calls = mock_session.return_value.post.call_args_list
        assert [c.kwargs["json"]["model"] for c in calls] == ["primary", "fallback"]

    def test_unhandled_status_on_final_model_raises_llm_unavailable(self):
        config = _config(model="primary")
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.post.return_value = _error_response(404, "model not found")
            with pytest.raises(LLMUnavailableError):
                client.chat([{"role": "user", "content": "hi"}])

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


# ---------------------------------------------------------------------------
# LLMClientConfig.from_env: empty-string env vars must fall back to defaults,
# not become "" — this is what GitHub Actions does for an unset `vars.X`
# (${{ vars.X }} evaluates to "" rather than being omitted from `env:`).
# ---------------------------------------------------------------------------

class TestFromEnvEmptyStringFallback:
    def test_empty_base_url_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("LLM_JUDGE_BASE_URL", "")
        monkeypatch.delenv("LLM_JUDGE_MODEL", raising=False)
        config = LLMClientConfig.from_env()
        assert config.base_url == DEFAULT_BASE_URL

    def test_empty_model_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("LLM_JUDGE_MODEL", "")
        config = LLMClientConfig.from_env()
        assert config.model == DEFAULT_MODEL

    def test_nonempty_base_url_and_model_are_respected(self, monkeypatch):
        monkeypatch.setenv("LLM_JUDGE_BASE_URL", "https://openrouter.ai/api/v1")
        monkeypatch.setenv("LLM_JUDGE_MODEL", "openai/gpt-oss-20b:free")
        config = LLMClientConfig.from_env()
        assert config.base_url == "https://openrouter.ai/api/v1"
        assert config.model == "openai/gpt-oss-20b:free"

    def test_empty_numeric_vars_fall_back_to_defaults_instead_of_crashing(self, monkeypatch):
        # int(os.environ.get("LLM_JUDGE_RPM", "4")) would call int("") and
        # raise ValueError if the var is set-but-empty (same GitHub Actions
        # ${{ vars.X }} gotcha as base_url/model, but worse here — it crashes
        # the whole run instead of just misconfiguring it).
        for name in ("LLM_JUDGE_TIMEOUT", "LLM_JUDGE_TEMPERATURE", "LLM_JUDGE_MAX_CONTEXT_CHARS",
                     "LLM_JUDGE_WORKERS", "LLM_JUDGE_RPM", "LLM_JUDGE_RPH"):
            monkeypatch.setenv(name, "")
        config = LLMClientConfig.from_env()
        assert config.timeout == 180
        assert config.temperature == 0.0
        assert config.max_context_chars == 12000
        assert config.workers == 4
        assert config.rpm == 4
        assert config.rph == 150

    def test_nonempty_rpm_rph_are_respected(self, monkeypatch):
        monkeypatch.setenv("LLM_JUDGE_RPM", "20")
        monkeypatch.setenv("LLM_JUDGE_RPH", "1000")
        config = LLMClientConfig.from_env()
        assert config.rpm == 20
        assert config.rph == 1000


# ---------------------------------------------------------------------------
# ping(): endpoint/key checks, and the advisory model-catalog cross-check.
#
# The catalog check must never raise. A provider's GET /models is not an
# oracle for what /chat/completions accepts — it can be paginated, scoped to
# the key's grants, or spell IDs differently — so treating a "missing" model
# as fatal would break working, documented setups (Gemini and Ollama both
# below). The authority stays with the real chat() call.
# ---------------------------------------------------------------------------

class TestPing:
    def _ping_with(self, config: LLMClientConfig, response: _FakeResponse) -> None:
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.get.return_value = response
            client.ping()

    def test_unreachable_endpoint_raises_with_a_hint(self):
        config = _config(base_url="http://localhost:11434/v1")
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.get.side_effect = requests.ConnectionError("refused")
            with pytest.raises(LLMUnavailableError) as exc_info:
                client.ping()
        assert "ollama serve" in str(exc_info.value).lower()

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_key_raises_authentication_error(self, status):
        # AuthenticationError so chat() knows not to walk the fallback chain;
        # still an LLMUnavailableError so judge_languages.py's handler catches it.
        with pytest.raises(AuthenticationError) as exc_info:
            self._ping_with(_config(), _error_response(status, "bad key"))
        assert isinstance(exc_info.value, LLMUnavailableError)

    def test_server_error_raises(self):
        with pytest.raises(LLMUnavailableError):
            self._ping_with(_config(), _error_response(503))


class TestConfiguredModelCheck:
    """ping()'s /models cross-check: warn-only, and lenient about ID spelling."""

    # langtrend.yml and judge-catchup.yml grep judge_output.log for this exact
    # prefix to fire a "configured model missing" notification. If this
    # constant has to change, change those three workflow steps with it.
    CONFIG_WARNING_PREFIX = "  ERROR: [config]"

    def _ping_output(self, config: LLMClientConfig, response: _FakeResponse, capsys) -> str:
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.get.return_value = response
            client.ping()  # must not raise, whatever the catalog says
        return capsys.readouterr().out

    def test_warns_with_the_prefix_ci_greps_for_when_a_fallback_is_missing(self, capsys):
        config = _config(model="present", fallback_models=("retired",))
        out = self._ping_output(config, _models_response("present"), capsys)
        assert self.CONFIG_WARNING_PREFIX in out
        assert "retired" in out
        assert "present" not in out.replace("retired", "")

    def test_every_model_missing_warns_but_does_not_raise(self, capsys):
        # Regression guard: this used to raise LLMUnavailableError, which turned
        # an incomplete catalog into a failed judge job and a blocked deploy.
        config = _config(model="gone-a", fallback_models=("gone-b",))
        out = self._ping_output(config, _models_response("something-else"), capsys)
        assert self.CONFIG_WARNING_PREFIX in out
        assert "gone-a" in out and "gone-b" in out

    def test_no_warning_when_every_configured_model_is_listed(self, capsys):
        config = _config(model="a", fallback_models=("b",))
        out = self._ping_output(config, _models_response("a", "b", "c"), capsys)
        assert out.strip() == ""

    @pytest.mark.parametrize(
        "configured, listed",
        [
            # Gemini's OpenAI-compat endpoint lists "models/<name>" but chats on "<name>".
            ("gemini-2.5-flash", "models/gemini-2.5-flash"),
            # Ollama lists the resolved tag; "qwen3" is how you ask for it.
            ("qwen3", "qwen3:latest"),
            # Incidental case/whitespace differences.
            ("OpenAI/GPT-OSS-120B", "openai/gpt-oss-120b"),
        ],
    )
    def test_does_not_warn_about_equivalent_id_spellings(self, configured, listed, capsys):
        out = self._ping_output(_config(model=configured), _models_response(listed), capsys)
        assert out.strip() == ""

    @pytest.mark.parametrize(
        "body",
        [
            '{"models": [{"name": "x"}]}',   # non-OpenAI shape
            '{"data": []}',                   # empty catalog
            '{"data": ["x", "y"]}',           # list of bare strings, not objects
            '{"data": {"x": {}}}',            # dict where a list was expected
            'not json at all',                # unparseable body
        ],
    )
    def test_skips_silently_when_the_catalog_is_unusable(self, body, capsys):
        # No usable evidence => say nothing, rather than cry wolf every run.
        out = self._ping_output(_config(model="x"), _FakeResponse(200, text=body), capsys)
        assert out.strip() == ""


class TestAuthErrorShortCircuitsFallbackChain:
    def test_401_from_chat_does_not_try_the_remaining_models(self):
        # A rejected key is account-wide: walking the chain would spend one
        # throttled round-trip per model, per paper, to be rejected identically.
        config = _config(model="primary", fallback_models=("fb1", "fb2"))
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.post.return_value = _error_response(401, "invalid api key")
            with pytest.raises(AuthenticationError):
                client.chat([{"role": "user", "content": "hi"}])
        assert mock_session.return_value.post.call_count == 1

    def test_404_still_falls_over_to_the_next_model(self):
        # Contrast with the above: a bad slug is per-model, so the chain runs.
        config = _config(model="primary", fallback_models=("fb1",))
        client = OpenAICompatClient(config)
        with patch.object(OpenAICompatClient, "_session") as mock_session:
            mock_session.return_value.post.side_effect = [
                _error_response(404, "no endpoints found"),
                _ok_response("fb1"),
            ]
            assert client.chat([{"role": "user", "content": "hi"}]) == "hi"
        assert mock_session.return_value.post.call_count == 2
