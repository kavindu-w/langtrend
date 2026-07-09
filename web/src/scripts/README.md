# src/scripts

Client-side JS that ships to the browser as-is, rather than running at build time in an Astro frontmatter. Distinct from `src/lib/`, whose modules run server-side (or are imported by a client script, like `text-utils.js`) — files here are the entry points actually bundled into the page.

```text
scripts/
└── week-calendar-client.js   FullCalendar wiring + mode switching for WeekCalendar.astro
```

`week-calendar-client.js` is loaded as a client script by `WeekCalendar.astro`. It initializes a FullCalendar `dayGridPlugin` instance, handles the week/period/all-papers mode tabs, the year/month jump selects, and navigates between weeks by reading `data-active-week`/`data-available-weeks`/`data-base-url` attributes off the component's root element rather than receiving props directly (client scripts can't receive Astro props). Pure date-math helpers at the top of the file (`isoDate`, `startOfWeek`, `isoWeekNumber`, `fmtShort`, `fmtTitle`, `buildWeekLabel`) have no DOM dependency and are kept separate from the DOM-wiring code below them for testability.
