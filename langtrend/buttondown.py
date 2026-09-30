"""Minimal Buttondown API client for the weekly digest (stdlib only).

Only two operations are needed: look up whether this week's digest already
exists (so daily catch-up deploys don't create duplicates), and create it.
The client never lists or reads subscribers — the API key is used purely to
create emails, which keeps addresses out of CI logs and memory entirely.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

API_BASE = "https://api.buttondown.com/v1"
# Pins the 2026-04-01 semantics: POST /emails defaults to "draft", and
# status="about_to_send" needs the X-Buttondown-Live-Dangerously header.
API_VERSION = "2026-04-01"
_MAX_PAGES = 5


class ButtondownError(RuntimeError):
    pass


class ButtondownClient:
    def __init__(self, api_key: str, base_url: str = API_BASE, timeout: float = 30.0, opener=None):
        if not api_key:
            raise ButtondownError("BUTTONDOWN_API_KEY is empty")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._open = opener or urllib.request.urlopen

    def _request(self, method: str, url: str, payload: dict | None = None, extra_headers: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Token {self._api_key}",
            "X-API-Version": API_VERSION,
            "Accept": "application/json",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        headers.update(extra_headers or {})
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._open(req, timeout=self._timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise ButtondownError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ButtondownError(f"{method} {url} failed: {exc.reason}") from exc
        return json.loads(body) if body else {}

    def find_email_by_subject(self, subject: str) -> dict | None:
        """Return the most recent email with exactly this subject, any status."""
        for email in self._paginate(f"{self._base_url}/emails?ordering=-creation_date"):
            if email.get("subject") == subject:
                return email
        return None

    def _paginate(self, url: str | None):
        for _ in range(_MAX_PAGES):
            if not url:
                return
            page = self._request("GET", url)
            yield from page.get("results", [])
            url = page.get("next")

    def create_email(self, subject: str, body: str, send: bool = False) -> dict:
        payload = {"subject": subject, "body": body, "status": "about_to_send" if send else "draft"}
        extra = {"X-Buttondown-Live-Dangerously": "true"} if send else None
        return self._request("POST", f"{self._base_url}/emails", payload, extra)
