"""
Unit tests for scripts/send_digest.py and langtrend/buttondown.py.

Run with: pytest tests/test_send_digest.py -v
"""

import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import send_digest as sd
from langtrend.buttondown import API_VERSION, ButtondownClient, ButtondownError


@pytest.fixture
def manifest_path(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({
        "week_start": "2026-09-07",
        "week_end": "2026-09-14",
        "counts": {"papers": 1, "flagged_papers": 1, "unique_languages": 1},
        "flagged_papers": [{"paper": {"id": "http://arxiv.org/abs/1", "title": "T"}, "languages": [{"language": "Sinhala"}]}],
    }), encoding="utf-8")
    return path


class FakeClient:
    instances = []

    def __init__(self, api_key, existing=None, fail=False):
        self.api_key = api_key
        self.existing = existing
        self.fail = fail
        self.created = []
        FakeClient.instances.append(self)

    def find_email_by_subject(self, subject):
        if self.fail:
            raise ButtondownError("boom")
        return self.existing

    def create_email(self, subject, body, send=False):
        self.created.append((subject, body, send))
        return {"id": "em_1"}


def test_dry_run_appends_readme_stats(manifest_path, tmp_path, capsys):
    readme = tmp_path / "README.md"
    readme.write_text("x\n<!-- LANGTREND_STATS_START -->\n| Papers scanned | 1 | 401 |\n<!-- LANGTREND_STATS_END -->\n", encoding="utf-8")
    assert sd.run(_args(manifest_path, "dry-run", readme=readme)) == sd.EXIT_OK
    assert "| Papers scanned | 1 | 401 |" in capsys.readouterr().out


def test_missing_readme_still_renders(manifest_path, tmp_path, capsys):
    assert sd.run(_args(manifest_path, "dry-run", readme=tmp_path / "nope.md")) == sd.EXIT_OK
    captured = capsys.readouterr()
    assert "papers scanned" in captured.out and "no run summary" in captured.err


def test_digest_imports_need_only_the_standard_library():
    # CI's digest step runs with no pip install; langtrend/__init__ must keep
    # importing its heavier submodules lazily for that to work.
    code = (
        "import sys; import langtrend.digest, langtrend.buttondown; "
        "heavy = [m for m in ('requests', 'bs4', 'tqdm', 'langtrend.pdf_processor', 'langtrend.html_processor') if m in sys.modules]; "
        "assert not heavy, heavy"
    )
    subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, check=True)


def _args(manifest_path, mode, **kw):
    return sd.parse_args(["--manifest", str(manifest_path), "--mode", mode] + [a for k, v in kw.items() for a in (f"--{k}", str(v))])


# ---------------------------------------------------------------------------
# send_digest.run
# ---------------------------------------------------------------------------

def test_dry_run_prints_without_network(manifest_path, capsys, monkeypatch):
    monkeypatch.delenv("BUTTONDOWN_API_KEY", raising=False)
    assert sd.run(_args(manifest_path, "dry-run"), client_factory=None) == sd.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("Subject: LangTrend weekly digest: 7–14 Sep 2026")
    assert '{% if ",Sinhala," in subscriber.metadata.languages %}' in out


def test_dry_run_writes_output_file(manifest_path, tmp_path):
    out = tmp_path / "digest.md"
    assert sd.run(_args(manifest_path, "dry-run", output=out)) == sd.EXIT_OK
    assert "papers scanned" in out.read_text(encoding="utf-8")


def test_draft_requires_api_key(manifest_path, monkeypatch):
    monkeypatch.setenv("BUTTONDOWN_API_KEY", "  ")
    assert sd.run(_args(manifest_path, "draft"), client_factory=FakeClient) == sd.EXIT_NO_KEY


@pytest.mark.parametrize("mode,send", [("draft", False), ("send", True)])
def test_creates_email_when_none_exists(manifest_path, monkeypatch, mode, send):
    monkeypatch.setenv("BUTTONDOWN_API_KEY", "key")
    FakeClient.instances.clear()
    assert sd.run(_args(manifest_path, mode), client_factory=FakeClient) == sd.EXIT_OK
    (client,) = FakeClient.instances
    assert client.api_key == "key"
    assert [(s, snd) for s, _, snd in client.created] == [("LangTrend weekly digest: 7–14 Sep 2026", send)]


def test_skips_when_digest_already_exists(manifest_path, monkeypatch, capsys):
    monkeypatch.setenv("BUTTONDOWN_API_KEY", "key")
    FakeClient.instances.clear()
    factory = lambda key: FakeClient(key, existing={"status": "sent"})
    assert sd.run(_args(manifest_path, "send"), client_factory=factory) == sd.EXIT_OK
    assert FakeClient.instances[0].created == []
    assert "already exists (sent)" in capsys.readouterr().out


def test_api_error_returns_error_code(manifest_path, monkeypatch):
    monkeypatch.setenv("BUTTONDOWN_API_KEY", "key")
    assert sd.run(_args(manifest_path, "draft"), client_factory=lambda k: FakeClient(k, fail=True)) == sd.EXIT_ERROR


def test_manifest_without_week_is_an_error(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("{}", encoding="utf-8")
    assert sd.run(_args(path, "dry-run")) == sd.EXIT_ERROR


# ---------------------------------------------------------------------------
# ButtondownClient (HTTP layer stubbed via the opener hook)
# ---------------------------------------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RecordingOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return _Resp(json.dumps(nxt).encode("utf-8"))


def test_client_rejects_empty_key():
    with pytest.raises(ButtondownError):
        ButtondownClient("")


def test_find_email_follows_pagination():
    opener = RecordingOpener([
        {"results": [{"subject": "old"}], "next": "https://api.buttondown.com/v1/emails?page=2"},
        {"results": [{"subject": "wanted", "status": "draft"}], "next": None},
    ])
    client = ButtondownClient("k", opener=opener)
    assert client.find_email_by_subject("wanted") == {"subject": "wanted", "status": "draft"}
    assert opener.requests[1].full_url.endswith("page=2")
    headers = dict(opener.requests[0].header_items())
    assert headers["Authorization"] == "Token k"
    assert headers["X-api-version"] == API_VERSION


def test_find_email_returns_none_when_absent():
    client = ButtondownClient("k", opener=RecordingOpener([{"results": [], "next": None}]))
    assert client.find_email_by_subject("x") is None


def test_create_draft_does_not_send_live_header():
    opener = RecordingOpener([{"id": "em_1"}])
    ButtondownClient("k", opener=opener).create_email("S", "B")
    req = opener.requests[0]
    assert json.loads(req.data) == {"subject": "S", "body": "B", "status": "draft"}
    assert "X-buttondown-live-dangerously" not in dict(req.header_items())


def test_create_send_confirms_live_send():
    opener = RecordingOpener([{"id": "em_1"}])
    ButtondownClient("k", opener=opener).create_email("S", "B", send=True)
    req = opener.requests[0]
    assert json.loads(req.data)["status"] == "about_to_send"
    assert dict(req.header_items())["X-buttondown-live-dangerously"] == "true"


def test_http_error_is_wrapped():
    err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b'{"detail":"bad key"}'))
    client = ButtondownClient("k", opener=RecordingOpener([err]))
    with pytest.raises(ButtondownError, match="HTTP 401.*bad key"):
        client.create_email("S", "B")
