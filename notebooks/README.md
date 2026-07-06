# notebooks

Exploratory notebooks used during development — not part of the automated pipeline and not run in CI. Useful when manually reviewing detection quality or trying out text-cleaning changes before committing them.

```text
notebooks/
├── cleaning_check.ipynb     Manual review of html_cache/ detections for a week
├── judge_check.ipynb        Interactive testing of the LLM judge on single papers
├── single_paper_check.ipynb Interactive pipeline test on any one arXiv paper
└── testing.ipynb            Scratch pad for text_cleaning function experiments
```

| Notebook | Purpose |
|----------|---------|
| `cleaning_check.ipynb` | Loads a week's `html_cache/` detections and `language_data.json`, then lists every section where a language (class 0–4) was detected, for manual spot-checking of true/false positives. |
| `judge_check.ipynb` | Runs the LLM-as-judge stage (`langtrend/judge.py`) on hand-picked papers from a processed week: previews the assembled prompt/context, prints verdicts (studied / mentioned_only / false_positive) side by side with the regex detections, and compares against cached `judge_cache/` verdicts. Needs LLM judge config in `../.env` (see `.env.example`). |
| `single_paper_check.ipynb` | Same idea as `judge_check.ipynb` but for trying the pipeline on an arbitrary arXiv paper (not just ones from a processed week) — fetches one paper, runs the abstract → HTML → PDF detection cascade, lets you inspect the cleaned text per section, and optionally judges it. Writes to `../data/sandbox/`, same as `make test-paper` / `scripts/test_single_paper.py`. |
| `testing.ipynb` | Scratch pad for running `langtrend.text_cleaning` functions (`clean_paper_text_for_language_screening`, `detect_languages_in_text`) against hand-picked sample text — useful when debugging why a specific string is or isn't producing a detection. |

All notebooks expect to be run from within `notebooks/` (they use relative paths like `../data/processed/...`).
