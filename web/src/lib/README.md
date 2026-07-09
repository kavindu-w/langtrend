# src/lib

Data loading and pure presentation logic for the Astro frontend. Every module here (except `data.js`, which touches the filesystem) is framework-free JS with a colocated `*.test.js` — components import from these modules rather than embedding logic inline, so the logic can be unit-tested without rendering Astro.

```text
lib/
├── data.js              Reads manifests from disk, shapes them for pages
├── paper-table.js        Per-paper chip/section/badge shaping for PaperTable
├── trend-charts.js       Chart math (bump/pie/line paths, period aggregation) for TrendCharts
├── language-colors.js    Language → class/border-color/fill-color mapping
├── abstract-math.js      Server-side KaTeX rendering of LaTeX abstracts
└── text-utils.js         Diacritic-folding search normalization
```

| File | Purpose |
|------|---------|
| `data.js` | The sole data-loading layer. Resolves the data root (local dev vs. Vercel-bundled), reads `langtrend_manifest*.json`/JSONL files under `../data/`, and exposes `loadSiteData`, `loadAllWeeksData`, `getAvailableWeeks`. `countLanguages` here duplicates the studied/mentioned-only bucketing rules in `langtrend/manifest.py`'s `build_snapshot_manifest`|
| `paper-table.js` | Shapes one flagged-paper record into everything `PaperTable.astro` renders: language chips (with judge-verdict/needs-review flags), per-section chip groups, source-coverage badges, and the flattened search text. `buildWeekApiPaper` is the equivalent shaping for the JSON API route. |
| `trend-charts.js` | Pure SVG/math helpers behind `TrendCharts.astro` — line/pie/bump-chart path builders, week/month/year period aggregation (`aggregateTrendPeriods`), and bump-chart leader-line lane assignment to avoid overlapping labels. |
| `language-colors.js` | Maps a language name to its resource-class index (`languageBorderClass`, from the taxonomy in `language_data.json`) or a deterministic hash-based fill color, for languages outside the taxonomy. |
| `abstract-math.js` | Renders `$...$` LaTeX math in arXiv abstracts to KaTeX HTML server-side (build time / API route), so the browser never loads the KaTeX JS engine. |
| `text-utils.js` | `foldSearchText` — strips diacritics/typographic quotes for ASCII-friendly search matching. Kept dependency-free since it's also imported by the client-side search script bundled into `PaperTable.astro`, which must not pull in server-only modules like `abstract-math.js`. |

Run the whole suite from `web/`:

```bash
npm test
```

Run a single file:

```bash
npx vitest run src/lib/paper-table.test.js
```
