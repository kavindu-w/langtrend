"""
Unit tests for scripts/extract_language_data.py.

Run with: pytest tests/test_extract_language_data.py -v
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import extract_language_data as eld


def _write_class_files(base: Path, classes: dict[int, list[str]]):
    base.mkdir(parents=True, exist_ok=True)
    for class_id, languages in classes.items():
        (base / f"{class_id}.txt").write_text("\n".join(languages), encoding="utf-8")


def test_extract_language_data_writes_sorted_lang_classes(tmp_path):
    submodule = tmp_path / "submodule"
    base = submodule / "Language_List/Language_Classes_According_To/DataSet_Availability"
    _write_class_files(base, {0: ["Swahili", "Arabic"], 1: ["French"]})

    output_path = tmp_path / "language_data.json"
    eld.extract_language_data(submodule, output_path)

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["lang_classes"]["0"] == ["Arabic", "Swahili"]
    assert data["lang_classes"]["1"] == ["French"]


def test_extract_language_data_includes_ignore_list_and_pfp_map(tmp_path):
    submodule = tmp_path / "submodule"
    base = submodule / "Language_List/Language_Classes_According_To/DataSet_Availability"
    _write_class_files(base, {0: ["Swahili"]})

    output_path = tmp_path / "language_data.json"
    eld.extract_language_data(submodule, output_path)

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert "Gan" in data["possible_false_positive_languages"]
    assert data["languages_to_ignore"] == sorted(eld._LANGUAGES_TO_IGNORE)


def test_extract_language_data_tolerates_missing_class_files(tmp_path):
    submodule = tmp_path / "submodule"
    base = submodule / "Language_List/Language_Classes_According_To/DataSet_Availability"
    # Only class 0 present; classes 1-5 missing files should just be skipped with a warning.
    _write_class_files(base, {0: ["Swahili"]})

    output_path = tmp_path / "language_data.json"
    eld.extract_language_data(submodule, output_path)

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["lang_classes"] == {"0": ["Swahili"]}


def test_extract_language_data_raises_when_no_class_files_present(tmp_path):
    submodule = tmp_path / "submodule"
    base = submodule / "Language_List/Language_Classes_According_To/DataSet_Availability"
    base.mkdir(parents=True)  # directory exists, but no 0.txt..5.txt inside it

    with pytest.raises(RuntimeError):
        eld.extract_language_data(submodule, tmp_path / "out.json")


def test_extract_language_data_raises_when_submodule_dir_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        eld.extract_language_data(tmp_path / "does_not_exist", tmp_path / "out.json")


def test_extract_language_data_creates_output_parent_dirs(tmp_path):
    submodule = tmp_path / "submodule"
    base = submodule / "Language_List/Language_Classes_According_To/DataSet_Availability"
    _write_class_files(base, {0: ["Swahili"]})

    output_path = tmp_path / "nested" / "dir" / "language_data.json"
    eld.extract_language_data(submodule, output_path)
    assert output_path.exists()
