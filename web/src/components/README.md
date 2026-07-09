# src/components

`.astro` components rendered by the pages in `src/pages/`. Each one keeps its markup/styles in the component and delegates non-trivial logic to a paired module in `src/lib/` (see `src/lib/README.md`) so that logic has unit tests.

```text
components/
├── PaperTable.astro     Searchable/filterable table of flagged papers
├── TrendCharts.astro    Language/class/coverage charts for a week or period
└── WeekCalendar.astro   Week picker (calendar, period range, all-papers mode)
```

| Component | Purpose |
|-----------|---------|
| `PaperTable.astro` | Renders the flagged-papers list: language chips per paper (colored by resource class, flagged for review, or dimmed for `mentioned_only`/judge-rejected), per-section detection breakdowns, and a client-side search/filter bar. Shaping comes from `src/lib/paper-table.js`. |
| `TrendCharts.astro` | Renders the statistics panel: language/class bar and pie charts for the current week, plus a bump chart tracking top languages across weeks/months/years (aggregated via `src/lib/trend-charts.js`). Uses `d3`'s `scaleBand`/`scaleLinear` for axis scales; all path geometry is computed in `lib/trend-charts.js`, not here. |
| `WeekCalendar.astro` | The week/period/all-papers view picker on the homepage. Markup only — its interactive behavior (FullCalendar wiring, mode switching, URL navigation) lives in `src/scripts/week-calendar-client.js`, loaded as a client script. |

Props are typed inline with Astro's `Astro.props as {...}` pattern rather than separate `.d.ts` files — check the top of each component for its expected shape before wiring up a new caller.
