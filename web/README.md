# web

[![Built with Astro](https://img.shields.io/badge/Built%20with-Astro-BC52EE.svg?logo=astro&logoColor=white)](https://astro.build)

Astro frontend for the LangTrend dashboard, statically built and deployed to GitHub Pages (Vercel is also supported — see Deploy below). It reads the Python pipeline's committed JSON manifests under `../data/` and never talks to arXiv or an LLM directly.

```text
web/
├── src/
│   ├── pages/        Routes — index, about, per-week pages, JSON API
│   ├── components/   .astro components (paper table, charts, calendar)
│   ├── layouts/       Shared page shell
│   ├── lib/           Data loading + pure logic, unit-tested
│   ├── scripts/       Client-side JS bundled into a component
│   └── styles/        Global CSS
├── public/            Static assets copied as-is to the build output
└── astro.config.mjs   Site config (base path, adapters)
```

| Directory | Contents |
|-----------|----------|
| `src/pages/` | File-based routes: `index.astro` (latest week), `about.astro`, `weeks/[slug].astro` (historical week), `api/weeks/[slug].json.ts` (JSON endpoint). See `src/pages/README.md`. |
| `src/components/` | `.astro` components consumed by the pages — `PaperTable`, `TrendCharts`, `WeekCalendar`. See `src/components/README.md`. |
| `src/layouts/` | `BaseLayout.astro`, the shared `<head>`/nav/footer shell every page renders into. See `src/layouts/README.md`. |
| `src/lib/` | The data-loading layer (`data.js`) plus pure, unit-tested helper modules each component imports its rendering logic from. See `src/lib/README.md`. |
| `src/scripts/` | Client-side JS shipped to the browser (not server-rendered), e.g. the FullCalendar wiring behind `WeekCalendar.astro`. See `src/scripts/README.md`. |
| `src/styles/` | `global.css`, imported once by `BaseLayout.astro`. See `src/styles/README.md`. |

## Setup

```bash
cd web
npm install
```

## Run (dev server)

```bash
make web-dev     # or: cd web && npm run dev
```

## Build

```bash
make web-build    # or: cd web && npm run build
```

## Test

```bash
make test-web                       # or: cd web && npm test
cd web && npm run test:coverage     # with v8 coverage
```
Tests are colocated `*.test.js` files next to the module they cover, under `src/lib/`.

## Deploy to Vercel

```bash
cd web
npm run build
npm run deploy
```

## Data contract

`src/lib/data.js` resolves a data root that differs between local dev (`../data`, relative to `web/`) and the Vercel-bundled path (`data/` alongside the built function), then reads the manifest JSON/JSONL files the Python pipeline commits under `data/processed/`. 
