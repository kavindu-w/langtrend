import { describe, expect, it } from 'vitest';
import { buildCalendarStats, countLabel, dayBin, dayBinLabels, dayBreaks, dayChip, dayHeaderFormat, dayLabel, EMPTY_DAY_NOTE, isEmptyTrackedDay, labelledCount, scanSummary, weekTooltip } from './week-calendar.js';

const weeks = [
  {
    weekStart: '2026-10-05', papers: 620, flaggedPapers: 253,
    dailySeries: [{ date: '2026-10-05', papers: 131 }, { date: '2026-10-06', papers: 124 }],
  },
  {
    weekStart: '2026-10-12', papers: 889, flaggedPapers: 613, lateAnnounced: 166,
    dailySeries: [{ date: '2026-10-12', papers: 150 }, { date: '2026-10-13', papers: 0 }],
  },
];

describe('buildCalendarStats', () => {
  it('maps days and weeks and totals papers', () => {
    const stats = buildCalendarStats(weeks);
    expect(stats.days).toEqual({ '2026-10-05': 131, '2026-10-06': 124, '2026-10-12': 150 });
    expect(stats.weeks['2026-10-12']).toEqual({ papers: 889, flagged: 613, late: 166 });
    expect(stats.weeks['2026-10-05']).toEqual({ papers: 620, flagged: 253, late: 0 });
    expect(stats.totalPapers).toBe(1509);
    expect(stats.firstWeek).toBe('2026-10-05');
    expect(stats.latest).toEqual({ weekStart: '2026-10-12', papers: 889, flagged: 613, late: 166 });
    expect(stats.lastDay).toBe('2026-10-18');
    expect(stats.dayBreaks).toEqual([50, 100, 150]); // p95 day 150 -> step 50
  });

  it('latest is the newest week regardless of input order', () => {
    expect(buildCalendarStats([...weeks].reverse()).latest.weekStart).toBe('2026-10-12');
  });

  it('handles no data', () => {
    const stats = buildCalendarStats([]);
    expect(stats).toEqual({ days: {}, weeks: {}, totalPapers: 0, firstWeek: null, latest: null, lastDay: null, dayBreaks: [] });
    expect(scanSummary(stats)).toBeNull();
  });

  it('skips rows without a week start and missing series', () => {
    const stats = buildCalendarStats([null, { papers: 5 }, { weekStart: '2026-10-19', papers: 3 }]);
    expect(stats.weeks).toEqual({ '2026-10-19': { papers: 3, flagged: 0, late: 0 } });
    expect(stats.days).toEqual({});
  });
});

describe('labels', () => {
  it('countLabel hides zero/missing and compacts thousands', () => {
    expect(countLabel(0)).toBeNull();
    expect(countLabel(undefined)).toBeNull();
    expect(countLabel(131)).toBe('131');
    expect(countLabel(1234)).toBe('1.2k');
  });

  it('weekTooltip explains late papers, which make the total exceed its days', () => {
    expect(weekTooltip({ papers: 1055, flagged: 680, late: 166 }))
      .toBe('1,055 papers scanned (incl. 166 announced late, submitted in earlier weeks) · 680 with detected languages');
  });

  it('dayLabel gives the compact count and readable aria text', () => {
    expect(dayLabel(106)).toEqual({ text: '106', aria: '106 papers scanned' });
    expect(dayLabel(1234)).toEqual({ text: '1.2k', aria: '1,234 papers scanned' });
    expect(dayLabel(1)).toEqual({ text: '1', aria: '1 paper scanned' });
    expect(dayLabel(0)).toBeNull();
  });

  it('isEmptyTrackedDay flags only empty days inside the tracked range', () => {
    const stats = buildCalendarStats(weeks);
    expect(isEmptyTrackedDay('2026-10-10', stats)).toBe(true);   // weekend whose batch went to the next week
    expect(isEmptyTrackedDay('2026-10-05', stats)).toBe(false);  // has papers
    expect(isEmptyTrackedDay('2026-10-04', stats)).toBe(false);  // before tracking began
    expect(isEmptyTrackedDay('2026-10-19', stats)).toBe(false);  // after the latest week
    expect(isEmptyTrackedDay('2026-10-10', {})).toBe(false);
  });

  it('weekTooltip mentions detected languages only when there are some', () => {
    expect(weekTooltip({ papers: 889, flagged: 613 })).toBe('889 papers scanned · 613 with detected languages');
    expect(weekTooltip({ papers: 1, flagged: 0 })).toBe('1 paper scanned');
    expect(weekTooltip(undefined)).toBe('');
  });

  it('scanSummary names the latest week and the running total', () => {
    expect(scanSummary(buildCalendarStats(weeks)))
      .toEqual(['Latest scan: week of 12 Oct · 889 papers', '1,509 scanned since 5 Oct 2026']);
  });
});

