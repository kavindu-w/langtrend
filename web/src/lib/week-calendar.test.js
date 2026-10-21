import { describe, expect, it } from 'vitest';
import { buildCalendarStats, countLabel, dayHeaderFormat, dayLabel, isEmptyTrackedDay, labelledCount, scanSummary, weekTooltip } from './week-calendar.js';

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
  });

  it('latest is the newest week regardless of input order', () => {
    expect(buildCalendarStats([...weeks].reverse()).latest.weekStart).toBe('2026-10-12');
  });

  it('handles no data', () => {
    const stats = buildCalendarStats([]);
    expect(stats).toEqual({ days: {}, weeks: {}, totalPapers: 0, firstWeek: null, latest: null, lastDay: null });
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

  it('dayLabel parenthesises the count and gives readable aria text', () => {
    expect(dayLabel(106)).toEqual({ text: '(106)', aria: '106 papers scanned' });
    expect(dayLabel(1)).toEqual({ text: '(1)', aria: '1 paper scanned' });
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
      .toBe('Latest scan: week of 12 Oct · 889 papers · 1,509 scanned since 5 Oct 2026');
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
    const el = labelledCount(fakeDoc, 'fc-day-count', '(106)', '106 papers scanned');
    expect(el).toMatchObject({ tag: 'span', className: 'fc-day-count', title: '106 papers scanned' });
    const [shown, spoken] = el.children;
    expect(shown).toMatchObject({ textContent: '(106)', attrs: { 'aria-hidden': 'true' } });
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
