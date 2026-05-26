#!/usr/bin/env python3
"""
Fetch arXiv paper metadata for a given time window and save as JSONL.

Defaults to the 7-day window ending last Monday at midnight — designed to run
on Monday morning as a scheduled task.

Usage:
    python scripts/fetch_arxiv_metadata.py
    python scripts/fetch_arxiv_metadata.py --window-days 30 --max-results 2000
    python scripts/fetch_arxiv_metadata.py --end-date 2026-05-25
    python scripts/fetch_arxiv_metadata.py --category "cat:cs.CL OR cat:cs.AI"
"""

import argparse
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import arxiv
import requests

_DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "data/raw/extracted_papers_metadata"
_OAI_BASE_URL = "https://oaipmh.arxiv.org/oai"
_OAI_USER_AGENT = "LangTrendHarvester/1.0 (contact@yourinstitution.edu; supports OAI-PMH)"
_ARXIV_CLIENT_PAGE_SIZE = 1000
_ARXIV_CLIENT_DELAY_SECONDS = 10.0
_ARXIV_CLIENT_NUM_RETRIES = 5
_FETCH_ATTEMPTS = 4
_FETCH_BACKOFF_SECONDS = 60
_OAI_REQUEST_TIMEOUT = 60
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
    end_str = end_date.strftime("%Y-%m-%d")
    category_tokens = _extract_category_tokens(category_query)
    set_specs = [_category_token_to_oai_set_spec(token) for token in category_tokens] or [None]

    headers = {
        "User-Agent": _OAI_USER_AGENT,
    }

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

        url = _OAI_BASE_URL
        current_params = params
        while url:
            try:
                response = requests.get(url, params=current_params, headers=headers, timeout=_OAI_REQUEST_TIMEOUT)
                if response.status_code == 200:
                    root = ET.fromstring(response.content)
                    ns = {"oai": "http://www.openarchives.org/OAI/2.0/"}
                    for record in root.findall(".//oai:record", ns):
                        parsed = _parse_oai_record(record)
                        if not parsed:
                            continue
                        created = _parse_oai_date(parsed.get("created"))
                        if created is None or not (end_date - timedelta(days=window_days) <= created <= end_date):
                            continue
                        paper_id = parsed["id"]
                        if paper_id and paper_id not in records_by_id:
                            records_by_id[paper_id] = parsed

                    token = _parse_resumption_token(response.content)
                    if token:
                        current_params = {"verb": "ListRecords", "resumptionToken": token}
                        url = _OAI_BASE_URL
                    else:
                        url = None
                elif response.status_code == 429:
                    wait_time = int(response.headers.get("Retry-After", 60))
                    print(f"OAI-PMH returned 429 Too Many Requests; retrying in {wait_time}s")
                    time.sleep(wait_time)
                else:
                    print(f"OAI-PMH error: {response.status_code}")
                    break
            except requests.exceptions.RequestException as exc:
                print(f"OAI-PMH request failed: {exc}")
                time.sleep(60)
                continue

    return list(records_by_id.values())


def _last_monday_midnight() -> datetime:
    today = datetime.now()
    days_since_monday = today.weekday()
    monday = today - timedelta(days=days_since_monday)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def fetch_and_save(
    end_date: datetime,
    window_days: int,
    max_results: int,
    category_query: str,
    output_dir: Path,
) -> Path:
    start_date = end_date - timedelta(days=window_days)
    start_str = start_date.strftime("%Y%m%d%H%M")
    end_str = end_date.strftime("%Y%m%d%H%M")

    query = f"{category_query} AND submittedDate:[{start_str} TO {end_str}]"
    print(f"Query:       {query}")
    print(f"Max results: {max_results}")
    print(f"Window:      {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")

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

    paper_dicts: list[dict] = []
    fetch_source = "arxiv_api"
    last_error: Exception | None = None
    for attempt in range(1, _FETCH_ATTEMPTS + 1):
        try:
            results = list(client.results(search))
            paper_dicts = [
                {
                    "id": r.entry_id,
                    "title": r.title,
                    "abstract": r.summary,
                    "authors": [author.name for author in r.authors],
                    "published": r.published.isoformat(),
                    "updated": r.updated.isoformat(),
                    "categories": list(r.categories),
                    "pdf_url": r.pdf_url,
                    "_fetch_source": "arxiv_api",
                }
                for r in results
            ]
            last_error = None
            break
        except arxiv.HTTPError as err:
            last_error = err
            if not _is_transient_arxiv_error(err) or attempt == _FETCH_ATTEMPTS:
                break
            wait_seconds = _FETCH_BACKOFF_SECONDS * attempt
            print(
                f"arXiv returned a transient API error; retrying in {wait_seconds}s "
                f"(attempt {attempt}/{_FETCH_ATTEMPTS})"
            )
            time.sleep(wait_seconds)

    if last_error is not None and not paper_dicts:
        print("arXiv API fetch failed; falling back to OAI-PMH harvest")
        oai_records = _fetch_oai_records(category_query, end_date, window_days)
        fetch_source = "oai_pmh"
        for rec in oai_records:
            rec["_fetch_source"] = "oai_pmh"
        paper_dicts = oai_records
        print(
            f"Retrieved {len(paper_dicts)} papers via OAI-PMH fallback "
            "(note: OAI created dates may not align exactly with the requested week window)"
        )

    print(f"Retrieved {len(paper_dicts)} papers (source: {fetch_source})")

    if fetch_source == "arxiv_api" and len(paper_dicts) >= max_results:
        print(
            f"Warning: max_results limit ({max_results}) reached — "
            "there may be more papers in this window. Consider increasing --max-results."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"arxiv_papers_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.jsonl"
    )
    output_path = output_dir / filename

    with output_path.open("w", encoding="utf-8") as fp:
        for paper_data in paper_dicts:
            fp.write(json.dumps(paper_data, ensure_ascii=False) + "\n")

    print(f"Saved to {output_path}")
    return output_path


def main() -> None:
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
        help="End date for the window (default: last Monday at midnight)",
    )
    args = parser.parse_args()

    if args.end_date:
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
    else:
        end_date = _last_monday_midnight()
        print(f"Using last Monday as end date: {end_date.strftime('%Y-%m-%d')}")

    fetch_and_save(
        end_date=end_date,
        window_days=args.window_days,
        max_results=args.max_results,
        category_query=args.category,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
