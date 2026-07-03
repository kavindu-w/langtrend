"""
Unit tests for langtrend/manifest.py.

Run with: pytest tests/test_manifest.py -v
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langtrend.manifest import build_detections, build_snapshot_manifest, save_json, load_snapshot_inputs


# ---------------------------------------------------------------------------
# build_detections
# ---------------------------------------------------------------------------

def test_build_detections_attaches_class_id():
    result = build_detections(["Sinhala"], {2: {"Sinhala"}, 5: {"English"}})
    assert result == [{"language": "Sinhala", "class": 2}]


def test_build_detections_skips_language_not_in_any_class():
    result = build_detections(["Klingon"], {2: {"Sinhala"}})
    assert result == []


def test_build_detections_skips_ignored_languages_case_insensitively():
    result = build_detections(
        ["Gan", "Sinhala"], {1: {"Gan"}, 2: {"Sinhala"}}, languages_to_ignore={"gan"}
    )
    assert result == [{"language": "Sinhala", "class": 2}]


def test_build_detections_flags_possible_false_positives():
    result = build_detections(
        ["Gan"], {1: {"Gan"}}, possible_false_positives={"Gan": "very common ML acronym"}
    )
    assert result == [{
        "language": "Gan",
        "class": 1,
        "needs_review": True,
        "flag_reason": "very common ML acronym",
    }]


def test_build_detections_uses_first_matching_class_only():
    # A language should not appear twice even if present in more than one class set.
    result = build_detections(["Sinhala"], {1: {"Sinhala"}, 2: {"Sinhala"}})
    assert len(result) == 1


# ---------------------------------------------------------------------------
# save_json
# ---------------------------------------------------------------------------

def test_save_json_creates_parent_dirs_and_writes_content(tmp_path):
    target = tmp_path / "nested" / "dir" / "out.json"
    returned = save_json({"a": 1}, target)
    assert returned == target
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


# ---------------------------------------------------------------------------
# build_snapshot_manifest
# ---------------------------------------------------------------------------

def test_build_snapshot_manifest_counts_and_aggregates():
    papers = [
        {"id": "1", "published": "2026-05-18T00:00:00"},
        {"id": "2", "published": "2026-05-19T00:00:00"},
        {"id": "3", "published": "2026-05-19T00:00:00"},
    ]
    flagged_papers = [
        {
            "paper": {"id": "1", "published": "2026-05-18T00:00:00"},
            "languages": [{"language": "Sinhala", "class": 2}, {"language": "Tamil", "class": 3}],
        },
        {
            "paper": {"id": "2", "published": "2026-05-19T00:00:00"},
            "languages": [{"language": "Sinhala", "class": 2}],
        },
    ]

    manifest = build_snapshot_manifest(
        papers=papers,
        flagged_papers=flagged_papers,
        window_days=7,
        category_query="cat:cs.CL",
        week_start="2026-05-18",
        week_end="2026-05-25",
    )

    assert manifest["counts"] == {
        "papers": 3,
        "flagged_papers": 2,
        "unique_languages": 2,
        "pdf_failed_no_detection": 0,
        "judge": {
            "judged_papers": 0,
            "judged_languages": 0,
            "studied": 0,
            "mentioned_only": 0,
            "false_positive": 0,
        },
    }
    assert manifest["week_start"] == "2026-05-18"
    assert manifest["week_end"] == "2026-05-25"
    assert {"language": "Sinhala", "count": 2} in manifest["language_counts"]
    assert {"language": "Tamil", "count": 1} in manifest["language_counts"]
    assert {"class_id": 2, "count": 2} in manifest["class_counts"]
    assert {"class_id": 3, "count": 1} in manifest["class_counts"]
    # daily_series covers every date that had either a paper or a flagged paper
    series_by_date = {item["date"]: item for item in manifest["daily_series"]}
    assert series_by_date["2026-05-18"] == {"date": "2026-05-18", "papers": 1, "flagged": 1}
    assert series_by_date["2026-05-19"] == {"date": "2026-05-19", "papers": 2, "flagged": 1}


def test_build_snapshot_manifest_handles_empty_input():
    manifest = build_snapshot_manifest(
        papers=[], flagged_papers=[], window_days=7, category_query="cat:cs.CL"
    )
    assert manifest["counts"]["papers"] == 0
    assert manifest["counts"]["flagged_papers"] == 0
    assert manifest["language_counts"] == []
    assert manifest["daily_series"] == []
    assert manifest["week_start"] is None


def test_build_snapshot_manifest_reports_pdf_failed_count():
    manifest = build_snapshot_manifest(
        papers=[], flagged_papers=[], window_days=7, category_query="cat:cs.CL",
        pdf_failed_no_detection=4,
    )
    assert manifest["counts"]["pdf_failed_no_detection"] == 4


def test_build_snapshot_manifest_excludes_judged_false_positives_from_counts():
    flagged_papers = [
        {
            "paper": {"id": "1", "published": "2026-05-18T00:00:00"},
            "languages": [
                {"language": "Swahili", "class": 0, "judge_verdict": "studied"},
                {"language": "Ari", "class": 0, "judge_verdict": "false_positive"},
                {"language": "French", "class": 5, "judge_verdict": "mentioned_only"},
            ],
        },
        {
            # every detection judged false positive → kept in flagged_papers
            # but excluded from flagged counts and the daily series
            "paper": {"id": "2", "published": "2026-05-19T00:00:00"},
            "languages": [{"language": "Agi", "class": 0, "judge_verdict": "false_positive"}],
        },
        {
            # unjudged paper — counted exactly as before
            "paper": {"id": "3", "published": "2026-05-19T00:00:00"},
            "languages": [{"language": "Tamil", "class": 3}],
        },
    ]
    manifest = build_snapshot_manifest(
        papers=[], flagged_papers=flagged_papers, window_days=7, category_query="cat:cs.CL"
    )

    langs = {item["language"] for item in manifest["language_counts"]}
    assert langs == {"Swahili", "French", "Tamil"}  # no Ari, no Agi
    assert {"class_id": 0, "count": 1} in manifest["class_counts"]

    assert manifest["counts"]["flagged_papers"] == 2  # paper 2 excluded
    assert len(manifest["flagged_papers"]) == 3  # …but still present for auditability
    series_by_date = {item["date"]: item for item in manifest["daily_series"]}
    assert series_by_date["2026-05-19"]["flagged"] == 1  # only the unjudged paper

    assert manifest["counts"]["judge"] == {
        "judged_papers": 2,
        "judged_languages": 4,
        "studied": 1,
        "mentioned_only": 1,
        "false_positive": 2,
    }


# ---------------------------------------------------------------------------
# load_snapshot_inputs
# ---------------------------------------------------------------------------

def test_load_snapshot_inputs_reads_jsonl_from_expected_paths(tmp_path):
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    processed_dir.mkdir()
    (raw_dir / "arxiv_papers_last_7_days.jsonl").write_text('{"id": "1"}\n{"id": "2"}\n', encoding="utf-8")
    (processed_dir / "papers_with_tracked_langs_last_7_days.jsonl").write_text('{"id": "1"}\n', encoding="utf-8")

    papers, flagged = load_snapshot_inputs(data_root=tmp_path, window_days=7)
    assert papers == [{"id": "1"}, {"id": "2"}]
    assert flagged == [{"id": "1"}]


def test_load_snapshot_inputs_returns_empty_lists_when_files_missing(tmp_path):
    papers, flagged = load_snapshot_inputs(data_root=tmp_path, window_days=7)
    assert papers == []
    assert flagged == []
