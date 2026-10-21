#!/usr/bin/env python3
"""
Fetch arXiv paper metadata for a given time window and save as JSONL.

Defaults to the 7-day window ending last Monday at midnight UTC — designed to
run on Tuesday morning UTC, after arXiv's Monday 20:00 ET announcement (which
covers Fri–Mon submissions) has completed (~Tue 00:00 UTC). The query end is
extended +1 day internally to capture those Tuesday-published papers.

After fetching, papers announced this week but submitted earlier (moderation
holds, holiday delays) are appended too — see add_late_announced().

Usage:
    python scripts/fetch_arxiv_metadata.py
    python scripts/fetch_arxiv_metadata.py --window-days 30 --max-results 2000
    python scripts/fetch_arxiv_metadata.py --end-date 2026-05-25
    python scripts/fetch_arxiv_metadata.py --category "cat:cs.CL OR cat:cs.AI"
    python scripts/fetch_arxiv_metadata.py --end-date 2026-09-28 --add-late   # backfill an existing week
"""

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import arxiv
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from langtrend.html_processor import ARXIV_USER_AGENT

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "data/raw/extracted_papers_metadata"
_OAI_BASE_URL = "https://oaipmh.arxiv.org/oai"
_OAI_USER_AGENT = "LangTrendBot (automated research tool; https://github.com/kavindu-w/langtrend; akwarnakulasuriya@gmail.com; supports OAI-PMH)"
_ARXIV_DIRECT_HEADERS = {
    "User-Agent": ARXIV_USER_AGENT,
    "Accept": "application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
}
_OAI_HEADERS = {
    "User-Agent": _OAI_USER_AGENT,
    "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
}
_ARXIV_DIRECT_BASE_URL = "https://export.arxiv.org/api/query"
_ARXIV_DIRECT_PAGE_SIZE = 500
_ARXIV_DIRECT_DELAY_SECONDS = 5.0
_ARXIV_DIRECT_TIMEOUT = 60
_ARXIV_CLIENT_PAGE_SIZE = 500
_ARXIV_CLIENT_DELAY_SECONDS = 10.0
_ARXIV_CLIENT_NUM_RETRIES = 0
_FETCH_ATTEMPTS = 3
_FETCH_BACKOFF_SECONDS = 600
_OAI_REQUEST_TIMEOUT = 120
_OAI_CREATED_WINDOW_BUFFER_DAYS = 2


def _is_transient_arxiv_error(error: arxiv.HTTPError) -> bool:
    message = str(error)
    return any(code in message for code in ("HTTP 429", "HTTP 503", "HTTP 502", "HTTP 504"))


def _extract_category_tokens(category_query: str) -> list[str]:
    return sorted({match.group(1) for match in re.finditer(r"cat:([A-Za-z0-9.+\-]+)", category_query)})


def _category_token_to_oai_set_spec(category_token: str) -> str:
    parts = category_token.split(".", 1)
    archive = parts[0]
    if len(parts) == 1:
        return archive
    return f"{archive}:{archive}:{parts[1]}"


def _parse_oai_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_resumption_token(xml_content: bytes | str) -> str | None:
    root = ET.fromstring(xml_content)
    ns = {"oai": "http://www.openarchives.org/OAI/2.0/"}
    token_node = root.find(".//oai:resumptionToken", ns)
    if token_node is None or not token_node.text:
        return None
    token = token_node.text.strip()
    return token or None


def _oai_text(parent: ET.Element | None, path: str, namespaces: dict[str, str]) -> str:
    if parent is None:
        return ""
    node = parent.find(path, namespaces)
    return (node.text or "").strip() if node is not None and node.text else ""


