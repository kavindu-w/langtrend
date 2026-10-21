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
import requests

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import fetch_arxiv_metadata as fam


def _direct_fetch_unavailable():
    """`_fetch_arxiv_direct` is the primary fetch path tried before `arxiv.Client`.

    All `fetch_and_save` tests below target the Client/OAI fallback logic, so this
    patches it to fail fast with no `.response` (non-transient — no retry sleep),
    skipping straight past it instead of making a real network call.
    """
    return patch("fetch_arxiv_metadata._fetch_arxiv_direct", side_effect=requests.HTTPError("direct API unavailable"))


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

    with _direct_fetch_unavailable(), \
         patch("fetch_arxiv_metadata.arxiv.Client", return_value=mock_client):
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

    with _direct_fetch_unavailable(), \
         patch("fetch_arxiv_metadata.arxiv.Client", return_value=mock_client), \
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

    with _direct_fetch_unavailable(), \
         patch("fetch_arxiv_metadata.arxiv.Client", return_value=mock_client), \
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

    with _direct_fetch_unavailable(), \
         patch("fetch_arxiv_metadata.arxiv.Client", return_value=mock_client), \
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

    with _direct_fetch_unavailable(), \
         patch("fetch_arxiv_metadata.arxiv.Client", return_value=mock_client), \
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

    with _direct_fetch_unavailable(), \
         patch("fetch_arxiv_metadata.arxiv.Client", return_value=mock_client), \
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
    assert len(records) == 0


# ---------------------------------------------------------------------------
# _fetch_arxiv_direct — the primary arXiv Atom API path (unit-level, not via
# fetch_and_save — all the tests above deliberately disable this path with
# _direct_fetch_unavailable() to reach the fallback code they're targeting).
# ---------------------------------------------------------------------------

_ATOM_NS = "http://www.w3.org/2005/Atom"
_OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"


def _make_atom_entry(paper_id="2501.00001", title="A Paper"):
    return f"""
    <entry>
      <id>http://arxiv.org/abs/{paper_id}</id>
      <title>{title}</title>
      <summary>An abstract.</summary>
      <author><name>Alice</name></author>
      <published>2025-01-01T00:00:00Z</published>
      <updated>2025-01-02T00:00:00Z</updated>
      <category term="cs.CL"/>
      <link href="http://arxiv.org/pdf/{paper_id}" type="application/pdf"/>
    </entry>
    """


