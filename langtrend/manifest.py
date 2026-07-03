from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_detections(
    raw_languages: list[str],
    lang_classes: dict[int, set[str]],
    possible_false_positives: dict[str, str] | None = None,
    languages_to_ignore: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Attach class IDs and optional review flags to detected language strings."""
    ignore = languages_to_ignore or set()
    ignore_lower = {v.lower() for v in ignore}
    pfp = possible_false_positives or {}
    result = []
    for language in raw_languages:
        if language in ignore or language.lower() in ignore_lower:
            continue
        for class_id, langs in lang_classes.items():
            if language in langs:
                entry: dict[str, Any] = {"language": language, "class": class_id}
                if language in pfp:
                    entry["needs_review"] = True
                    entry["flag_reason"] = pfp[language]
                result.append(entry)
                break
    return result


def save_json(data: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return path


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build_snapshot_manifest(
    papers: list[dict[str, Any]],
    flagged_papers: list[dict[str, Any]],
    window_days: int,
    category_query: str,
    week_start: str | None = None,
    week_end: str | None = None,
    pdf_failed_no_detection: int = 0,
) -> dict[str, Any]:
    # "studied" bucket also holds not-yet-judged detections (no judge_verdict
    # at all) so counts don't regress to near-zero for weeks that haven't run
    # the LLM judge yet — only an explicit "mentioned_only" verdict moves a
    # detection into the separate, disjoint mentioned-only bucket.
    language_studied_counts: Counter[str] = Counter()
    language_mentioned_counts: Counter[str] = Counter()
    class_studied_counts: Counter[int] = Counter()
    class_mentioned_counts: Counter[int] = Counter()
    daily_papers: Counter[str] = Counter()
    daily_flagged: Counter[str] = Counter()
    judge_counts: Counter[str] = Counter()
    judged_papers = 0
    flagged_paper_count = 0

    for paper in papers:
        published = str(paper.get("published", ""))[:10]
        if published:
            daily_papers[published] += 1

    for flagged in flagged_papers:
        paper = flagged.get("paper", {})
        published = str(paper.get("published", ""))[:10]
        languages = flagged.get("languages", [])
        if any("judge_verdict" in detected for detected in languages):
            judged_papers += 1
        # A paper whose detections are all judged false positives stays in
        # flagged_papers (auditable) but is excluded from the counts.
        all_false_positive = bool(languages) and all(
            detected.get("judge_verdict") == "false_positive" for detected in languages
        )
        if not all_false_positive:
            flagged_paper_count += 1
            if published:
                daily_flagged[published] += 1
        for detected in languages:
            verdict = detected.get("judge_verdict")
            if verdict:
                judge_counts[verdict] += 1
            if verdict == "false_positive":
                continue
            language = detected.get("language")
            class_id = detected.get("class")  # key is "class", not "class_id"
            bucket_lang, bucket_class = (
                (language_mentioned_counts, class_mentioned_counts)
                if verdict == "mentioned_only"
                else (language_studied_counts, class_studied_counts)
            )
            if language:
                bucket_lang[language] += 1
            if class_id is not None:
                bucket_class[int(class_id)] += 1

    all_languages = sorted(set(language_studied_counts) | set(language_mentioned_counts))
    all_classes = sorted(set(class_studied_counts) | set(class_mentioned_counts))
    # Languages seen only via "mentioned_only" (never studied, never unjudged)
    # — these would otherwise vanish entirely from the studied-only headline.
    mentioned_only_languages = set(language_mentioned_counts) - set(language_studied_counts)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "week_start": week_start,
        "week_end": week_end,
        "query": category_query,
        "counts": {
            "papers": len(papers),
            "flagged_papers": flagged_paper_count,
            "unique_languages": len(language_studied_counts),
            "unique_languages_mentioned_only": len(mentioned_only_languages),
            "pdf_failed_no_detection": pdf_failed_no_detection,
            "judge": {
                "judged_papers": judged_papers,
                "judged_languages": sum(judge_counts.values()),
                "studied": judge_counts.get("studied", 0),
                "mentioned_only": judge_counts.get("mentioned_only", 0),
                "false_positive": judge_counts.get("false_positive", 0),
            },
        },
        "language_counts": [
            {
                "language": language,
                "count": language_studied_counts.get(language, 0) + language_mentioned_counts.get(language, 0),
                "studied": language_studied_counts.get(language, 0),
                "mentioned_only": language_mentioned_counts.get(language, 0),
            }
            for language in sorted(
                all_languages,
                key=lambda lang: language_studied_counts.get(lang, 0) + language_mentioned_counts.get(lang, 0),
                reverse=True,
            )
        ],
        "class_counts": [
            {
                "class_id": class_id,
                "count": class_studied_counts.get(class_id, 0) + class_mentioned_counts.get(class_id, 0),
                "studied": class_studied_counts.get(class_id, 0),
                "mentioned_only": class_mentioned_counts.get(class_id, 0),
            }
            for class_id in all_classes
        ],
        "daily_series": [
            {
                "date": date,
                "papers": daily_papers.get(date, 0),
                "flagged": daily_flagged.get(date, 0),
            }
            for date in sorted(daily_papers.keys() | daily_flagged.keys())
        ],
        "papers": papers,
        "flagged_papers": flagged_papers,
    }


def load_snapshot_inputs(
    data_root: str | Path = "data",
    window_days: int = 7,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = Path(data_root)
    raw_path = root / "raw" / f"arxiv_papers_last_{window_days}_days.jsonl"
    flagged_path = root / "processed" / f"papers_with_tracked_langs_last_{window_days}_days.jsonl"
    return _load_jsonl(raw_path), _load_jsonl(flagged_path)