def _parse_oai_record(record: ET.Element) -> dict | None:
    ns = {
        "oai": "http://www.openarchives.org/OAI/2.0/",
        "arxiv": "http://arxiv.org/OAI/arXiv/",
    }
    header = record.find("oai:header", ns)
    metadata = record.find("oai:metadata", ns)
    arxiv_meta = metadata.find("arxiv:arXiv", ns) if metadata is not None else None
    if arxiv_meta is None:
        return None

    created = _oai_text(arxiv_meta, "arxiv:created", ns)
    updated = _oai_text(arxiv_meta, "arxiv:updated", ns) or created
    authors: list[str] = []
    for author in arxiv_meta.findall("arxiv:authors/arxiv:author", ns):
        forenames = _oai_text(author, "arxiv:forenames", ns)
        keyname = _oai_text(author, "arxiv:keyname", ns)
        name = " ".join(part for part in (forenames, keyname) if part)
        if name:
            authors.append(name)

    paper_id = _oai_text(arxiv_meta, "arxiv:id", ns)
    if not paper_id and header is not None:
        identifier = _oai_text(header, "oai:identifier", ns)
        paper_id = identifier.rsplit(":", 1)[-1]

    return {
        "id": f"https://arxiv.org/abs/{paper_id}" if paper_id else _oai_text(header, "oai:identifier", ns),
        "title": _oai_text(arxiv_meta, "arxiv:title", ns),
        "abstract": _oai_text(arxiv_meta, "arxiv:abstract", ns),
        "authors": authors,
        "published": f"{created}T00:00:00" if created else "",
        "updated": f"{updated}T00:00:00" if updated and len(updated) == 10 else updated,
        "categories": _oai_text(arxiv_meta, "arxiv:categories", ns).split(),
        "pdf_url": f"https://arxiv.org/pdf/{paper_id}.pdf" if paper_id else "",
        "created": created,
    }


def _fetch_oai_records(category_query: str, end_date: datetime, window_days: int) -> list[dict]:
    start_date = end_date - timedelta(days=window_days + _OAI_CREATED_WINDOW_BUFFER_DAYS)
    start_str = start_date.strftime("%Y-%m-%d")
    # OAI-PMH `until` is exclusive for same-day datestamps — papers submitted on the
    # last weekend (Sat/Sun) are announced on end_date Monday and get datestamp=end_date,
    # so extend by 1 day to include them.
    oai_end_date = end_date + timedelta(days=1)
    end_str = oai_end_date.strftime("%Y-%m-%d")
    category_tokens = _extract_category_tokens(category_query)
    set_specs = [_category_token_to_oai_set_spec(token) for token in category_tokens] or [None]

    log.info("OAI-PMH harvest: %s → %s  set(s)=%s", start_str, end_str,
             [s for s in set_specs if s])

    headers = _OAI_HEADERS

    records_by_id: dict[str, dict] = {}
    for set_spec in set_specs:
        params = {
            "verb": "ListRecords",
            "metadataPrefix": "arXiv",
            "from": start_str,
            "until": end_str,
        }
        if set_spec:
            params["set"] = set_spec

        page = 0
        url = _OAI_BASE_URL
        current_params = params
        while url:
            try:
                page += 1
                log.info("  OAI page %d (records so far: %d)", page, len(records_by_id))
                response = requests.get(url, params=current_params, headers=headers, timeout=_OAI_REQUEST_TIMEOUT)
                if response.status_code == 200:
                    root = ET.fromstring(response.content)
                    ns = {"oai": "http://www.openarchives.org/OAI/2.0/"}
                    page_added = 0
                    for record in root.findall(".//oai:record", ns):
                        parsed = _parse_oai_record(record)
                        if not parsed:
                            continue
                        created = _parse_oai_date(parsed.get("created"))
                        # end_date is exclusive: papers created on end_date belong to the next window
                        if created is None or not (end_date - timedelta(days=window_days) <= created < end_date):
                            continue
                        paper_id = parsed["id"]
                        # Reject cross-listed papers: OAI sets <created> to the cross-listing
                        # date rather than the original submission date, so the created filter
                        # above passes them. Guard by checking the YYMM prefix in the paper ID
                        # against the window's month range.
                        id_match = re.search(r"(\d{2})(\d{2})\.\d+", paper_id)
                        if id_match:
                            id_year = 2000 + int(id_match.group(1))
                            id_month = int(id_match.group(2))
                            id_month_start = datetime(id_year, id_month, 1)
                            window_start = end_date - timedelta(days=window_days)
                            window_month_start = datetime(window_start.year, window_start.month, 1)
                            window_month_end = datetime(end_date.year, end_date.month, 1)
                            if not (window_month_start <= id_month_start <= window_month_end):
                                continue
                        if paper_id and paper_id not in records_by_id:
                            records_by_id[paper_id] = parsed
                            page_added += 1
                    log.info("  OAI page %d: +%d new records", page, page_added)

                    token = _parse_resumption_token(response.content)
                    if token:
                        current_params = {"verb": "ListRecords", "resumptionToken": token}
                        url = _OAI_BASE_URL
                    else:
                        url = None
                elif response.status_code == 429:
                    wait_time = int(response.headers.get("Retry-After", 60))
                    log.warning("OAI-PMH 429 Too Many Requests; retrying in %ds", wait_time)
                    time.sleep(wait_time)
                else:
                    log.error("OAI-PMH error: HTTP %d", response.status_code)
                    break
            except requests.exceptions.RequestException as exc:
                log.error("OAI-PMH request failed: %s", exc)
                time.sleep(60)
                continue

    log.info("OAI-PMH harvest complete: %d records", len(records_by_id))
    return list(records_by_id.values())


