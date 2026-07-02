# notebooks

Exploratory notebooks used during development — not part of the automated pipeline and not run in CI. Useful when manually reviewing detection quality or trying out text-cleaning changes before committing them.

```text
notebooks/
├── cleaning_check.ipynb   Manual review of html_cache/ detections for a week
└── testing.ipynb          Scratch pad for text_cleaning function experiments
```

| Notebook | Purpose |
|----------|---------|
| `cleaning_check.ipynb` | Loads a week's `html_cache/` detections and `language_data.json`, then lists every section where a language (class 0–4) was detected, for manual spot-checking of true/false positives. |
| `testing.ipynb` | Scratch pad for running `langtrend.text_cleaning` functions (`clean_paper_text_for_language_screening`, `detect_languages_in_text`) against hand-picked sample text — useful when debugging why a specific string is or isn't producing a detection. |

Both notebooks expect to be run from within `notebooks/` (they use relative paths like `../data/processed/...`).