def _make_atom_xml(entries_xml, total_results=None):
    total_xml = (
        f'<opensearch:totalResults xmlns:opensearch="{_OPENSEARCH_NS}">{total_results}</opensearch:totalResults>'
        if total_results is not None else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="{_ATOM_NS}">
      {total_xml}
      {entries_xml}
    </feed>""".encode()


def test_fetch_arxiv_direct_parses_entries_into_paper_dicts():
    xml = _make_atom_xml(_make_atom_entry("2501.00001", "Success Paper"), total_results=1)
    resp = MagicMock(content=xml)

    with patch("fetch_arxiv_metadata.requests.get", return_value=resp):
        papers = fam._fetch_arxiv_direct("cat:cs.CL", max_results=100)

    assert len(papers) == 1
    assert papers[0]["title"] == "Success Paper"
    assert papers[0]["id"] == "http://arxiv.org/abs/2501.00001"
    assert papers[0]["_fetch_source"] == "arxiv_direct"
    assert papers[0]["pdf_url"] == "http://arxiv.org/pdf/2501.00001"


def test_fetch_arxiv_direct_stops_when_first_page_returns_no_entries():
    resp = MagicMock(content=_make_atom_xml(""))
    with patch("fetch_arxiv_metadata.requests.get", return_value=resp) as mock_get:
        papers = fam._fetch_arxiv_direct("cat:cs.CL", max_results=100)

    assert papers == []
    mock_get.assert_called_once()


def test_fetch_arxiv_direct_raises_when_response_status_is_an_error():
    import requests as requests_lib
    resp = MagicMock()
    resp.raise_for_status.side_effect = requests_lib.HTTPError("500 server error")

    with patch("fetch_arxiv_metadata.requests.get", return_value=resp):
        with pytest.raises(requests_lib.HTTPError):
            fam._fetch_arxiv_direct("cat:cs.CL", max_results=100)


def test_fetch_arxiv_direct_paginates_across_multiple_requests(tmp_path):
    page1 = MagicMock(content=_make_atom_xml(
        _make_atom_entry("2501.00001", "Page One A") + _make_atom_entry("2501.00002", "Page One B"),
        total_results=3,
    ))
    page2 = MagicMock(content=_make_atom_xml(_make_atom_entry("2501.00003", "Page Two A"), total_results=3))

    with patch("fetch_arxiv_metadata._ARXIV_DIRECT_PAGE_SIZE", 2), \
         patch("fetch_arxiv_metadata.time.sleep") as mock_sleep, \
         patch("fetch_arxiv_metadata.requests.get", side_effect=[page1, page2]) as mock_get:
        papers = fam._fetch_arxiv_direct("cat:cs.CL", max_results=3)

    assert mock_get.call_count == 2
    assert [p["title"] for p in papers] == ["Page One A", "Page One B", "Page Two A"]
    mock_sleep.assert_called_once()  # one inter-page delay between the two requests


# ---------------------------------------------------------------------------
# Late-announced papers (submitted before the week, announced during it)
# ---------------------------------------------------------------------------

def _ids(start, stop, step=200, month=2609):
    """Dense run of IDs, like a real week (a few hundred apart at most)."""
    return [f"{month}.{n:05d}" for n in range(start, stop, step)]


def _write_week(directory, start, end, ids, late=()):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"arxiv_papers_{start}_to_{end}.jsonl"
    rows = [{"id": f"http://arxiv.org/abs/{i}v1"} for i in ids]
    rows += [{"id": f"http://arxiv.org/abs/{i}v1", "_late_announced": True} for i in late]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _ids_in(path):
    return [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines()]


def _meta(arxiv_id, published):
    return {"id": f"http://arxiv.org/abs/{arxiv_id}v1", "title": arxiv_id, "published": f"{published}T05:44:10Z",
            "categories": ["cs.CL"], "_fetch_source": "arxiv_direct"}


class _FakeArxiv:
    """Listing + id_list stand-ins; `published` maps ID -> v1 submission date."""

    def __init__(self, listed, published=None):
        self.listed = {k: {fam._id_key(i) for i in v} for k, v in listed.items()}
        self.published = published or {}
        self.requested = []
        self.listing_calls = []

    def listing(self, month, category):
        self.listing_calls.append((category, month))
        return self.listed.get(month, set())

    def metadata(self, ids):
        self.requested.append(list(ids))
        return [_meta(i, self.published[i]) for i in ids if i in self.published]

    def kwargs(self):
        return dict(listing_fetcher=self.listing, metadata_fetcher=self.metadata)


def _ranges(out):
    return fam._week_id_ranges(fam._load_weeks(out))


@pytest.fixture
def two_weeks(tmp_path):
    out = tmp_path / "meta"
    w1 = _write_week(out, "20260914", "20260921", _ids(14857, 23900) + ["2609.23951"])
    w2 = _write_week(out, "20260921", "20260928", _ids(23955, 34000) + ["2609.34033"])
    return out, w1, w2


def test_id_key_orders_by_month_then_sequence():
    assert fam._id_key("http://arxiv.org/abs/2609.29511v3") == (2609, 29511)
    assert fam._id_key("2609.34056") < fam._id_key("2610.00001")


def test_months_between_spans_year_end():
    assert fam._months_between((2612, 1), (2701, 5)) == ["2026-12", "2027-01"]


# --- ranges -----------------------------------------------------------------

def test_range_ignores_outlier_far_past_main_run(tmp_path):
    # A retroactively fetched week holds a paper filed by submission date far
    # ahead of its main run; it mustn't stretch the range (latest week: no cap).
    out = tmp_path / "meta"
    w1 = _write_week(out, "20260427", "20260504", _ids(100, 2000, month=2605) + ["2605.22834"])
    assert _ranges(out)[w1][1] == (2605, 1900)


def test_range_ends_before_next_weeks_first_id_despite_sparse_tail(tmp_path):
    # The real 27 Apr case: a sparse tail (gaps ~2000, under the outlier limit)
    # runs into the next week's IDs; the next week's first ID caps the range.
    out = tmp_path / "meta"
    w1 = _write_week(out, "20260427", "20260504",
                     _ids(23938, 30000, month=2604) + _ids(100, 2000, month=2605)
                     + ["2605.04000", "2605.06000", "2605.08149"])
    w2 = _write_week(out, "20260504", "20260511", _ids(2105, 9800, month=2605))
    ranges = _ranges(out)
    assert ranges[w1][1] == (2605, 2104)
    assert ranges[w2] == ((2605, 2104), (2605, 9705))


def test_range_cap_handles_outlier_in_the_following_month(tmp_path):
    out = tmp_path / "meta"
    w1 = _write_week(out, "20260921", "20260928", _ids(23955, 34000) + ["2610.05000"])
    _write_week(out, "20260928", "20261005", _ids(34056, 36000) + _ids(10, 5000, month=2610))
    assert _ranges(out)[w1][1] == (2609, 34055)


def test_late_batch_between_weeks_goes_to_the_week_it_was_announced(tmp_path):
    # The Labor Day case: a delayed batch with none of the next week's own papers
    # sits between the weeks' runs; it was announced in the later week.
    out = tmp_path / "meta"
    w1 = _write_week(out, "20260831", "20260907", _ids(100, 5405) + ["2609.05405"])
    w2 = _write_week(out, "20260907", "20260914", _ids(6880, 14800))
    ranges = _ranges(out)
    assert ranges[w1][1] == (2609, 5405)
    assert ranges[w2][0] == (2609, 5405)


def test_ranges_tile_when_main_runs_overlap(tmp_path):
    out = tmp_path / "meta"
    w1 = _write_week(out, "20260427", "20260504", _ids(100, 900, 100, month=2605))
    w2 = _write_week(out, "20260504", "20260511", _ids(200, 800, 100, month=2605))  # inside w1's run
    w3 = _write_week(out, "20260511", "20260518", ["2605.00900"])
    ranges = _ranges(out)
    assert ranges[w1] == ((2605, 99), (2605, 199))
    assert ranges[w2] == ((2605, 199), (2605, 700))
    assert ranges[w3] == ((2605, 700), (2605, 900))


def test_appended_late_rows_never_move_a_boundary(two_weeks):
    out, w1, w2 = two_weeks
    before = _ranges(out)
    _write_week(out, "20260921", "20260928", _ids(23955, 34000) + ["2609.34033"], late=["2609.20000"])
    assert _ranges(out) == before


def test_load_weeks_skips_other_window_lengths_and_bad_rows(two_weeks):
    out, w1, w2 = two_weeks
    _write_week(out, "20260801", "20260831", ["2609.99999"])  # a 30-day file
    with w1.open("a") as fh:
        fh.write(json.dumps({"title": "no id"}) + "\n")
    weeks = fam._load_weeks(out)
    assert [w.path for w in weeks] == [w1, w2]


def test_main_block_top_spans_month_boundary():
    keys = [(2609, n) for n in range(34056, 36000, 200)] + [(2610, n) for n in range(10, 5700, 200)]
    assert fam._main_block_top(keys) == (2610, 5610)


# --- add_late_announced --------------------------------------------------------

def test_add_late_appends_held_paper_to_its_announcement_week(two_weeks):
    # The EnSiTa case: submitted 25 Aug, held, announced in the 21–28 Sep week.
    out, _, w2 = two_weeks
    rows_before = w2.read_text().splitlines()
    fake = _FakeArxiv({"2026-09": ["2609.23955", "2609.29511", "2609.34033"]}, {"2609.29511": "2026-08-25"})
    assert fam.add_late_announced(w2, out, **fake.kwargs()) == 1
    rows_after = w2.read_text().splitlines()
    assert rows_after[:-1] == rows_before  # existing rows untouched
    appended = json.loads(rows_after[-1])
    assert appended["id"] == "http://arxiv.org/abs/2609.29511v1"
    assert appended["_late_announced"] is True
    assert fake.requested == [["2609.29511"]]  # 23955/34033 are already in w2


def test_add_late_claims_boundary_gap_for_the_later_week(two_weeks):
    out, w1, w2 = two_weeks
    fake = _FakeArxiv({"2026-09": ["2609.23953"]}, {"2609.23953": "2026-09-19"})
    assert fam.add_late_announced(w1, out, **fake.kwargs()) == 0
    assert fam.add_late_announced(w2, out, **fake.kwargs()) == 1


def test_add_late_leaves_papers_submitted_after_the_window_to_next_week(two_weeks):
    out, _, w2 = two_weeks
    fake = _FakeArxiv({"2026-09": ["2609.30001"]}, {"2609.30001": "2026-09-28"})
    before = _ids_in(w2)
    assert fam.add_late_announced(w2, out, **fake.kwargs()) == 0
    assert _ids_in(w2) == before


def test_add_late_files_next_weeks_unfetched_paper_under_next_week(tmp_path):
    # The 2606.01592 case: ID just past the next week's first ID, submitted in
    # that week's window, missing from its file — the next week's range owns it.
    out = tmp_path / "meta"
    w1 = _write_week(out, "20260525", "20260601", _ids(26165, 32000, month=2605) + _ids(10, 2628, month=2606))
    w2 = _write_week(out, "20260601", "20260608", _ids(1513, 11000, month=2606))
    fake = _FakeArxiv({"2026-06": ["2606.01592"]}, {"2606.01592": "2026-06-01"})
    assert fam.add_late_announced(w1, out, **fake.kwargs()) == 0
    assert fam.add_late_announced(w2, out, **fake.kwargs()) == 1
    assert _ids_in(w2)[-1] == "http://arxiv.org/abs/2606.01592v1"


def test_next_week_claims_its_paper_below_its_own_first_id(two_weeks):
    # Submitted in w2's window, announced in the boundary batch with an ID in
    # w1's range, missed by w2's regular fetch: w1 leaves it, w2 takes it.
    out, w1, w2 = two_weeks
    fake = _FakeArxiv({"2026-09": ["2609.23900"]}, {"2609.23900": "2026-09-21"})
    assert fam.add_late_announced(w1, out, **fake.kwargs()) == 0
    assert fam.add_late_announced(w2, out, **fake.kwargs()) == 1
    assert _ids_in(w2)[-1] == "http://arxiv.org/abs/2609.23900v1"
    # Order doesn't matter: once owned, the earlier week never re-adds it.
    assert fam.add_late_announced(w1, out, **fake.kwargs()) == 0


def test_next_week_leaves_previous_ranges_own_late_papers_alone(two_weeks):
    # In w1's range and submitted before w1 ended: w1's, not w2's.
    out, w1, w2 = two_weeks
    fake = _FakeArxiv({"2026-09": ["2609.23900"]}, {"2609.23900": "2026-09-01"})
    assert fam.add_late_announced(w2, out, **fake.kwargs()) == 0
    assert fam.add_late_announced(w1, out, **fake.kwargs()) == 1


def test_add_late_never_duplicates_a_paper_from_another_week(two_weeks):
    out, _, w2 = two_weeks
    _write_week(out, "20260928", "20261005", _ids(34056, 36000), late=["2609.26000"])
    fake = _FakeArxiv({"2026-09": ["2609.24155", "2609.26000"]}, {"2609.26000": "2026-09-01"})
    assert fam.add_late_announced(w2, out, **fake.kwargs()) == 0
    assert fake.requested == []  # 24155 is already in w2, 26000 in the later week


def test_add_late_is_idempotent(two_weeks):
    out, _, w2 = two_weeks
    fake = _FakeArxiv({"2026-09": ["2609.29511"]}, {"2609.29511": "2026-08-25"})
    assert fam.add_late_announced(w2, out, **fake.kwargs()) == 1
    assert fam.add_late_announced(w2, out, **fake.kwargs()) == 0


def test_add_late_reads_every_category_and_reuses_cache(two_weeks):
    out, w1, w2 = two_weeks
    fake = _FakeArxiv({"2026-09": []})
    cache = {}
    for week in (w1, w2):
        fam.add_late_announced(week, out, categories=("cs.CL", "cs.AI"), listing_cache=cache, **fake.kwargs())
    assert sorted(fake.listing_calls) == [("cs.AI", "2026-09"), ("cs.CL", "2026-09")]


# --- network helpers -------------------------------------------------------------

class _Resp:
    def __init__(self, text="", status_code=200, content=b""):
        self.text = text
        self.status_code = status_code
        self.content = content or text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _listing_page(ids, total):
    entries = "".join(
        f'<dt><a href ="/abs/{i}" title="Abstract" id="{i}">arXiv:{i}</a></dt>'
        f"<dd>Comments: extends arXiv:2608.12345</dd>" for i in ids)
    return f"<h3>Total of {total:,} entries</h3>{entries}"


def _feed(*entry_ids):
    entries = "".join(f"<entry><id>{i}</id><title>T</title><published>2026-09-01T00:00:00Z</published></entry>"
                      for i in entry_ids)
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode()


def test_fetch_listing_ids_paginates_and_ignores_mentioned_ids(monkeypatch):
    monkeypatch.setattr(fam, "_LISTING_PAGE_SIZE", 2)
    pages = {0: _listing_page(["2609.00001", "2609.00002"], 3), 2: _listing_page(["2609.00003"], 3)}
    seen = []

    def fake_get(url, params, headers, timeout):
        seen.append(params["skip"])
        return _Resp(pages[params["skip"]])

    ids = fam._fetch_listing_ids("2026-09", get=fake_get, sleep=lambda s: None)
    assert seen == [0, 2]
    assert ids == {(2609, 1), (2609, 2), (2609, 3)}


def test_fetch_listing_ids_retries_throttling_and_network_errors():
    responses = iter([_Resp(status_code=403), requests.exceptions.ConnectionError("reset"),
                      _Resp(_listing_page(["2609.00001"], 1))])

    def fake_get(*a, **k):
        r = next(responses)
        if isinstance(r, Exception):
            raise r
        return r

    assert fam._fetch_listing_ids("2026-09", get=fake_get, sleep=lambda s: None) == {(2609, 1)}


def test_fetch_listing_ids_raises_after_last_attempt():
    with pytest.raises(requests.HTTPError):
        fam._fetch_listing_ids("2026-09", get=lambda *a, **k: _Resp(status_code=429), sleep=lambda s: None)


def test_fetch_arxiv_by_ids_batches_retries_and_drops_error_entries(monkeypatch):
    # Batch 1: a timeout, then 00001 plus an error entry for 00002 (dropped,
    # then tried on OAI, which doesn't know it either). Batch 2: 00003.
    monkeypatch.setattr(fam, "_ID_LIST_BATCH", 2)
    responses = iter([
        requests.exceptions.Timeout("slow"),
        _Resp(content=_feed("http://arxiv.org/abs/2609.00001v1", "http://arxiv.org/api/errors#incorrect_id_format")),
        _Resp(content=_feed("http://arxiv.org/abs/2609.00003v1")),
    ])
    calls = []

    def fake_get(url, params, headers, timeout):
        if "oaipmh" in url:
            calls.append("oai:" + params["identifier"].rsplit(":", 1)[-1])
            return _Resp(content=b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="idDoesNotExist"/></OAI-PMH>')
        calls.append(params["id_list"])
        r = next(responses)
        if isinstance(r, Exception):
            raise r
        return r

    papers = fam._fetch_arxiv_by_ids(["2609.00001", "2609.00002", "2609.00003"], get=fake_get, sleep=lambda s: None)
    assert calls == ["2609.00001,2609.00002", "2609.00001,2609.00002", "oai:2609.00002", "2609.00003"]
    assert [p["id"] for p in papers] == ["http://arxiv.org/abs/2609.00001v1", "http://arxiv.org/abs/2609.00003v1"]


# --- main() ------------------------------------------------------------------------

def test_main_add_late_and_no_add_late_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        fam.main(["--add-late", "--no-add-late", "--output-dir", str(tmp_path)])


def test_main_add_late_errors_when_week_missing(tmp_path):
    with pytest.raises(SystemExit):
        fam.main(["--add-late", "--end-date", "2026-09-28", "--output-dir", str(tmp_path)])


def test_main_add_late_runs_without_fetching(tmp_path):
    week = _write_week(tmp_path, "20260921", "20260928", ["2609.23955"])
    with patch.object(fam, "fetch_and_save") as fetch, patch.object(fam, "add_late_announced") as late:
        fam.main(["--add-late", "--end-date", "2026-09-28", "--output-dir", str(tmp_path),
                  "--category", "cat:cs.CL OR cat:cs.AI"])
    fetch.assert_not_called()
    late.assert_called_once_with(week, output_dir=tmp_path, categories=("cs.AI", "cs.CL"), window_days=7)


def test_main_late_failure_is_logged_with_marker_not_raised(tmp_path, caplog):
    week = tmp_path / "arxiv_papers_20260921_to_20260928.jsonl"
    with patch.object(fam, "fetch_and_save", return_value=week), \
         patch.object(fam, "add_late_announced", side_effect=requests.HTTPError("HTTP 403")):
        fam.main(["--end-date", "2026-09-28", "--output-dir", str(tmp_path)])
    assert fam.LATE_FAILURE_MARKER in caplog.text
    assert "--add-late --end-date 2026-09-28" in caplog.text


def test_main_no_add_late_skips_the_step(tmp_path):
    with patch.object(fam, "fetch_and_save", return_value=tmp_path / "x.jsonl"), \
         patch.object(fam, "add_late_announced") as late:
        fam.main(["--end-date", "2026-09-28", "--output-dir", str(tmp_path), "--no-add-late"])
    late.assert_not_called()


def _oai_record_xml(arxiv_id, created="2026-08-25"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><GetRecord><record>
<header><identifier>oai:arXiv.org:{arxiv_id}</identifier></header>
<metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/"><id>{arxiv_id}</id><created>{created}</created>
<title>T {arxiv_id}</title><abstract>A.</abstract><categories>cs.CL</categories></arXiv></metadata>
</record></GetRecord></OAI-PMH>""".encode()


def test_fetch_arxiv_by_ids_falls_back_to_oai_when_id_list_stays_throttled(monkeypatch):
    # The search API 429s every attempt (per-IP throttle); OAI-PMH answers.
    monkeypatch.setattr(fam, "_ID_LIST_BATCH", 1)
    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append(url)
        if "export.arxiv.org" in url:
            return _Resp(status_code=429)
        if params["identifier"].endswith("2609.00002"):
            return _Resp(content=b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="idDoesNotExist"/></OAI-PMH>')
        return _Resp(content=_oai_record_xml(params["identifier"].rsplit(":", 1)[-1]))

    papers = fam._fetch_arxiv_by_ids(["2609.00001", "2609.00002", "2609.00003"], get=fake_get, sleep=lambda s: None)
    assert [p["id"] for p in papers] == ["https://arxiv.org/abs/2609.00001", "https://arxiv.org/abs/2609.00003"]
    assert papers[0]["published"].startswith("2026-08-25") and papers[0]["_fetch_source"] == "oai_pmh"
    # Gave up on the search API after the first batch's retries, not per batch.
    assert sum("export.arxiv.org" in u for u in calls) == fam._FETCH_ATTEMPTS


# --- newest week's missed boundary batch / re-fetch dedupe / partial failures ------

def test_newest_week_claims_its_own_batch_above_its_range(two_weeks):
    # The 5–12 Oct case: the Tuesday fetch missed the Monday-night batch of the
    # week's own weekend papers (IDs above its top); listed, submitted inside
    # the window -> claimed now, before the week's digest.
    out, w1, w2 = two_weeks
    fake = _FakeArxiv({"2026-09": ["2609.34100", "2609.34101"]},
                      {"2609.34100": "2026-09-26", "2609.34101": "2026-09-28"})
    assert fam.add_late_announced(w2, out, **fake.kwargs(), claim_boundary_batch=True) == 1
    assert _ids_in(w2)[-1] == "http://arxiv.org/abs/2609.34100v1"  # 34101 is next week's


def test_older_week_never_claims_above_its_range(two_weeks):
    # Same papers, but w1 isn't the newest: IDs above its range belong to w2's
    # range now, so re-running w1 must not move them back.
    out, w1, w2 = two_weeks
    fake = _FakeArxiv({"2026-09": ["2609.30000"]}, {"2609.30000": "2026-09-20"})
    assert fam.add_late_announced(w1, out, **fake.kwargs()) == 0
    assert fake.requested == []


def test_above_range_claim_is_capped(two_weeks, monkeypatch):
    monkeypatch.setattr(fam, "_ABOVE_RANGE_LIMIT", 2)
    out, _, w2 = two_weeks
    listed = ["2609.34100", "2609.34101", "2609.34102"]
    fake = _FakeArxiv({"2026-09": listed}, {i: "2026-09-26" for i in listed})
    assert fam.add_late_announced(w2, out, **fake.kwargs(), claim_boundary_batch=True) == 2
    assert fake.requested == [["2609.34100", "2609.34101"]]


def test_fetch_and_save_drops_papers_filed_under_another_week(two_weeks):
    # Re-fetching w1 by submission date returns a held paper that w2's late
    # step already filed (announced in w2) — it mustn't land in both.
    out, w1, w2 = two_weeks
    _write_week(out, "20260921", "20260928", _ids(23955, 34000), late=["2609.29511"])
    papers = [_meta("2609.15000", "2026-09-15"), _meta("2609.29511", "2026-09-16")]
    with patch.object(fam, "_fetch_arxiv_direct", return_value=papers):
        path = fam.fetch_and_save(datetime(2026, 9, 21), 7, 1000, "cat:cs.CL", out)
    assert path == w1
    assert _ids_in(w1) == ["http://arxiv.org/abs/2609.15000v1"]


def test_fetch_arxiv_by_ids_keeps_api_batches_then_switches_to_oai(monkeypatch):
    # Batch 1 via the API, batch 2 throttled -> OAI for it and everything after.
    monkeypatch.setattr(fam, "_ID_LIST_BATCH", 1)
    api_calls = []

    def fake_get(url, params, headers, timeout):
        if "export.arxiv.org" in url:
            api_calls.append(params["id_list"])
            if params["id_list"] == "2609.00001":
                return _Resp(content=_feed("http://arxiv.org/abs/2609.00001v1"))
            return _Resp(status_code=429)
        return _Resp(content=_oai_record_xml(params["identifier"].rsplit(":", 1)[-1]))

    papers = fam._fetch_arxiv_by_ids(["2609.00001", "2609.00002", "2609.00003"], get=fake_get, sleep=lambda s: None)
    assert [p["_fetch_source"] for p in papers] == ["arxiv_direct", "oai_pmh", "oai_pmh"]
    assert api_calls == ["2609.00001"] + ["2609.00002"] * fam._FETCH_ATTEMPTS


def test_fetch_oai_by_ids_skips_an_id_that_keeps_failing():
    def fake_get(url, params, headers, timeout):
        if params["identifier"].endswith("2609.00002"):
            raise requests.exceptions.ConnectionError("reset")
        return _Resp(content=_oai_record_xml(params["identifier"].rsplit(":", 1)[-1]))

    papers = fam._fetch_oai_by_ids(["2609.00001", "2609.00002", "2609.00003"], get=fake_get, sleep=lambda s: None)
    assert [p["id"] for p in papers] == ["https://arxiv.org/abs/2609.00001", "https://arxiv.org/abs/2609.00003"]


def test_fetch_arxiv_by_ids_gets_ids_the_api_left_out_from_oai():
    # Not yet indexed: the search API answers 200 but without these papers —
    # exactly when the newest week's boundary-batch repair runs.
    def fake_get(url, params, headers, timeout):
        if "export.arxiv.org" in url:
            return _Resp(content=_feed("http://arxiv.org/abs/2610.14112v1"))
        return _Resp(content=_oai_record_xml(params["identifier"].rsplit(":", 1)[-1], created="2026-10-10"))

    papers = fam._fetch_arxiv_by_ids(["2610.14112", "2610.14113"], get=fake_get, sleep=lambda s: None)
    assert [(p["id"], p["_fetch_source"]) for p in papers] == [
        ("http://arxiv.org/abs/2610.14112v1", "arxiv_direct"), ("https://arxiv.org/abs/2610.14113", "oai_pmh")]


def test_add_late_logs_failure_marker_when_papers_stay_unfetched(two_weeks, caplog):
    # Nothing retries a shortfall automatically, so it must reach CI's grep —
    # after writing what did come back.
    out, _, w2 = two_weeks
    fake = _FakeArxiv({"2026-09": ["2609.29511", "2609.29600"]}, {"2609.29511": "2026-08-25"})
    assert fam.add_late_announced(w2, out, **fake.kwargs()) == 1
    assert _ids_in(w2)[-1] == "http://arxiv.org/abs/2609.29511v1"
    assert fam.LATE_FAILURE_MARKER in caplog.text and "2609.29600" in caplog.text


def test_add_late_no_marker_when_everything_came_back(two_weeks, caplog):
    out, _, w2 = two_weeks
    fake = _FakeArxiv({"2026-09": ["2609.29511"]}, {"2609.29511": "2026-08-25"})
    fam.add_late_announced(w2, out, **fake.kwargs())
    assert fam.LATE_FAILURE_MARKER not in caplog.text


def test_above_range_cap_warns(two_weeks, monkeypatch, caplog):
    monkeypatch.setattr(fam, "_ABOVE_RANGE_LIMIT", 1)
    out, _, w2 = two_weeks
    fake = _FakeArxiv({"2026-09": ["2609.34100", "2609.34101"]}, {"2609.34100": "2026-09-26"})
    fam.add_late_announced(w2, out, **fake.kwargs(), claim_boundary_batch=True)
    assert "checking only the first 1" in caplog.text


def test_unfetched_above_range_ids_warn_but_dont_raise_the_alert(two_weeks, caplog):
    # 34101 sits above the newest week's range with no metadata from either
    # source — most likely next week's paper, so no CI alert, just a warning.
    out, _, w2 = two_weeks
    with w2.open("a") as fh:  # a normal week: its weekend batch is there
        fh.write(json.dumps({"id": "http://arxiv.org/abs/2609.33990v1", "published": "2026-09-27T10:00:00Z"}) + "\n")
    fake = _FakeArxiv({"2026-09": ["2609.34100", "2609.34101"]}, {"2609.34100": "2026-09-26"})
    assert fam.add_late_announced(w2, out, **fake.kwargs(), claim_boundary_batch=True) == 1
    assert fam.LATE_FAILURE_MARKER not in caplog.text
    assert "above this week's range" in caplog.text and "2609.34101" in caplog.text


def test_unfetched_above_range_ids_with_weekend_missing_warn_not_alert(tmp_path, caplog):
    # The 5–12 Oct signature: no regular papers from the window's last two
    # days, and the batch above the range can't be fetched yet. Next week
    # filing it is the intended fallback and nothing could fix it now, so a
    # specific warning, not the CI alert.
    out = tmp_path / "meta"
    week = out / "arxiv_papers_20261005_to_20261012.jsonl"
    out.mkdir()
    week.write_text("".join(json.dumps({"id": f"http://arxiv.org/abs/2610.{n:05d}v1", "published": "2026-10-09T12:00:00Z"}) + "\n"
                            for n in range(5681, 13912, 200)) + json.dumps({"id": "http://arxiv.org/abs/2610.13912v1",
                            "published": "2026-10-09T13:00:00Z"}) + "\n", encoding="utf-8")
    fake = _FakeArxiv({"2026-10": ["2610.14112"]})
    fam.add_late_announced(week, out, **fake.kwargs(), claim_boundary_batch=True)
    assert fam.LATE_FAILURE_MARKER not in caplog.text
    assert "likely its weekend batch; next week will file them" in caplog.text


def test_weekend_missing_reads_only_regular_rows(tmp_path):
    week = tmp_path / "arxiv_papers_20261005_to_20261012.jsonl"
    week.write_text(json.dumps({"id": "2610.00001", "published": "2026-10-10T01:00:00Z", "_late_announced": True}) + "\n"
                    + json.dumps({"id": "2610.00002", "published": "2026-10-09T01:00:00Z"}) + "\n", encoding="utf-8")
    assert fam._weekend_missing(week, datetime(2026, 10, 12)) is True
    week.write_text(json.dumps({"id": "2610.00003", "published": "2026-10-11T23:00:00Z"}) + "\n", encoding="utf-8")
    assert fam._weekend_missing(week, datetime(2026, 10, 12)) is False


def test_later_add_late_run_never_claims_above_the_range(two_weeks):
    # Same listing as the newest-week claim test, but no fetch run (a manual
    # --add-late, e.g. after a holiday batch came out): left to next week.
    out, _, w2 = two_weeks
    fake = _FakeArxiv({"2026-09": ["2609.34100"]}, {"2609.34100": "2026-09-26"})
    assert fam.add_late_announced(w2, out, **fake.kwargs()) == 0
    assert fake.requested == []


def test_newest_week_claim_reads_the_following_month(two_weeks):
    out, _, w2 = two_weeks
    fake = _FakeArxiv({"2026-09": []})
    fam.add_late_announced(w2, out, **fake.kwargs(), claim_boundary_batch=True)
    assert sorted(fake.listing_calls) == [("cs.CL", "2026-09"), ("cs.CL", "2026-10")]


def test_main_fetch_run_claims_boundary_batch_but_add_late_does_not(tmp_path):
    week = tmp_path / "arxiv_papers_20260921_to_20260928.jsonl"

    def first_fetch(**kw):
        return _write_week(tmp_path, "20260921", "20260928", ["2609.23955"])

    with patch.object(fam, "fetch_and_save", side_effect=first_fetch), patch.object(fam, "add_late_announced") as late:
        fam.main(["--end-date", "2026-09-28", "--output-dir", str(tmp_path)])
    assert late.call_args.kwargs["claim_boundary_batch"] is True
    # A re-fetch of a week that already had a file (e.g. days later) doesn't claim.
    with patch.object(fam, "fetch_and_save", return_value=week), patch.object(fam, "add_late_announced") as late:
        fam.main(["--end-date", "2026-09-28", "--output-dir", str(tmp_path)])
    assert late.call_args.kwargs["claim_boundary_batch"] is False
    with patch.object(fam, "add_late_announced") as late:
        fam.main(["--add-late", "--end-date", "2026-09-28", "--output-dir", str(tmp_path)])
    assert "claim_boundary_batch" not in late.call_args.kwargs