_ATOM = "http://www.w3.org/2005/Atom"


def _parse_atom_entries(root: ET.Element) -> list[dict]:
    """Paper dicts from an arXiv Atom API response."""

    def _text(el: ET.Element, tag: str) -> str:
        node = el.find(f"{{{_ATOM}}}{tag}")
        return (node.text or "").strip() if node is not None else ""

    papers = []
    for entry in root.findall(f"{{{_ATOM}}}entry"):
        authors = [_text(author, "name") for author in entry.findall(f"{{{_ATOM}}}author")]
        categories = [
            cat.get("term", "")
            for cat in entry.findall(f"{{{_ATOM}}}category")
            if cat.get("term")
        ]
        pdf_url = next(
            (lnk.get("href", "") for lnk in entry.findall(f"{{{_ATOM}}}link")
             if lnk.get("type") == "application/pdf"),
            "",
        )
        papers.append({
            "id": _text(entry, "id"),
            "title": _text(entry, "title"),
            "abstract": _text(entry, "summary"),
            "authors": authors,
            "published": _text(entry, "published"),
            "updated": _text(entry, "updated"),
            "categories": categories,
            "pdf_url": pdf_url,
            "_fetch_source": "arxiv_direct",
        })
    return papers


def _fetch_arxiv_direct(query: str, max_results: int) -> list[dict]:
    """Fetch papers directly from the arXiv Atom API using requests, paginating until max_results."""
    OPENSEARCH = "http://a9.com/-/spec/opensearch/1.1/"

    papers: list[dict] = []
    start = 0
    total_available = max_results

    while start < min(max_results, total_available):
        page_size = min(_ARXIV_DIRECT_PAGE_SIZE, max_results - start)
        params = {
            "search_query": query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "start": start,
            "max_results": page_size,
        }
        log.info("  arXiv direct: start=%d page_size=%d", start, page_size)
        resp = requests.get(
            _ARXIV_DIRECT_BASE_URL, params=params, headers=_ARXIV_DIRECT_HEADERS, timeout=_ARXIV_DIRECT_TIMEOUT
        )
        resp.raise_for_status()

        root = ET.fromstring(resp.content)

        if start == 0:
            total_node = root.find(f"{{{OPENSEARCH}}}totalResults")
            if total_node is not None and total_node.text:
                total_available = int(total_node.text)
                log.info("  arXiv direct: totalResults=%d", total_available)

        entries = _parse_atom_entries(root)
        if not entries:
            break
        papers.extend(entries)

        start += len(entries)
        if len(entries) < page_size:
            break
        if start < min(max_results, total_available):
            time.sleep(_ARXIV_DIRECT_DELAY_SECONDS)

    return papers


# ---------------------------------------------------------------------------
# Late-announced papers
#
# Every fetch path above selects a week's papers by *submission* date, but a
# paper only becomes public — with HTML/PDF to scan — when arXiv *announces*
# it, which can be days to months later (moderation holds, holidays). Such a
# paper is never in its submission week's file (not public yet when that week
# was fetched) nor in its announcement week's (submitted too early), so it
# was silently lost — ~10% of cs.CL in 2026.
#
# arXiv assigns IDs at announcement, in order, so a week's announcements are a
# contiguous ID range. add_late_announced() files each missed paper under the
# week whose range holds its ID: it lists cs.CL's IDs for the range from
# arXiv's monthly listing (cross-lists included, like the weekly fetch), and
# appends whichever no week has yet. Ranges tile: each starts right after the
# previous week's, so a paper on a boundary isn't lost either.
# ---------------------------------------------------------------------------