describe('day shading bins', () => {
  const days = { a: 19, b: 48, c: 52, d: 60, e: 75, f: 80, g: 100, h: 104, i: 196 };

  it('dayBreaks uses four equal round-step bins covering the 95th-percentile day', () => {
    expect(dayBreaks(days)).toEqual([50, 100, 150]);
    expect(dayBreaks({ a: 199 })).toEqual([50, 100, 150]);
    expect(dayBreaks({ a: 200 })).toEqual([100, 200, 300]);
    expect(dayBreaks({ a: 3 })).toEqual([5, 10, 15]);
  });

  it('dayBreaks ignores a lone outlier instead of doubling the step', () => {
    // 40 ordinary days up to 176, then one deadline rush: still bins of 50.
    const ordinary = Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`d${i}`, 20 + i * 4]));
    expect(dayBreaks(ordinary)).toEqual([50, 100, 150]);
    expect(dayBreaks({ ...ordinary, rush: 420 })).toEqual([50, 100, 150]);
  });

  it('dayBreaks is empty without counts', () => {
    expect(dayBreaks({})).toEqual([]);
    expect(dayBreaks({ a: 0 })).toEqual([]);
  });

  it('dayBin counts the breaks a value reaches', () => {
    const breaks = dayBreaks(days);
    expect([19, 49, 50, 99, 100, 149, 150, 420].map(n => dayBin(n, breaks))).toEqual([0, 0, 1, 1, 2, 2, 3, 3]);
    expect(dayBin(42, [])).toBe(0);
  });

  it('dayBinLabels spells out each range', () => {
    expect(dayBinLabels(dayBreaks(days))).toEqual(['<50', '50–99', '100–149', '150+']);
    expect(dayBinLabels([100])).toEqual(['<100', '100+']);
    expect(dayBinLabels([])).toEqual([]);
  });
});

describe('dayChip', () => {
  const stats = buildCalendarStats(weeks); // breaks [50, 100, 150]

  it('shades a counted day by its bin', () => {
    expect(dayChip('2026-10-05', stats)).toEqual({ text: '131', aria: '131 papers scanned', className: 'fc-day-count bin-2' });
    expect(dayChip('2026-10-12', stats).className).toBe('fc-day-count bin-3');
  });

  it('outlines an empty day inside the tracked range', () => {
    expect(dayChip('2026-10-10', stats)).toEqual({ text: '–', aria: EMPTY_DAY_NOTE, className: 'fc-day-count is-empty' });
  });

  it('shows nothing outside the tracked range, and no shading without breaks', () => {
    expect(dayChip('2026-10-04', stats)).toBeNull();
    expect(dayChip('2026-10-19', stats)).toBeNull();
    expect(dayChip('2026-10-05', { ...stats, dayBreaks: [] }).className).toBe('fc-day-count');
  });
});

// Minimal stand-in for the DOM calls labelledCount makes (no jsdom in this repo).
const fakeDoc = {
  createElement: (tag) => ({
    tag, className: '', title: '', textContent: '', attrs: {}, children: [],
    setAttribute(name, value) { this.attrs[name] = value; },
    append(...nodes) { this.children.push(...nodes); },
  }),
};

describe('labelledCount', () => {
  it('hides the visible label from screen readers and speaks the sentence instead', () => {
    const el = labelledCount(fakeDoc, 'fc-day-count', '106', '106 papers scanned');
    expect(el).toMatchObject({ tag: 'span', className: 'fc-day-count', title: '106 papers scanned' });
    const [shown, spoken] = el.children;
    expect(shown).toMatchObject({ textContent: '106', attrs: { 'aria-hidden': 'true' } });
    expect(spoken).toMatchObject({ className: 'sr-only', textContent: '106 papers scanned' });
    expect(spoken.attrs['aria-hidden']).toBeUndefined();
  });
});

describe('dayHeaderFormat', () => {
  it('uses single letters on narrow screens, short names otherwise', () => {
    expect(dayHeaderFormat(true)).toEqual({ weekday: 'narrow' });
    expect(dayHeaderFormat(false)).toEqual({ weekday: 'short' });
  });
});
