"""
Unit tests for scripts/build_manifest.py.

Run with: pytest tests/test_build_manifest.py -v
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_manifest as bm


# ---------------------------------------------------------------------------
# Shared language data fixtures (same shape as tests/test_text_cleaning.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def lang_classes():
    return {0: {"Swahili", "Arabic"}, 1: {"French"}}


@pytest.fixture
def languages_to_ignore():
    return {"The", "To"}


# ---------------------------------------------------------------------------
# _iso_date
# ---------------------------------------------------------------------------

def test_iso_date_converts_compact_to_dashed():
    assert bm._iso_date("20260518") == "2026-05-18"


# ---------------------------------------------------------------------------
# _week_dir
# ---------------------------------------------------------------------------

def test_week_dir_derives_slug_from_filename(tmp_path):
    input_path = Path("arxiv_papers_20260518_to_20260525.jsonl")
    result = bm._week_dir(input_path, processed_dir=tmp_path)
    assert result == tmp_path / "weeks" / "20260518_to_20260525"


def test_week_dir_falls_back_to_stem_when_no_date_pattern(tmp_path):
    input_path = Path("some_other_file.jsonl")
    result = bm._week_dir(input_path, processed_dir=tmp_path)
    assert result == tmp_path / "weeks" / "some_other_file"


# ---------------------------------------------------------------------------
# _load_papers
# ---------------------------------------------------------------------------

def test_load_papers_parses_jsonl_and_skips_malformed_lines(tmp_path):
    jsonl_path = tmp_path / "papers.jsonl"
    jsonl_path.write_text('{"id": "1"}\nnot json\n{"id": "2"}\n\n', encoding="utf-8")
    papers = bm._load_papers(jsonl_path)
    assert papers == [{"id": "1"}, {"id": "2"}]


# ---------------------------------------------------------------------------
# _load_language_data
# ---------------------------------------------------------------------------

def test_load_language_data_reshapes_json_into_sets(tmp_path):
    path = tmp_path / "language_data.json"
    path.write_text(json.dumps({
        "lang_classes": {"0": ["Swahili", "Arabic"], "1": ["French"]},
        "languages_to_ignore": ["The", "To"],
        "possible_false_positive_languages": {"Gan": "acronym"},
    }), encoding="utf-8")

    lang_classes, ignore, pfp = bm._load_language_data(path)
    assert lang_classes == {0: {"Swahili", "Arabic"}, 1: {"French"}}
    assert ignore == {"The", "To"}
    assert pfp == {"Gan": "acronym"}


def test_load_language_data_defaults_pfp_to_empty_dict(tmp_path):
    path = tmp_path / "language_data.json"
    path.write_text(json.dumps({"lang_classes": {}, "languages_to_ignore": []}), encoding="utf-8")
    _, _, pfp = bm._load_language_data(path)
    assert pfp == {}


# ---------------------------------------------------------------------------
# _merge_detections
# ---------------------------------------------------------------------------

def test_merge_detections_deduplicates_by_language_and_class():
    groups = [
        ([{"language": "Sinhala", "class": 2}], "abstract"),
        ([{"language": "Sinhala", "class": 2}], "html"),
    ]
    merged = bm._merge_detections(groups)
    assert len(merged) == 1
    assert merged[0]["language"] == "Sinhala"
    assert sorted(merged[0]["sources"]) == ["abstract", "html"]


def test_merge_detections_keeps_same_language_different_class_separate():
    groups = [
        ([{"language": "Sinhala", "class": 2}], "abstract"),
        ([{"language": "Sinhala", "class": 3}], "html"),
    ]
    merged = bm._merge_detections(groups)
    assert len(merged) == 2


def test_merge_detections_does_not_duplicate_the_same_source_twice():
    groups = [
        ([{"language": "Sinhala", "class": 2}], "html"),
        ([{"language": "Sinhala", "class": 2}], "html"),
    ]
    merged = bm._merge_detections(groups)
    assert merged[0]["sources"] == ["html"]


def test_merge_detections_skips_entries_without_a_language():
    groups = [([{"class": 2}], "abstract")]
    assert bm._merge_detections(groups) == []


def test_merge_detections_preserves_extra_fields_from_first_occurrence():
    groups = [
        ([{"language": "Gan", "class": 1, "needs_review": True, "flag_reason": "acronym"}], "abstract"),
    ]
    merged = bm._merge_detections(groups)
    assert merged[0]["needs_review"] is True
    assert merged[0]["flag_reason"] == "acronym"


# ---------------------------------------------------------------------------
# _load_pdf_detections
# ---------------------------------------------------------------------------

def test_load_pdf_detections_returns_none_when_not_cached(tmp_path):
    assert bm._load_pdf_detections("2501.00001", tmp_path) is None


def test_load_pdf_detections_reads_cached_detections(tmp_path):
    (tmp_path / "2501.00001.json").write_text(json.dumps({
        "detected_languages": [{"language": "Sinhala", "class": 2}],
    }), encoding="utf-8")
    dets = bm._load_pdf_detections("2501.00001", tmp_path)
    assert dets == [{"language": "Sinhala", "class": 2}]


def test_load_pdf_detections_filters_ignored_languages_case_insensitively(tmp_path):
    (tmp_path / "2501.00001.json").write_text(json.dumps({
        "detected_languages": [
            {"language": "Sinhala", "class": 2},
            {"language": "gan", "class": 1},
        ],
    }), encoding="utf-8")
    dets = bm._load_pdf_detections("2501.00001", tmp_path, languages_to_ignore={"Gan"})
    assert dets == [{"language": "Sinhala", "class": 2}]


def test_load_pdf_detections_returns_none_on_corrupt_cache_file(tmp_path):
    (tmp_path / "2501.00001.json").write_text("not json", encoding="utf-8")
    assert bm._load_pdf_detections("2501.00001", tmp_path) is None


# ---------------------------------------------------------------------------
# _load_html_detections
# ---------------------------------------------------------------------------

def test_load_html_detections_returns_none_when_not_cached(tmp_path, lang_classes):
    dets, sections, details = bm._load_html_detections("2501.00001", tmp_path, lang_classes, {})
    assert dets is None
    assert sections == []
    assert details == []


def test_load_html_detections_builds_detections_per_section(tmp_path, lang_classes):
    (tmp_path / "2501.00001.json").write_text(json.dumps({
        "Introduction": {"detected": ["Arabic"]},
        "Experiments": {"detected": ["Swahili", "Arabic"]},
        "_meta": {"ignored": True},
    }), encoding="utf-8")

    dets, sections, details = bm._load_html_detections("2501.00001", tmp_path, lang_classes, {})
    assert sorted(sections) == ["Experiments", "Introduction"]
    assert {d["language"] for d in dets} == {"Arabic", "Swahili"}
    assert len(details) == 2
    assert all(d["source"] == "html" for d in details)


def test_load_html_detections_skips_sections_with_no_hits(tmp_path, lang_classes):
    (tmp_path / "2501.00001.json").write_text(json.dumps({
        "Conclusion": {"detected": []},
    }), encoding="utf-8")
    dets, sections, details = bm._load_html_detections("2501.00001", tmp_path, lang_classes, {})
    assert dets == []
    assert sections == []


# ---------------------------------------------------------------------------
# _scan_abstract (real text-cleaning + detection pipeline)
# ---------------------------------------------------------------------------

def test_scan_abstract_detects_languages_in_abstract_text(lang_classes, languages_to_ignore):
    paper = {"id": "1", "abstract": "We evaluate on Arabic and Swahili benchmarks."}
    dets = bm._scan_abstract(paper, lang_classes, languages_to_ignore, {})
    assert {d["language"] for d in dets} == {"Arabic", "Swahili"}


def test_scan_abstract_returns_empty_list_for_missing_abstract(lang_classes, languages_to_ignore):
    assert bm._scan_abstract({"id": "1"}, lang_classes, languages_to_ignore, {}) == []


# ---------------------------------------------------------------------------
# assemble_flagged_papers (full per-paper orchestration)
# ---------------------------------------------------------------------------

def test_assemble_flagged_papers_combines_abstract_and_html_cache(tmp_path, lang_classes, languages_to_ignore):
    html_cache_dir = tmp_path / "html_cache"
    pdf_cache_dir = tmp_path / "pdf_cache"
    html_cache_dir.mkdir()
    pdf_cache_dir.mkdir()
    (html_cache_dir / "2501.00001.json").write_text(json.dumps({
        "Experiments": {"detected": ["Swahili"]},
    }), encoding="utf-8")

    papers = [{"id": "http://arxiv.org/abs/2501.00001", "abstract": "We study Arabic dialects."}]
    flagged = bm.assemble_flagged_papers(
        papers, lang_classes, languages_to_ignore, {}, html_cache_dir, pdf_cache_dir
    )

    assert len(flagged) == 1
    languages = {l["language"] for l in flagged[0]["languages"]}
    assert languages == {"Arabic", "Swahili"}
    assert flagged[0]["sources_checked"] == ["abstract", "html"]


def test_assemble_flagged_papers_excludes_papers_with_no_detections(tmp_path, lang_classes, languages_to_ignore):
    html_cache_dir = tmp_path / "html_cache"
    pdf_cache_dir = tmp_path / "pdf_cache"
    html_cache_dir.mkdir()
    pdf_cache_dir.mkdir()

    papers = [{"id": "http://arxiv.org/abs/2501.00002", "abstract": "No tracked languages mentioned here."}]
    flagged = bm.assemble_flagged_papers(
        papers, lang_classes, languages_to_ignore, {}, html_cache_dir, pdf_cache_dir
    )
    assert flagged == []


def test_assemble_flagged_papers_merges_pdf_only_when_html_missing(tmp_path, lang_classes, languages_to_ignore):
    html_cache_dir = tmp_path / "html_cache"
    pdf_cache_dir = tmp_path / "pdf_cache"
    html_cache_dir.mkdir()
    pdf_cache_dir.mkdir()
    (pdf_cache_dir / "2501.00003.json").write_text(json.dumps({
        "detected_languages": [{"language": "Swahili", "class": 0}],
    }), encoding="utf-8")

    papers = [{"id": "http://arxiv.org/abs/2501.00003", "abstract": "No mentions here."}]
    flagged = bm.assemble_flagged_papers(
        papers, lang_classes, languages_to_ignore, {}, html_cache_dir, pdf_cache_dir
    )
    assert len(flagged) == 1
    assert flagged[0]["sources_checked"] == ["abstract", "pdf"]
    assert flagged[0]["languages"][0]["language"] == "Swahili"


# ---------------------------------------------------------------------------
# _flagged_from_detected_jsonl (CI code path)
# ---------------------------------------------------------------------------

def test_flagged_from_detected_jsonl_builds_records_from_precomputed_detections(tmp_path):
    detected_path = tmp_path / "detected.jsonl"
    record = {
        "paper": {"id": "http://arxiv.org/abs/2501.00001", "title": "Test"},
        "sources_checked": ["abstract", "html"],
        "sections": {
            "abstract": {"source": "abstract", "detected_languages": [{"language": "Arabic", "class": 0}]},
        },
    }
    detected_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    flagged = bm._flagged_from_detected_jsonl(detected_path)
    assert len(flagged) == 1
    assert flagged[0]["languages"] == [{"language": "Arabic", "class": 0, "sources": ["abstract"]}]
    assert flagged[0]["sections_with_detections"] == ["abstract"]


def test_flagged_from_detected_jsonl_skips_records_with_no_detections(tmp_path):
    detected_path = tmp_path / "detected.jsonl"
    record = {"paper": {"id": "1"}, "sources_checked": ["abstract"], "sections": {}}
    detected_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert bm._flagged_from_detected_jsonl(detected_path) == []


# ---------------------------------------------------------------------------
# build_and_save (end-to-end)
# ---------------------------------------------------------------------------

def test_build_and_save_assembles_from_caches_when_no_detected_jsonl(tmp_path, lang_classes, languages_to_ignore):
    input_path = tmp_path / "arxiv_papers_20260518_to_20260525.jsonl"
    input_path.write_text(
        json.dumps({"id": "http://arxiv.org/abs/1", "published": "2026-05-18T00:00:00", "abstract": "Arabic study."}) + "\n",
        encoding="utf-8",
    )

    lang_data_path = tmp_path / "language_data.json"
    lang_data_path.write_text(json.dumps({
        "lang_classes": {"0": ["Arabic"]},
        "languages_to_ignore": [],
    }), encoding="utf-8")

    output_dir = tmp_path / "weeks" / "20260518_to_20260525"
    manifest_path = bm.build_and_save(input_path, output_dir, lang_data_path, window_days=7)

    assert manifest_path == output_dir / "langtrend_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["counts"]["papers"] == 1
    assert manifest["counts"]["flagged_papers"] == 1
    assert manifest["week_start"] == "2026-05-18"
    assert manifest["week_end"] == "2026-05-25"

    # "latest" pointer is also written at the processed root (two levels above the week dir)
    latest_path = tmp_path / "langtrend_manifest_last_7_days.json"
    assert latest_path.exists()


def test_build_and_save_prefers_precomputed_detected_jsonl_when_present(tmp_path, lang_classes, languages_to_ignore):
    input_path = tmp_path / "arxiv_papers_20260518_to_20260525.jsonl"
    input_path.write_text(
        json.dumps({"id": "http://arxiv.org/abs/1", "published": "2026-05-18T00:00:00", "abstract": "irrelevant"}) + "\n",
        encoding="utf-8",
    )
    lang_data_path = tmp_path / "language_data.json"
    lang_data_path.write_text(json.dumps({"lang_classes": {}, "languages_to_ignore": []}), encoding="utf-8")

    output_dir = tmp_path / "weeks" / "20260518_to_20260525"
    output_dir.mkdir(parents=True)
    detected_path = output_dir / "arxiv_papers_20260518_to_20260525_detected.jsonl"
    detected_path.write_text(json.dumps({
        "paper": {"id": "http://arxiv.org/abs/1", "published": "2026-05-18T00:00:00"},
        "sources_checked": ["abstract"],
        "sections": {"abstract": {"source": "abstract", "detected_languages": [{"language": "Arabic", "class": 0}]}},
    }) + "\n", encoding="utf-8")

    manifest_path = bm.build_and_save(input_path, output_dir, lang_data_path, window_days=7)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Detected via the precomputed jsonl, not by re-scanning the (irrelevant) abstract text.
    assert manifest["counts"]["flagged_papers"] == 1
    assert manifest["language_counts"] == [{"language": "Arabic", "count": 1}]
