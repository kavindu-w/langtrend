"""Minimal OpenAI-compatible chat client for the LLM judge stage.

Works against any /chat/completions endpoint. Defaults target Cerebras' free
tier (open-weight gpt-oss-120b) via its OpenAI-compatible endpoint — check `GET /v1/models` if DEFAULT_MODEL ever starts 404ing. A local Ollama server is a drop-in
override for quota-free testing:

    LLM_JUDGE_BASE_URL=http://localhost:11434/v1
    LLM_JUDGE_MODEL=qwen3:8b
    LLM_JUDGE_API_KEY=ollama

Environment variables (all optional except the API key for hosted backends):
    LLM_JUDGE_BASE_URL           OpenAI-compatible base URL
    LLM_JUDGE_MODEL              model name
    LLM_JUDGE_API_KEY            bearer token (required unless server is keyless)
    LLM_JUDGE_TIMEOUT            per-request timeout in seconds (default 180)
    LLM_JUDGE_TEMPERATURE        sampling temperature (default 0)
    LLM_JUDGE_MAX_CONTEXT_CHARS  context assembly budget (default 12000)
    LLM_JUDGE_WORKERS            parallel paper workers (default 4)
    LLM_JUDGE_RPM                max requests per minute across all workers (default 4)
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

_PROJECT_ROOT = Path(__file__).parent.parent

DEFAULT_BASE_URL = "https://api.cerebras.ai/v1"
DEFAULT_MODEL = "gpt-oss-120b"

_CHAT_RETRIES = 3
_RETRY_BACKOFF = 2  # seconds; doubles each attempt


class LLMUnavailableError(Exception):
    """The endpoint could not be reached or rejected authentication."""


class QuotaExhaustedError(LLMUnavailableError):
    """The daily request/token quota looks exhausted — stop the run, don't retry.

    Distinct from a transient per-minute throttle (handled by the normal
    retry/backoff loop): this means waiting won't help until the provider's
    daily quota resets, so callers should stop cleanly and leave remaining
    work pending for the next scheduled run.
    """


class JSONParseError(Exception):
    """The model reply could not be parsed into a JSON object."""


_DAILY_QUOTA_MARKERS = ("per day", "rpd", "daily limit", "requests per day", "tokens per day")
_DAILY_QUOTA_RETRY_AFTER_THRESHOLD = 300  # seconds; longer than this implies a daily-reset wait, not per-minute


def _looks_like_daily_quota_exhausted(response_text: str, retry_after: str | None) -> bool:
    lowered = response_text.lower()
    if any(marker in lowered for marker in _DAILY_QUOTA_MARKERS):
        return True
    if retry_after:
        try:
            return float(retry_after) > _DAILY_QUOTA_RETRY_AFTER_THRESHOLD
        except ValueError:
            return False
    return False


@dataclass
class LLMClientConfig:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    timeout: int = 180
    temperature: float = 0.0
    max_context_chars: int = 12000
    workers: int = 4
    rpm: int = 4

    @classmethod
    def from_env(cls) -> "LLMClientConfig":
        import os

        try:
            from dotenv import load_dotenv

            load_dotenv(_PROJECT_ROOT / ".env")
        except ImportError:
            pass
        return cls(
            base_url=os.environ.get("LLM_JUDGE_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            api_key=os.environ.get("LLM_JUDGE_API_KEY", ""),
            model=os.environ.get("LLM_JUDGE_MODEL", DEFAULT_MODEL),
            timeout=int(os.environ.get("LLM_JUDGE_TIMEOUT", "180")),
            temperature=float(os.environ.get("LLM_JUDGE_TEMPERATURE", "0")),
            max_context_chars=int(os.environ.get("LLM_JUDGE_MAX_CONTEXT_CHARS", "12000")),
            workers=int(os.environ.get("LLM_JUDGE_WORKERS", "4")),
            rpm=int(os.environ.get("LLM_JUDGE_RPM", "4")),
        )

    def is_local(self) -> bool:
        return "localhost" in self.base_url or "127.0.0.1" in self.base_url


class _RpmThrottle:
    """Blocks callers so requests start evenly spaced, `60/rpm` seconds apart.

    Not a sliding-window counter: some providers (Cerebras included — see
    its own docs: "a rate limit of 60 RPM may be enforced as 1 request per
    second") reject bursts even when the total over any 60s window is under
    the limit. A counter that lets `rpm` requests fire back-to-back then
    goes quiet for the rest of the minute is compliant with the aggregate
    but not with that per-second enforcement — only strict even spacing is
    safe against both styles.
    """

    def __init__(self, rpm: int):
        self._min_interval = 60.0 / max(1, rpm)
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            start_at = max(now, self._next_allowed)
            self._next_allowed = start_at + self._min_interval
        sleep_for = start_at - now
        if sleep_for > 0:
            time.sleep(sleep_for)


class OpenAICompatClient:
    def __init__(self, config: LLMClientConfig):
        self.config = config
        self._throttle = _RpmThrottle(config.rpm)
        self._local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            if self.config.api_key:
                session.headers["Authorization"] = f"Bearer {self.config.api_key}"
            session.headers["Content-Type"] = "application/json"
            self._local.session = session
        return session

    def ping(self) -> None:
        """Fail fast if the endpoint is unreachable or the key is rejected."""
        url = f"{self.config.base_url}/models"
        try:
            resp = self._session().get(url, timeout=min(self.config.timeout, 30))
        except requests.RequestException as exc:
            hint = (
                "Is Ollama running? Try `ollama serve`."
                if self.config.is_local()
                else "Check LLM_JUDGE_BASE_URL and your network."
            )
            raise LLMUnavailableError(f"Cannot reach {url}: {exc}. {hint}") from exc
        if resp.status_code in (401, 403):
            raise LLMUnavailableError(
                f"Authentication failed at {url} (HTTP {resp.status_code}). "
                "Check LLM_JUDGE_API_KEY."
            )
        if resp.status_code >= 500:
            raise LLMUnavailableError(f"Endpoint error at {url}: HTTP {resp.status_code}")

    def chat(self, messages: list[dict], response_format_json: bool = True) -> str:
        """POST a chat completion; returns the assistant message content."""
        payload: dict = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.config.base_url}/chat/completions"
        last_error: Exception | None = None
        for attempt in range(1, _CHAT_RETRIES + 1):
            self._throttle.acquire()
            try:
                resp = self._session().post(url, json=payload, timeout=self.config.timeout)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(_RETRY_BACKOFF * (2 ** (attempt - 1)))
                continue

            if resp.status_code == 400 and response_format_json and "response_format" in resp.text:
                # Some servers (e.g. older Ollama) reject response_format — the
                # prompt-embedded schema is the real guarantee, so drop the hint.
                payload.pop("response_format", None)
                response_format_json = False
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                if resp.status_code == 429 and _looks_like_daily_quota_exhausted(resp.text, retry_after):
                    raise QuotaExhaustedError(
                        f"Daily quota likely exhausted (HTTP 429): {resp.text[:200]}"
                    )
                try:
                    delay = float(retry_after) if retry_after else _RETRY_BACKOFF * (2 ** (attempt - 1))
                except ValueError:
                    delay = _RETRY_BACKOFF * (2 ** (attempt - 1))
                last_error = requests.HTTPError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                time.sleep(min(delay, 120))
                continue
            resp.raise_for_status()

            data = resp.json()
            try:
                return data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise JSONParseError(f"Unexpected response shape: {json.dumps(data)[:300]}") from exc

        raise LLMUnavailableError(f"chat failed after {_CHAT_RETRIES} attempts: {last_error}")


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


def extract_json(text: str) -> dict:
    """Pull a JSON object out of a possibly-messy model reply."""
    cleaned = _THINK_RE.sub("", text)
    fenced = _FENCE_RE.search(cleaned)
    if fenced:
        cleaned = fenced.group(1)
    candidate = _first_balanced_object(cleaned)
    if candidate is None:
        raise JSONParseError(f"No JSON object found in reply: {text[:200]!r}")
    for attempt in (candidate, _TRAILING_COMMA_RE.sub(r"\1", candidate)):
        try:
            parsed = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise JSONParseError(f"Unparseable JSON object in reply: {candidate[:200]!r}")
