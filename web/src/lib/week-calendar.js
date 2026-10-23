// Logic behind WeekCalendar.astro's activity counts: per-day and per-week
// paper totals shown in the calendar cells, plus the "latest scan" summary
// line — visible evidence that the weekly pipeline is running and recording.

import { compactNumber } from './site-stats.js';

/**
 * Compact counts for the calendar, from loadAllWeeksData() rows.
 * days: submission date -> papers that day (each week's daily_series);
 * weeks: week start -> { papers, flagged }. Late-announced papers count in
 * their week's total but not on any day (their dates fall outside the week).
 */
export function buildCalendarStats(allWeeksData = []) {
  const days = {};
  const weeks = {};
  let totalPapers = 0;
  for (const week of allWeeksData) {
    if (!week?.weekStart) continue;
    weeks[week.weekStart] = {
      papers: week.papers ?? 0,
      flagged: week.flaggedPapers ?? 0,
      late: week.lateAnnounced ?? 0,
    };
    totalPapers += week.papers ?? 0;
    for (const day of week.dailySeries ?? []) {
      if (day?.date && day.papers) days[day.date] = (days[day.date] ?? 0) + day.papers;
    }
  }
  const starts = Object.keys(weeks).sort();
  const latestStart = starts[starts.length - 1] ?? null;
  return {
    days,
    weeks,
    totalPapers,
    firstWeek: starts[0] ?? null,
    latest: latestStart ? { weekStart: latestStart, ...weeks[latestStart] } : null,
    lastDay: latestStart ? addDays(latestStart, 6) : null,
    dayBreaks: dayBreaks(days),
  };
}

/**
 * Shading bins for the day chips: four equal-width bins with a round step
 * covering the 95th-percentile day, e.g. [50, 100, 150] when it is 181.
 * Equal widths (not quantiles) keep the scale honest and the key readable,
 * and don't saturate when early weeks were smaller. Sizing from p95 rather
 * than the max means one deadline-rush day can't double the step and reshade
 * every past week; outliers just land in the open-ended top bin. Returns the
 * lower bound of each bin after the first; [] when there's nothing to shade.
 */
const BIN_STEPS = [5, 10, 20, 25, 50, 100, 200, 250, 500, 1000];
export function dayBreaks(days = {}) {
  const values = Object.values(days).filter(n => n > 0).sort((a, b) => a - b);
  if (!values.length) return [];
  const p95 = values[Math.floor(0.95 * (values.length - 1))];
  const step = BIN_STEPS.find(s => s * 4 > p95) ?? Math.ceil(p95 / 4000) * 1000;
  return [step, step * 2, step * 3];
}

/** Bin index (0 = fewest papers) for a daily count. */
export function dayBin(n, breaks = []) {
  return breaks.filter(b => n >= b).length;
}

/** Key labels for each bin: ["<50", "50–99", "100–149", "150+"]. */
export function dayBinLabels(breaks = []) {
  if (!breaks.length) return [];
  return [
    `<${breaks[0]}`,
    ...breaks.slice(1).map((b, i) => `${breaks[i]}–${b - 1}`),
    `${breaks[breaks.length - 1]}+`,
  ];
}

function addDays(slug, n) {
  const d = new Date(`${slug}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Cell/badge label: "131", "1.2k"; null for missing/zero so nothing renders. */
export function countLabel(n) {
  return n ? compactNumber(n) : null;
}

/**
 * Hover/aria text for a week row's total badge. Spells out late-announced
 * papers, since they make the total larger than the sum of the day cells.
 */
export function weekTooltip(week) {
  if (!week) return '';
  let text = `${week.papers.toLocaleString('en-US')} paper${week.papers === 1 ? '' : 's'} scanned`;
  if (week.late) text += ` (incl. ${week.late.toLocaleString('en-US')} announced late, submitted in earlier weeks)`;
  if (week.flagged) text += ` · ${week.flagged.toLocaleString('en-US')} with detected languages`;
  return text;
}

/**
 * Day-cell label/aria text: "106" / "106 papers scanned"; null when there's no
 * count. Styled as a chip (and keyed in the legend) so it doesn't read as a date.
 */
export function dayLabel(n) {
  const label = countLabel(n);
  return label ? { text: label, aria: `${n.toLocaleString('en-US')} paper${n === 1 ? '' : 's'} scanned` } : null;
}

// A day inside the tracked range with no papers: its batch wasn't in that
// week's scan — announced after it (Labor Day 2026) or not yet returned by the
// search API (10–11 Oct 2026) — and was counted in the next week.
export const EMPTY_DAY_NOTE = "No papers recorded for this day: they weren't in this week's scan and are counted in the next week";

/** True for a date in the tracked range (first week's Monday … latest week's Sunday) with no papers. */
export function isEmptyTrackedDay(date, stats) {
  if (!stats?.firstWeek || !stats?.lastDay) return false;
  return date >= stats.firstWeek && date <= stats.lastDay && !stats.days?.[date];
}

/**
 * The chip a day cell shows: { text, aria, className }, or null for no chip.
 * A count gets a shading class (bin-0 … bin-3, see dayBreaks); an empty day
 * inside the tracked range gets an outlined dash (is-empty).
 */
export function dayChip(date, stats) {
  const n = stats?.days?.[date];
  const label = dayLabel(n);
  if (label) {
    const bin = stats.dayBreaks?.length ? ` bin-${dayBin(n, stats.dayBreaks)}` : '';
    return { ...label, className: `fc-day-count${bin}` };
  }
  if (isEmptyTrackedDay(date, stats)) return { text: '–', aria: EMPTY_DAY_NOTE, className: 'fc-day-count is-empty' };
  return null;
}

function fmtDay(slug) {
  return new Date(`${slug}T12:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
}

/** ["Latest scan: week of 12 Oct · 889 papers", "14,404 scanned since 27 Apr 2026"] (shown as two lines), or null without data. */
export function scanSummary(stats) {
  if (!stats?.latest) return null;
  const since = new Date(`${stats.firstWeek}T12:00:00Z`).toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
  });
  return [
    `Latest scan: week of ${fmtDay(stats.latest.weekStart)} · ${stats.latest.papers.toLocaleString('en-US')} papers`,
    `${stats.totalPapers.toLocaleString('en-US')} scanned since ${since}`,
  ];
}

/**
 * A count element with screen-reader text. aria-label isn't read out on a
 * plain span, so the visible label ("106", "1.1k") is aria-hidden and a
 * visually hidden .sr-only sibling carries the sentence; title keeps the
 * mouse tooltip. `doc` is a parameter so it can be tested without a DOM.
 */
export function labelledCount(doc, className, visible, spoken) {
  const wrap = doc.createElement('span');
  wrap.className = className;
  wrap.title = spoken;
  const shown = doc.createElement('span');
  shown.setAttribute('aria-hidden', 'true');
  shown.textContent = visible;
  const sr = doc.createElement('span');
  sr.className = 'sr-only';
  sr.textContent = spoken;
  wrap.append(shown, sr);
  return wrap;
}

/** FullCalendar dayHeaderFormat: single letters on narrow screens, where "Mon"/"Wed" overflow ~37px columns. */
export function dayHeaderFormat(isNarrow) {
  return { weekday: isNarrow ? 'narrow' : 'short' };
}