_LISTING_URL = "https://arxiv.org/list/{category}/{month}"
_LISTING_PAGE_SIZE = 2000  # arXiv's maximum `show`
# arxiv.org/robots.txt allows /list with Crawl-delay: 15 (export.arxiv.org
# disallows crawling entirely, so the listing pages have to come from here).
_LISTING_DELAY_SECONDS = 15.0
_LISTING_TIMEOUT = 120
_LISTING_TOTAL_RE = re.compile(r"Total of ([\d,]+) entries")
# The listing's per-paper Abstract link; abstracts/comments also mention other
# papers' IDs, so a bare ID pattern would over-match.
_LISTING_ENTRY_RE = re.compile(r'title="Abstract" id="(\d{4}\.\d{4,5})"')
_ID_KEY_RE = re.compile(r"(\d{4})\.(\d{4,5})")
_WEEK_FILE_RE = re.compile(r"arxiv_papers_(\d{8})_to_(\d{8})\.jsonl$")
_ID_LIST_BATCH = 100
_LATE_RETRY_STATUSES = (403, 429, 500, 502, 503, 504)
_LATE_RETRY_BACKOFF_SECONDS = 60
# A week's own papers are rarely more than a few hundred IDs apart (a holiday
# batch ~1.5k); a bigger jump means papers filed under their submission week
# by a retroactive fetch — they belong to a later week's range, not this one's.
_RANGE_GAP_LIMIT = 3000
# Marker the CI fetch step greps for to push a notification.
LATE_FAILURE_MARKER = "ERROR: late-announced step failed"

IdKey = tuple[int, int]


def _id_key(arxiv_id: str) -> IdKey:
    """(YYMM, sequence) for an ID or abs URL, version ignored — sorts in announcement order."""
    m = _ID_KEY_RE.search(arxiv_id)
    if not m:
        raise ValueError(f"not an arXiv ID: {arxiv_id!r}")
    return int(m.group(1)), int(m.group(2))


def _format_id(key: IdKey) -> str:
    return f"{key[0]:04d}.{key[1]:05d}"


