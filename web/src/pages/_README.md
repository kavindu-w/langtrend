# src/pages

File-based Astro routes. Each `.astro`/`.ts` file's path under this directory is its URL path. Pages call `loadSiteData`/`getAvailableWeeks`/`loadAllWeeksData` from `src/lib/data.js` at build time and pass the result as props to the components in `src/components/`.

```text
pages/
├── index.astro              / — latest week's dashboard
├── about.astro               /about/ — pipeline + language-class explainer
├── weeks/
│   └── [slug].astro          /weeks/<YYYY-MM-DD>/ — one historical week
└── api/
    └── weeks/
        └── [slug].json.ts    /api/weeks/<YYYY-MM-DD>.json — flagged papers as JSON
```

| Page | Purpose |
|------|---------|
| `index.astro` | The homepage: hero, `WeekCalendar`, `TrendCharts`, and `PaperTable` for the current "last 7 days" manifest (`loadSiteData()` with no week argument). |
| `about.astro` | Static explainer page — describes the detection pipeline and lists the 6 language resource classes (`data/processed/language_data.json`'s `lang_classes`), with hand-maintained example languages per class. |
| `weeks/[slug].astro` | Same layout as `index.astro` but scoped to one historical week. `getStaticPaths` enumerates every week directory under `data/processed/weeks/` via `getAvailableWeeks()`, so Astro prerenders one static page per week at build time. |
| `api/weeks/[slug].json.ts` | Returns the week's flagged papers as JSON (shaped by `buildWeekApiPaper` in `src/lib/paper-table.js`), one static file per week via the same `getStaticPaths` pattern. Used for programmatic/external access to a week's data, separate from the HTML page. |

New pages should follow the existing pattern: fetch data via `src/lib/data.js` in the frontmatter, pass shaped props into `src/components/` rather than reshaping manifest JSON inline in the template.
