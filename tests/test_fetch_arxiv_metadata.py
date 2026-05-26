"""
Unit tests for scripts/fetch_arxiv_metadata.py.

All tests are offline — arXiv and OAI-PMH are fully mocked.

Run with:  pytest tests/test_fetch_arxiv_metadata.py -v
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import fetch_arxiv_metadata as fam


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_arxiv_result(paper_id="2501.00001", title="Test Paper"):
    r = SimpleNamespace()
    r.entry_id = f"https://arxiv.org/abs/{paper_id}"
    r.title = title
    r.summary = "An abstract."
    r.authors = [SimpleNamespace(name="Alice"), SimpleNamespace(name="Bob")]
    r.published = datetime(2025, 1, 1, 0, 0, 0)
    r.updated = datetime(2025, 1, 2, 0, 0, 0)
    r.categories = ["cs.CL"]
    r.pdf_url = f"https://arxiv.org/pdf/{paper_id}.pdf"
    return r


def _make_oai_xml(paper_id="2501.00001", created="2025-01-01", title="OAI Paper", resumption_token=None):
    token_block = (
        f"<resumptionToken>{resumption_token}</resumptionToken>"
        if resumption_token
        else "<resumptionToken/>"
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <ListRecords>
    <record>
      <header>
        <identifier>oai:arXiv.org:{paper_id}</identifier>
      </header>
      <metadata>
        <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
          <id>{paper_id}</id>
          <created>{created}</created>
          <title>{title}</title>
          <abstract>OAI abstract.</abstract>
          <authors>
            <author><forenames>Carol</forenames><keyname>Smith</keyname></author>
          </authors>
          <categories>cs.CL</categories>
        </arXiv>
      </metadata>
    </record>
    {token_block}
  </ListRecords>
</OAI-PMH>""".encode()


# ---------------------------------------------------------------------------
# _extract_category_tokens
# ---------------------------------------------------------------------------

def test_extract_category_tokens_single():
    assert fam._extract_category_tokens("cat:cs.CL") == ["cs.CL"]


def test_extract_category_tokens_multiple():
    tokens = fam._extract_category_tokens("cat:cs.CL OR cat:cs.AI")
    assert sorted(tokens) == ["cs.AI", "cs.CL"]


def test_extract_category_tokens_deduplication():
    tokens = fam._extract_category_tokens("cat:cs.CL AND cat:cs.CL")
    assert tokens == ["cs.CL"]


# ---------------------------------------------------------------------------
# _category_token_to_oai_set_spec
# ---------------------------------------------------------------------------

def test_oai_set_spec_top_level():
    assert fam._category_token_to_oai_set_spec("cs") == "cs"


def test_oai_set_spec_subcategory():
    assert fam._category_token_to_oai_set_spec("cs.CL") == "cs:cs:CL"


# ---------------------------------------------------------------------------
# _parse_oai_date
# ---------------------------------------------------------------------------

def test_parse_oai_date_iso():
    dt = fam._parse_oai_date("2025-01-15T00:00:00Z")
    assert dt == datetime(2025, 1, 15, 0, 0, 0)


def test_parse_oai_date_date_only():
    dt = fam._parse_oai_date("2025-01-15")
    assert dt == datetime(2025, 1, 15, 0, 0, 0)


def test_parse_oai_date_none():
    assert fam._parse_oai_date(None) is None


def test_parse_oai_date_invalid():
    assert fam._parse_oai_date("not-a-date") is None


# ---------------------------------------------------------------------------
# _parse_oai_record
# ---------------------------------------------------------------------------

def test_parse_oai_record_extracts_fields():
    import xml.etree.ElementTree as ET
    xml_bytes = _make_oai_xml("2501.00001", "2025-01-01", "My Title")
    root = ET.fromstring(xml_bytes)
    ns = {"oai": "http://www.openarchives.org/OAI/2.0/"}
    record = root.find(".//oai:record", ns)
    parsed = fam._parse_oai_record(record)
    assert parsed is not None
    assert parsed["id"] == "https://arxiv.org/abs/2501.00001"
    assert parsed["title"] == "My Title"
    assert "Carol Smith" in parsed["authors"]
    assert "_fetch_source" not in parsed  # set by caller, not by the parser itself


# ---------------------------------------------------------------------------
# fetch_and_save — happy path (arXiv API succeeds)
# ---------------------------------------------------------------------------

def test_fetch_and_save_arxiv_api_success(tmp_path):
    result = _make_arxiv_result("2501.00001", "Success Paper")
    mock_client = MagicMock()
    mock_client.results.return_value = iter([result])

    with patch("fetch_arxiv_metadata.arxiv.Client", return_value=mock_client):
        out = fam.fetch_and_save(
            end_date=datetime(2025, 1, 8),
            window_days=7,
            max_results=100,
            category_query="cat:cs.CL",
            output_dir=tmp_path,
        )

    records = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(records) == 1
    assert records[0]["title"] == "Success Paper"
    assert records[0]["_fetch_source"] == "arxiv_api"


# ---------------------------------------------------------------------------
# fetch_and_save — arXiv 429 retries then succeeds
# ---------------------------------------------------------------------------