def _months_between(lo: IdKey, hi: IdKey) -> list[str]:
    """Listing months ('2026-09') whose IDs can fall in lo..hi."""
    months = []
    year, month = 2000 + lo[0] // 100, lo[0] % 100
    while (year % 100) * 100 + month <= hi[0]:
        months.append(f"{year}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _main_block_top(keys: list[IdKey]) -> IdKey:
    """Highest ID of the week's main run of IDs, ignoring outliers past a big gap.

    Only a same-month gap can be measured (month sizes vary), so an outlier in
    the following month isn't split off here; for every week but the latest,
    _week_id_ranges caps the top at the next week's start, which covers that.
    """
    keys = sorted(keys)
    blocks, current = [], [keys[0]]
    for prev, key in zip(keys, keys[1:]):
        same_month_gap = key[0] == prev[0] and key[1] - prev[1] > _RANGE_GAP_LIMIT
        skips_a_month = key[0] != prev[0] and key[0] - prev[0] not in (1, 89)  # 89: Dec → Jan
        if same_month_gap or skips_a_month:
            blocks.append(current)
            current = []
        current.append(key)
    blocks.append(current)
    return max(blocks, key=len)[-1]


class _Week:
    def __init__(self, end: datetime, path: Path, regular: set[IdKey], late: set[IdKey]):
        self.end, self.path, self.regular, self.late = end, path, regular, late


def _load_weeks(output_dir: Path, window_days: int = 7) -> list[_Week]:
    """Every weekly file with the same window length, oldest first.

    `regular` = the week's own fetch; `late` = rows a previous late step
    appended. Ranges are built from `regular` only, so appending never moves
    a boundary.
    """
    weeks = []
    for path in output_dir.glob("arxiv_papers_*.jsonl"):
        m = _WEEK_FILE_RE.search(path.name)
        if not m:
            continue
        start, end = (datetime.strptime(d, "%Y%m%d") for d in m.groups())
        if (end - start).days != window_days:
            continue
        regular, late = set(), set()
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                try:
                    key = _id_key(str(row.get("id", "")))
                except ValueError:
                    log.warning("Late-announced: %s has a row without an arXiv ID — ignored", path.name)
                    continue
                (late if row.get("_late_announced") else regular).add(key)
        weeks.append(_Week(end, path, regular, late))
    return sorted(weeks, key=lambda w: w.end)


def _week_id_ranges(weeks: list[_Week]) -> dict[Path, tuple[IdKey, IdKey]]:
    """Each week's announced-ID range as (exclusive low, inclusive high).

    A week's range ends at the top of its main run of IDs, or just before the
    next week's first ID if that's lower. The next week's first ID is a hard
    limit: its papers were submitted after this window, so nothing from them
    on was announced in it — that's what keeps the early, retroactively
    fetched weeks (whose papers spill into later weeks' IDs) from swallowing
    their successors. Using the own top otherwise (not just the next start)
    keeps a batch announced late, with no regular papers of its own — the
    Labor Day batch — in the week it was actually announced. Highs never go
    backwards, so ranges tile without overlap.
    """
    weeks = [w for w in weeks if w.regular]
    ranges = {}
    prev_hi = None
    for i, week in enumerate(weeks):
        hi = _main_block_top(list(week.regular))
        if i + 1 < len(weeks):
            next_first = min(weeks[i + 1].regular)
            hi = min(hi, (next_first[0], next_first[1] - 1))
        if prev_hi is None:
            first = min(week.regular)
            prev_hi = (first[0], first[1] - 1)
        hi = max(hi, prev_hi)
        ranges[week.path] = (prev_hi, hi)
        prev_hi = hi
    return ranges


def _get_with_retry(get, sleep, url: str, delay: float, **kwargs):
    """GET after `delay`, retrying throttling/5xx/network errors with backoff; raises on the last failure."""
    for attempt in range(1, _FETCH_ATTEMPTS + 1):
        sleep(delay)
        try:
            resp = get(url, **kwargs)
        except requests.exceptions.RequestException as err:
            if attempt == _FETCH_ATTEMPTS:
                raise
            log.warning("GET %s failed (%s); retrying (attempt %d/%d)", url, err, attempt, _FETCH_ATTEMPTS)
        else:
            if resp.status_code not in _LATE_RETRY_STATUSES or attempt == _FETCH_ATTEMPTS:
                resp.raise_for_status()
                return resp
            log.warning("GET %s -> HTTP %d; retrying (attempt %d/%d)", url, resp.status_code, attempt, _FETCH_ATTEMPTS)
        sleep(_LATE_RETRY_BACKOFF_SECONDS * attempt)


def _fetch_listing_ids(month: str, category: str = "cs.CL", get=requests.get, sleep=time.sleep) -> set[IdKey]:
    """Every ID in arXiv's listing of `category` for one month (cross-lists included)."""
    ids: set[IdKey] = set()
    skip = 0
    while True:
        resp = _get_with_retry(get, sleep, _LISTING_URL.format(category=category, month=month),
                               _LISTING_DELAY_SECONDS,
                               params={"skip": skip, "show": _LISTING_PAGE_SIZE},
                               headers={"User-Agent": _OAI_USER_AGENT}, timeout=_LISTING_TIMEOUT)
        page = [_id_key(i) for i in _LISTING_ENTRY_RE.findall(resp.text)]
        total_m = _LISTING_TOTAL_RE.search(resp.text)
        ids.update(page)
        skip += _LISTING_PAGE_SIZE
        if not page or not total_m or skip >= int(total_m.group(1).replace(",", "")):
            break
    if total_m and len(ids) != int(total_m.group(1).replace(",", "")):
        log.warning("%s %s listing says %s entries, parsed %d", category, month, total_m.group(1), len(ids))
    return ids


def _fetch_arxiv_by_ids(ids: list[str], get=requests.get, sleep=time.sleep) -> list[dict]:
    """Paper dicts for specific IDs via the Atom API's id_list, in batches.

    IDs the API can't resolve come back as an error entry, not a paper; those
    are dropped so callers can compare what came back against what they asked.
    """
    papers = []
    for i in range(0, len(ids), _ID_LIST_BATCH):
        batch = ids[i:i + _ID_LIST_BATCH]
        resp = _get_with_retry(get, sleep, _ARXIV_DIRECT_BASE_URL, _ARXIV_DIRECT_DELAY_SECONDS,
                               params={"id_list": ",".join(batch), "max_results": len(batch)},
                               headers=_ARXIV_DIRECT_HEADERS, timeout=_ARXIV_DIRECT_TIMEOUT)
        papers.extend(p for p in _parse_atom_entries(ET.fromstring(resp.content))
                      if "/abs/" in p["id"] and _ID_KEY_RE.search(p["id"]))
    return papers


def add_late_announced(week_path: Path, output_dir: Path | None = None, categories: tuple[str, ...] = ("cs.CL",),
                       window_days: int = 7, listing_fetcher=_fetch_listing_ids,
                       metadata_fetcher=_fetch_arxiv_by_ids, listing_cache: dict | None = None) -> int:
    """Append to week_path the papers announced in its ID range that no week has. Returns how many.

    A listed paper in this range but submitted on/after the window's end came
    out in the boundary batch; it's left to the next week. That week claims it
    — its regular fetch (by submission date) usually has it already, and if
    not, its own late step does: besides its own range, each week also takes
    papers from the previous week's range that were submitted inside its
    window. So every paper has exactly one owner, whichever week runs first.
    """
    output_dir = output_dir or week_path.parent
    weeks = _load_weeks(output_dir, window_days)
    ranges = _week_id_ranges(weeks)
    if week_path not in ranges:
        log.warning("Late-announced: %s isn't a non-empty %d-day weekly file — skipping", week_path.name, window_days)
        return 0
    lo, hi = ranges[week_path]
    end_date = next(w.end for w in weeks if w.path == week_path)
    start_date = end_date - timedelta(days=window_days)
    ordered = [w.path for w in weeks if w.path in ranges]
    index = ordered.index(week_path)
    prev_lo = ranges[ordered[index - 1]][0] if index else lo
    have = set().union(*(w.regular | w.late for w in weeks))

    cache = listing_cache if listing_cache is not None else {}
    listed: set[IdKey] = set()
    for category in categories:
        for month in _months_between((prev_lo[0], prev_lo[1] + 1), hi):
            if (category, month) not in cache:
                cache[(category, month)] = listing_fetcher(month, category)
            listed |= cache[(category, month)]
    own = {k for k in listed if lo < k <= hi and k not in have}
    from_prev = {k for k in listed if prev_lo < k <= lo and k not in have}
    candidates = sorted(own | from_prev)
    log.info("Late-announced: ID range %s – %s, %d listed paper(s) not yet in any week (+%d from the previous range)",
             _format_id((lo[0], lo[1] + 1)), _format_id(hi), len(own), len(from_prev))
    if not candidates:
        return 0

    found = metadata_fetcher([_format_id(k) for k in candidates])
    if len(found) < len(candidates):
        log.warning("Late-announced: arXiv returned %d of %d requested papers", len(found), len(candidates))
    late, after_window = [], []
    for paper in found:
        published = _parse_oai_date(paper.get("published", "")[:10])
        if published is None:
            continue
        if _id_key(paper["id"]) in from_prev:
            # Previous week left it as submitted after its window; ours if in it.
            if not start_date <= published < end_date:
                continue
        elif published >= end_date:
            after_window.append(paper["id"])
            continue
        paper["_late_announced"] = True
        late.append(paper)
    if after_window:
        log.info("Late-announced: %d submitted after this window, left to the next week: %s",
                 len(after_window), ", ".join(after_window))

    with week_path.open("a", encoding="utf-8") as fp:
        for paper in late:
            fp.write(json.dumps(paper, ensure_ascii=False) + "\n")
    log.info("Late-announced: appended %d paper(s) to %s", len(late), week_path.name)
    return len(late)


def _last_monday_midnight() -> datetime:
    """Return the most recent Monday 00:00 UTC (treating today as Tuesday or later).

    Run this on Tuesday morning UTC: 'last Monday' then resolves to yesterday,
    giving a Mon-Mon window. By Tuesday morning, arXiv has finished its Monday
    20:00 ET announcement (Fri–Mon submissions), so the week is complete.
    """
    today = datetime.now(timezone.utc)
    days_since_monday = today.weekday()  # Mon=0, Tue=1, ...
    monday = today - timedelta(days=days_since_monday)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def fetch_and_save(
    end_date: datetime,
    window_days: int,
    max_results: int,
    category_query: str,
    output_dir: Path,
    oai_only: bool = False,
) -> Path:
    start_date = end_date - timedelta(days=window_days)
    start_str = start_date.strftime("%Y%m%d%H%M")
    end_str = end_date.strftime("%Y%m%d%H%M")

    log.info("Window:      %s → %s", start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))

    paper_dicts: list[dict] = []
    fetch_source = "arxiv_direct"
    last_error: Exception | None = None

    query = f"{category_query} AND submittedDate:[{start_str} TO {end_str}]"
    log.info("Query:       %s", query)

    if oai_only:
        log.info("--oai-only: skipping arXiv API")
        last_error = Exception("oai_only")
    else:
        # --- Primary: direct requests to arXiv Atom API ---
        log.info("Max results: %d  page_size=%d  delay=%.1fs",
                 max_results, _ARXIV_DIRECT_PAGE_SIZE, _ARXIV_DIRECT_DELAY_SECONDS)
        for attempt in range(1, _FETCH_ATTEMPTS + 1):
            log.info("arXiv direct API attempt %d/%d …", attempt, _FETCH_ATTEMPTS)
            try:
                paper_dicts = _fetch_arxiv_direct(query, max_results)
                log.info("arXiv direct API returned %d results", len(paper_dicts))
                last_error = None
                fetch_source = "arxiv_direct"
                break
            except requests.HTTPError as err:
                last_error = err
                is_transient = err.response is not None and err.response.status_code in (429, 502, 503, 504)
                log.warning("arXiv direct error (attempt %d/%d): %s  [transient=%s]",
                            attempt, _FETCH_ATTEMPTS, err, is_transient)
                if not is_transient or attempt == _FETCH_ATTEMPTS:
                    log.error("Giving up on arXiv direct API after %d attempt(s)", attempt)
                    break
                wait_seconds = _FETCH_BACKOFF_SECONDS * attempt
                log.info("Retrying in %ds …", wait_seconds)
                time.sleep(wait_seconds)
            except Exception as err:
                last_error = err
                log.error("Unexpected error on direct API attempt %d/%d: %s: %s",
                          attempt, _FETCH_ATTEMPTS, type(err).__name__, err)
                break

        # --- Fallback 1: arxiv Python package ---
        if last_error is not None and not paper_dicts:
            log.warning("arXiv direct API failed; trying arxiv Python package …")
            fetch_source = "arxiv_api"
            search = arxiv.Search(
                query=query,
                max_results=max_results,
                sort_by=arxiv.SortCriterion.SubmittedDate,
            )
            client = arxiv.Client(
                page_size=_ARXIV_CLIENT_PAGE_SIZE,
                delay_seconds=_ARXIV_CLIENT_DELAY_SECONDS,
                num_retries=_ARXIV_CLIENT_NUM_RETRIES,
            )
            client._session.headers.update(_ARXIV_DIRECT_HEADERS)
            for attempt in range(1, _FETCH_ATTEMPTS + 1):
                log.info("arxiv package attempt %d/%d …", attempt, _FETCH_ATTEMPTS)
                try:
                    paper_dicts = []
                    for r in client.results(search):
                        paper_dicts.append({
                            "id": r.entry_id,
                            "title": r.title,
                            "abstract": r.summary,
                            "authors": [author.name for author in r.authors],
                            "published": r.published.isoformat(),
                            "updated": r.updated.isoformat(),
                            "categories": list(r.categories),
                            "pdf_url": r.pdf_url,
                            "_fetch_source": "arxiv_api",
                        })
                        if len(paper_dicts) % _ARXIV_CLIENT_PAGE_SIZE == 0:
                            log.info("  … fetched %d papers so far", len(paper_dicts))
                    log.info("arxiv package returned %d results on attempt %d", len(paper_dicts), attempt)
                    last_error = None
                    break
                except arxiv.HTTPError as err:
                    last_error = err
                    is_transient = _is_transient_arxiv_error(err)
                    log.warning("arxiv package HTTP error (attempt %d/%d): %s  [transient=%s]",
                                attempt, _FETCH_ATTEMPTS, err, is_transient)
                    if not is_transient or attempt == _FETCH_ATTEMPTS:
                        log.error("Giving up on arxiv package after %d attempt(s): %s", attempt, err)
                        break
                    wait_seconds = _FETCH_BACKOFF_SECONDS * attempt
                    log.info("Retrying in %ds …", wait_seconds)
                    time.sleep(wait_seconds)
                except Exception as err:
                    last_error = err
                    log.error("Unexpected error on package attempt %d/%d: %s: %s",
                              attempt, _FETCH_ATTEMPTS, type(err).__name__, err)
                    break

    # --- Fallback 2: OAI-PMH ---
    if last_error is not None and not paper_dicts:
        log.warning("All arXiv API methods failed; falling back to OAI-PMH harvest")
        oai_records = _fetch_oai_records(category_query, end_date, window_days)
        fetch_source = "oai_pmh"
        for rec in oai_records:
            rec["_fetch_source"] = "oai_pmh"
        paper_dicts = oai_records
        log.info("Retrieved %d papers via OAI-PMH fallback", len(paper_dicts))

    log.info("Retrieved %d papers (source: %s)", len(paper_dicts), fetch_source)

    if fetch_source in ("arxiv_direct", "arxiv_api") and len(paper_dicts) >= max_results:
        log.warning(
            "max_results limit (%d) reached — there may be more papers in this window. "
            "Consider increasing --max-results.", max_results
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"arxiv_papers_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.jsonl"
    )
    output_path = output_dir / filename

    with output_path.open("w", encoding="utf-8") as fp:
        for paper_data in paper_dicts:
            fp.write(json.dumps(paper_data, ensure_ascii=False) + "\n")

    log.info("Saved %d papers to %s", len(paper_dicts), output_path)
    return output_path


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Fetch arXiv paper metadata for a time window",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--window-days", type=int, default=7, help="Number of days to look back (default: 7)")
    parser.add_argument("--max-results", type=int, default=1000, help="Max papers to fetch (default: 1000)")
    parser.add_argument("--category", default="cat:cs.CL", help='arXiv category query (default: "cat:cs.CL")')
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help=f"Output directory for JSONL files (default: {_DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="End date for the window (default: last Monday at midnight UTC — run on Tuesdays)",
    )
    parser.add_argument(
        "--oai-only",
        action="store_true",
        help="Skip the arXiv search API and go directly to OAI-PMH (recommended for automated runs)",
    )
    late_group = parser.add_mutually_exclusive_group()
    late_group.add_argument(
        "--add-late",
        action="store_true",
        help="Don't fetch: only append late-announced papers to the week's existing JSONL (backfill)",
    )
    late_group.add_argument(
        "--no-add-late",
        action="store_true",
        help="Skip the late-announced step after fetching",
    )
    args = parser.parse_args(argv)

    if args.end_date:
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d")  # treated as UTC boundary
    else:
        end_date = _last_monday_midnight()
        log.info("Using last Monday as end date: %s", end_date.strftime("%Y-%m-%d"))

    categories = tuple(_extract_category_tokens(args.category)) or ("cs.CL",)
    late_kwargs = dict(output_dir=args.output_dir, categories=categories, window_days=args.window_days)

    if args.add_late:
        start_date = end_date - timedelta(days=args.window_days)
        week_path = args.output_dir / f"arxiv_papers_{start_date:%Y%m%d}_to_{end_date:%Y%m%d}.jsonl"
        if not week_path.exists():
            parser.error(f"{week_path} doesn't exist — fetch the week first")
        add_late_announced(week_path, **late_kwargs)
        return

    output_path = fetch_and_save(
        end_date=end_date,
        window_days=args.window_days,
        max_results=args.max_results,
        category_query=args.category,
        output_dir=args.output_dir,
        oai_only=args.oai_only,
    )
    if not args.no_add_late:
        # The week's regular papers are already saved; a listing/API hiccup here
        # must not fail the weekly run. Nothing later covers this week's range,
        # so the marker line makes CI push a notification to rerun by hand.
        try:
            add_late_announced(output_path, **late_kwargs)
        except Exception as err:
            log.error("%s (%s: %s) — rerun: python scripts/fetch_arxiv_metadata.py --add-late --end-date %s",
                      LATE_FAILURE_MARKER, type(err).__name__, err, end_date.strftime("%Y-%m-%d"))


if __name__ == "__main__":
    main()
