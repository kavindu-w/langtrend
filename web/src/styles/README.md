# src/styles

```text
styles/
└── global.css   Site-wide CSS, imported once by BaseLayout.astro
```

One stylesheet, imported a single time in `src/layouts/BaseLayout.astro` (alongside KaTeX's own CSS for rendered abstract math) and applied to every page. It isn't split per-component — Astro component `<style>` blocks aren't used here, so all layout, chip/badge, calendar, and chart-tile styling lives in this one file, roughly ordered by the component it styles (focus rings and base resets first, then `WeekCalendar`, then `TrendCharts` tiles, then `PaperTable` chips/sections). CSS custom properties for the color palette (`--bg`, `--panel`, `--text`, `--accent`, etc.) are defined on `:root` at the top; reach for those before hardcoding a color.