def test_fetch_and_save_retries_on_429_then_succeeds(tmp_path):
    import arxiv as arxiv_lib
    result = _make_arxiv_result()
    mock_client = MagicMock()
    # Fail twice with 429, succeed on third attempt
    mock_client.results.side_effect = [
        arxiv_lib.HTTPError("url", 0, 429),
        arxiv_lib.HTTPError("url", 0, 429),
        iter([result]),
    ]

    with patch("fetch_arxiv_metadata.arxiv.Client", return_value=mock_client), \
         patch("fetch_arxiv_metadata.time.sleep"):
        out = fam.fetch_and_save(
            end_date=datetime(2025, 1, 8),
            window_days=7,
            max_results=100,
            category_query="cat:cs.CL",
            output_dir=tmp_path,
        )

    records = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert records[0]["_fetch_source"] == "arxiv_api"


# ---------------------------------------------------------------------------
# fetch_and_save — all arXiv attempts fail → OAI fallback
# ---------------------------------------------------------------------------

def test_fetch_and_save_falls_back_to_oai_on_persistent_429(tmp_path):
    import arxiv as arxiv_lib
    mock_client = MagicMock()
    mock_client.results.side_effect = arxiv_lib.HTTPError("url", 0, 429)

    oai_xml = _make_oai_xml("2501.00042", "2025-01-05", "OAI Fallback Paper")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = oai_xml

    with patch("fetch_arxiv_metadata.arxiv.Client", return_value=mock_client), \
         patch("fetch_arxiv_metadata.time.sleep"), \
         patch("fetch_arxiv_metadata.requests.get", return_value=mock_response):
        out = fam.fetch_and_save(
            end_date=datetime(2025, 1, 8),
            window_days=7,
            max_results=100,
            category_query="cat:cs.CL",
            output_dir=tmp_path,
        )

    records = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(records) == 1
    assert records[0]["title"] == "OAI Fallback Paper"
    assert records[0]["_fetch_source"] == "oai_pmh"


# ---------------------------------------------------------------------------
# fetch_and_save — OAI 429 triggers retry-after sleep
# ---------------------------------------------------------------------------

def test_oai_429_sleeps_then_retries(tmp_path):
    import arxiv as arxiv_lib
    mock_client = MagicMock()
    mock_client.results.side_effect = arxiv_lib.HTTPError("url", 0, 429)

    oai_xml = _make_oai_xml("2501.00099", "2025-01-05", "After 429")
    rate_limit_response = MagicMock()
    rate_limit_response.status_code = 429
    rate_limit_response.headers = {"Retry-After": "5"}

    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.content = oai_xml

    sleep_calls = []

    with patch("fetch_arxiv_metadata.arxiv.Client", return_value=mock_client), \
         patch("fetch_arxiv_metadata.time.sleep", side_effect=lambda s: sleep_calls.append(s)), \
         patch("fetch_arxiv_metadata.requests.get", side_effect=[rate_limit_response, ok_response]):
        out = fam.fetch_and_save(
            end_date=datetime(2025, 1, 8),
            window_days=7,
            max_results=100,
            category_query="cat:cs.CL",
            output_dir=tmp_path,
        )

    records = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert records[0]["_fetch_source"] == "oai_pmh"
    # At least one sleep call should have been made for the OAI 429
    assert any(s == 5 for s in sleep_calls)


# ---------------------------------------------------------------------------
# fetch_and_save — OAI pagination (resumption token)
# ---------------------------------------------------------------------------

def test_oai_pagination_follows_resumption_token(tmp_path):
    import arxiv as arxiv_lib
    mock_client = MagicMock()
    mock_client.results.side_effect = arxiv_lib.HTTPError("url", 0, 429)

    page1_xml = _make_oai_xml("2501.00001", "2025-01-05", "Page One", resumption_token="tok123")
    page2_xml = _make_oai_xml("2501.00002", "2025-01-06", "Page Two")

    page1_resp = MagicMock(status_code=200, content=page1_xml)
    page2_resp = MagicMock(status_code=200, content=page2_xml)

    with patch("fetch_arxiv_metadata.arxiv.Client", return_value=mock_client), \
         patch("fetch_arxiv_metadata.time.sleep"), \
         patch("fetch_arxiv_metadata.requests.get", side_effect=[page1_resp, page2_resp]):
        out = fam.fetch_and_save(
            end_date=datetime(2025, 1, 8),
            window_days=7,
            max_results=100,
            category_query="cat:cs.CL",
            output_dir=tmp_path,
        )

    records = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    titles = {r["title"] for r in records}
    assert "Page One" in titles
    assert "Page Two" in titles


# ---------------------------------------------------------------------------
# fetch_and_save — OAI date filtering excludes out-of-window papers
# ---------------------------------------------------------------------------

def test_oai_date_filter_excludes_out_of_window(tmp_path):
    import arxiv as arxiv_lib
    mock_client = MagicMock()
    mock_client.results.side_effect = arxiv_lib.HTTPError("url", 0, 429)

    # Paper created well before the window (2024-12-01, window is Jan 1–8 2025)
    oai_xml = _make_oai_xml("2412.00001", "2024-12-01", "Old Paper")
    mock_response = MagicMock(status_code=200, content=oai_xml)

    with patch("fetch_arxiv_metadata.arxiv.Client", return_value=mock_client), \
         patch("fetch_arxiv_metadata.time.sleep"), \
         patch("fetch_arxiv_metadata.requests.get", return_value=mock_response):
        out = fam.fetch_and_save(
            end_date=datetime(2025, 1, 8),
            window_days=7,
            max_results=100,
            category_query="cat:cs.CL",
            output_dir=tmp_path,
        )

    records = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert all(r["title"] != "Old Paper" for r in records)
