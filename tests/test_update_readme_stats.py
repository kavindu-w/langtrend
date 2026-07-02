"""
Unit tests for scripts/update_readme_stats.py.

Run with: pytest tests/test_update_readme_stats.py -v
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import update_readme_stats as urs


# ---------------------------------------------------------------------------
# find_week_manifests
# ---------------------------------------------------------------------------

def test_find_week_manifests_returns_empty_when_weeks_dir_missing(tmp_path):
    assert urs.find_week_manifests(tmp_path / "weeks") == []


def test_find_week_manifests_ignores_non_matching_dirs_and_sorts_by_date(tmp_path):
    weeks_dir = tmp_path / "weeks"
    for name in ["20260518_to_20260525", "20260504_to_20260511", "not_a_week", "20260427_to_20260504"]:
        d = weeks_dir / name
        d.mkdir(parents=True)
        if name != "not_a_week":
            (d / "langtrend_manifest.json").write_text("{}", encoding="utf-8")

    result = urs.find_week_manifests(weeks_dir)
    assert [p.parent.name for p in result] == [
        "20260427_to_20260504",
        "20260504_to_20260511",
        "20260518_to_20260525",
    ]


def test_find_week_manifests_skips_week_dir_without_manifest(tmp_path):
    weeks_dir = tmp_path / "weeks"
    (weeks_dir / "20260504_to_20260511").mkdir(parents=True)
    assert urs.find_week_manifests(weeks_dir) == []


# ---------------------------------------------------------------------------
# load_manifest
# ---------------------------------------------------------------------------

def test_load_manifest_returns_none_for_missing_file(tmp_path):
    assert urs.load_manifest(tmp_path / "missing.json") is None


def test_load_manifest_returns_none_for_corrupt_json(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert urs.load_manifest(path) is None


def test_load_manifest_returns_parsed_dict(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"week_start": "2026-05-04"}), encoding="utf-8")
    assert urs.load_manifest(path) == {"week_start": "2026-05-04"}


# ---------------------------------------------------------------------------
# compute_latest_stats
# ---------------------------------------------------------------------------

def test_compute_latest_stats_with_none_returns_zeros():
    result = urs.compute_latest_stats(None)
    assert result == {
        "week_start": None,
        "week_end": None,
        "papers": 0,
        "flagged_papers": 0,
        "unique_languages": 0,
    }


def test_compute_latest_stats_extracts_counts():
    manifest = {
        "week_start": "2026-06-22",
        "week_end": "2026-06-29",
        "counts": {"papers": 475, "flagged_papers": 293, "unique_languages": 301},
    }
    result = urs.compute_latest_stats(manifest)
    assert result == {
        "week_start": "2026-06-22",
        "week_end": "2026-06-29",
        "papers": 475,
        "flagged_papers": 293,
        "unique_languages": 301,
    }


# ---------------------------------------------------------------------------
# compute_cumulative_stats
# ---------------------------------------------------------------------------

def test_compute_cumulative_stats_with_no_weeks_returns_zeros():
    result = urs.compute_cumulative_stats([])
    assert result == {
        "total_papers": 0,
        "total_flagged_papers": 0,
        "total_unique_languages": 0,
        "weeks_tracked": 0,
        "earliest_week_start": None,
    }


def test_compute_cumulative_stats_sums_counts_and_dedupes_languages_across_weeks():
    weeks = [
        {
            "week_start": "2026-04-27",
            "counts": {"papers": 100, "flagged_papers": 60},
            "language_counts": [{"language": "English", "count": 10}, {"language": "Sinhala", "count": 2}],
        },
        {
            "week_start": "2026-05-04",
            "counts": {"papers": 200, "flagged_papers": 90},
            # "English" repeats across weeks — must be deduped, not double-counted.
            "language_counts": [{"language": "English", "count": 20}, {"language": "Tamil", "count": 5}],
        },
    ]
    result = urs.compute_cumulative_stats(weeks)
    assert result == {
        "total_papers": 300,
        "total_flagged_papers": 150,
        "total_unique_languages": 3,
        "weeks_tracked": 2,
        "earliest_week_start": "2026-04-27",
    }


# ---------------------------------------------------------------------------
# _top_language / _needs_review_counts
# ---------------------------------------------------------------------------

def test_top_language_picks_highest_count():
    language_counts = [{"language": "English", "count": 10}, {"language": "Chinese", "count": 15}]
    assert urs._top_language(language_counts) == ("Chinese", 15)


def test_top_language_breaks_ties_alphabetically():
    language_counts = [{"language": "Turkish", "count": 5}, {"language": "Arabic", "count": 5}]
    assert urs._top_language(language_counts) == ("Arabic", 5)


def test_top_language_with_no_languages_returns_none():
    assert urs._top_language([]) == (None, 0)


def test_needs_review_counts_counts_detections_and_distinct_papers():
    flagged_papers = [
        {"languages": [{"language": "Aka", "needs_review": True}, {"language": "English"}]},
        {"languages": [{"language": "Ami", "needs_review": True}, {"language": "Gan", "needs_review": True}]},
        {"languages": [{"language": "French"}]},
    ]
    detections, papers = urs._needs_review_counts(flagged_papers)
    assert detections == 3  # Aka + Ami + Gan
    assert papers == 2  # first two papers each had >=1 flagged detection


def test_needs_review_counts_with_no_flagged_papers_is_zero():
    assert urs._needs_review_counts([]) == (0, 0)


# ---------------------------------------------------------------------------
# build_weekly_summary_rows / write_weekly_summary_csv
# ---------------------------------------------------------------------------

def test_build_weekly_summary_rows_flattens_counts_and_class_counts():
    weeks = [
        {
            "week_start": "2026-04-27",
            "week_end": "2026-05-04",
            "counts": {"papers": 100, "flagged_papers": 60, "unique_languages": 42,
                       "pdf_failed_no_detection": 3},
            "class_counts": [{"class_id": 0, "count": 20}, {"class_id": 5, "count": 5}],
            "language_counts": [{"language": "English", "count": 20}, {"language": "Sinhala", "count": 5}],
            "flagged_papers": [
                {"languages": [{"language": "Aka", "needs_review": True}]},
                {"languages": [{"language": "English"}]},
            ],
        },
    ]
    rows = urs.build_weekly_summary_rows(weeks)
    assert rows == [{
        "week_start": "2026-04-27", "week_end": "2026-05-04",
        "papers": 100, "flagged_papers": 60, "unique_languages": 42,
        "total_language_mentions": 25,
        "top_language": "English", "top_language_count": 20,
        "needs_review_detections": 1, "needs_review_papers": 1,
        "pdf_failed_no_detection": 3,
        "class_0_mentions": 20, "class_1_mentions": 0, "class_2_mentions": 0, "class_3_mentions": 0, "class_4_mentions": 0, "class_5_mentions": 5,
    }]


def test_build_weekly_summary_rows_with_no_weeks_is_empty():
    assert urs.build_weekly_summary_rows([]) == []


def _sample_summary_row() -> dict:
    return {
        "week_start": "2026-04-27", "week_end": "2026-05-04",
        "papers": 100, "flagged_papers": 60, "unique_languages": 42,
        "total_language_mentions": 25, "top_language": "English", "top_language_count": 20,
        "needs_review_detections": 1, "needs_review_papers": 1,
        "class_0_mentions": 20, "class_1_mentions": 0, "class_2_mentions": 0, "class_3_mentions": 0, "class_4_mentions": 0, "class_5_mentions": 5,
        "pdf_failed_no_detection": 3,
    }


def test_write_weekly_summary_csv_writes_header_and_rows(tmp_path):
    out_path = urs.write_weekly_summary_csv([_sample_summary_row()], tmp_path / "weekly_summary.csv")

    text = out_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == ",".join(urs._SUMMARY_FIELDNAMES)
    assert lines[1] == "2026-04-27,2026-05-04,100,60,42,25,English,20,1,1,20,0,0,0,0,5,3"
    assert "\r\n" not in text  # fixed line terminator, no OS-dependent newlines


def test_write_weekly_summary_csv_is_deterministic(tmp_path):
    rows = [_sample_summary_row()]
    first_path = urs.write_weekly_summary_csv(rows, tmp_path / "weekly_summary.csv")
    first_bytes = first_path.read_bytes()
    second_path = urs.write_weekly_summary_csv(rows, tmp_path / "weekly_summary.csv")
    assert first_bytes == second_path.read_bytes()


# ---------------------------------------------------------------------------
# write_badge_files
# ---------------------------------------------------------------------------

def test_write_badge_files_writes_valid_shields_endpoint_schema(tmp_path):
    cumulative = {"total_papers": 5400, "total_unique_languages": 894}
    paths = urs.write_badge_files(cumulative, tmp_path)

    papers_badge = json.loads((tmp_path / "papers_badge.json").read_text(encoding="utf-8"))
    languages_badge = json.loads((tmp_path / "languages_badge.json").read_text(encoding="utf-8"))

    assert papers_badge == {
        "schemaVersion": 1, "label": "papers analysed", "message": "5,400", "color": "0f6c5d",
    }
    assert languages_badge == {
        "schemaVersion": 1, "label": "languages tracked", "message": "894", "color": "0f6c5d",
    }
    assert set(paths) == {tmp_path / "papers_badge.json", tmp_path / "languages_badge.json"}


# ---------------------------------------------------------------------------
# update_readme (idempotency)
# ---------------------------------------------------------------------------

def _readme_with_markers(body: str = "old content") -> str:
    return (
        "# Title\n\nintro\n\n"
        f"{urs._STATS_START}\n{body}\n{urs._STATS_END}\n\n---\n\n## Next section\n"
    )


def test_update_readme_raises_when_markers_missing(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Title\n\nno markers here\n", encoding="utf-8")
    with pytest.raises(ValueError):
        urs.update_readme("some block", readme)


def test_update_readme_replaces_content_between_markers(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers("old content"), encoding="utf-8")

    new_block = f"{urs._STATS_START}\nnew content\n{urs._STATS_END}"
    changed = urs.update_readme(new_block, readme)

    assert changed is True
    text = readme.read_text(encoding="utf-8")
    assert "new content" in text
    assert "old content" not in text
    assert "## Next section" in text  # content outside markers is preserved


def test_update_readme_is_idempotent(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(_readme_with_markers("old content"), encoding="utf-8")

    block = f"{urs._STATS_START}\nnew content\n{urs._STATS_END}"
    first_changed = urs.update_readme(block, readme)
    text_after_first = readme.read_text(encoding="utf-8")

    second_changed = urs.update_readme(block, readme)
    text_after_second = readme.read_text(encoding="utf-8")

    assert first_changed is True
    assert second_changed is False
    assert text_after_first == text_after_second


# ---------------------------------------------------------------------------
# render_stats_block (determinism)
# ---------------------------------------------------------------------------

def test_render_stats_block_is_deterministic_for_same_input():
    latest = {"week_start": "2026-06-22", "week_end": "2026-06-29", "papers": 475,
               "flagged_papers": 293, "unique_languages": 301}
    cumulative = {"total_papers": 5400, "total_flagged_papers": 3274,
                  "total_unique_languages": 894, "weeks_tracked": 9, "earliest_week_start": "2026-04-27"}

    first = urs.render_stats_block(latest, cumulative)
    second = urs.render_stats_block(latest, cumulative)
    assert first == second
    assert "2026-06-22" in first
    assert "5,400" in first


def test_render_stats_block_handles_missing_latest_week_gracefully():
    latest = {"week_start": None, "week_end": None, "papers": 0, "flagged_papers": 0, "unique_languages": 0}
    cumulative = {"total_papers": 0, "total_flagged_papers": 0, "total_unique_languages": 0,
                  "weeks_tracked": 0, "earliest_week_start": None}

    block = urs.render_stats_block(latest, cumulative)
    assert "N/A" in block
