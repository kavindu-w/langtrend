import { describe, expect, it } from 'vitest';
import {
  aggregateTrendPeriods,
  buildBumpSegmentPath,
  buildLinePath,
  buildPieSlices,
  classColorForId,
  classColorForLanguage,
  coverageSliceLines,
  coverageSliceLinesWithPercent,
  coverageSlicePercent,
  fillWeekSeries,
  formatCountWithShare,
  formatPercent,
  formatSliceLabel,
  formatWeekLabel,
  formatWeekRange,
  formatWeekdayLabel,
  markerPath,
  roundedBottomRect,
  roundedTopRect,
  scrollableChartWidth,
  sumCounts,
  trendPeriodKey,
  trendPeriodLabel,
} from './trend-charts.js';

describe('formatWeekRange', () => {
  it('formats a range from the first and last series dates', () => {
    const series = [{ date: '2026-05-18' }, { date: '2026-05-24' }];
    expect(formatWeekRange(series)).toBe(`The Week of ${formatWeekLabel('2026-05-18')}–${formatWeekLabel('2026-05-24')}`);
  });

  it('falls back to fallbackStart/fallbackEnd when the series is empty', () => {
    expect(formatWeekRange([], '2026-05-18', '2026-05-24')).toBe(
      `The Week of ${formatWeekLabel('2026-05-18')}–${formatWeekLabel('2026-05-24')}`,
    );
  });

  it('returns a generic label when no dates are available at all', () => {
    expect(formatWeekRange([])).toBe('the current window');
  });
});

describe('formatWeekLabel / formatWeekdayLabel', () => {
  it('formats a week start with the day number and short month name', () => {
    const label = formatWeekLabel('2026-05-18');
    expect(label).toContain('18');
    expect(label).toMatch(/May/i);
  });

  it('formats a date as a short weekday name', () => {
    // 2026-05-18 is a Monday
    expect(formatWeekdayLabel('2026-05-18')).toBe('Mon');
  });
});

describe('formatPercent', () => {
  it('computes a one-decimal percentage', () => {
    expect(formatPercent(1, 4)).toBe('25.0%');
  });

  it('returns 0.0% when total is zero to avoid dividing by zero', () => {
    expect(formatPercent(5, 0)).toBe('0.0%');
  });
});

describe('sumCounts', () => {
  it('adds up the count field across items', () => {
    expect(sumCounts([{ count: 2 }, { count: 3 }, { count: 0 }])).toBe(5);
  });

  it('treats a missing count as zero', () => {
    expect(sumCounts([{}, { count: 4 }])).toBe(4);
  });
});

describe('buildLinePath', () => {
  it('builds an SVG path starting with M and continuing with L', () => {
    expect(buildLinePath([{ x: 0, y: 0 }, { x: 10, y: 5 }, { x: 20, y: 1 }])).toBe('M0,0 L10,5 L20,1');
  });

  it('returns an empty string for no points', () => {
    expect(buildLinePath([])).toBe('');
  });
});

describe('classColorForId / classColorForLanguage', () => {
  it('returns a color for a valid class id and falls back to class 0 out of range', () => {
    expect(classColorForId(0)).toBeTruthy();
    expect(classColorForId(99)).toBe(classColorForId(0));
  });

  it('resolves a language color via its class lookup', () => {
    expect(classColorForLanguage('Hindi', { 3: ['Hindi'] })).toBe(classColorForId(3));
  });
});

describe('buildPieSlices', () => {
  it('skips zero/negative-value items and builds arcs for the rest', () => {
    const slices = buildPieSlices(
      [
        { label: 'A', value: 3, color: 'red' },
        { label: 'B', value: 0, color: 'blue' },
        { label: 'C', value: 1, color: 'green' },
      ],
      100,
      100,
      50,
    );
    expect(slices.map((s) => s.label)).toEqual(['A', 'C']);
    expect(slices[0].path).toMatch(/^M 100 100/);
  });

  it('returns no slices when all values are zero', () => {
    expect(buildPieSlices([{ label: 'A', value: 0, color: 'red' }], 0, 0, 10)).toEqual([]);
  });
});

describe('formatSliceLabel / formatCountWithShare', () => {
  it('formats a slice label with its count', () => {
    expect(formatSliceLabel('English', 5)).toBe('English (5)');
  });

  it('formats a count with a rounded percentage share', () => {
    expect(formatCountWithShare(1, 3)).toBe('1 (33%)');
  });

  it('omits the share when total is zero', () => {
    expect(formatCountWithShare(5, 0)).toBe('5');
  });
});

describe('trendPeriodKey / trendPeriodLabel', () => {
  it('uses the week start as-is for week granularity', () => {
    expect(trendPeriodKey('2026-05-18', 'week')).toBe('2026-05-18');
    expect(trendPeriodLabel('2026-05-18', 'week')).toBe(formatWeekLabel('2026-05-18'));
  });

  it('buckets by year-month for month granularity', () => {
    expect(trendPeriodKey('2026-05-18', 'month')).toBe('2026-05');
  });

  it('buckets by year for year granularity', () => {
    expect(trendPeriodKey('2026-05-18', 'year')).toBe('2026');
    expect(trendPeriodLabel('2026-05-18', 'year')).toBe('2026');
  });
});

describe('aggregateTrendPeriods', () => {
  const weeks = [
    {
      weekStart: '2026-05-04',
      papers: 10,
      flaggedPapers: 3,
      languageCounts: [{ language: 'Sinhala', count: 2 }],
      classCounts: [{ class_id: 2, count: 2 }],
    },
    {
      weekStart: '2026-05-11',
      papers: 5,
      flaggedPapers: 1,
      languageCounts: [{ language: 'Sinhala', count: 1 }, { language: 'Tamil', count: 4 }],
      classCounts: [{ class_id: 2, count: 1 }, { class_id: 3, count: 4 }],
    },
  ];

  it('passes weeks through unmerged at week granularity', () => {
    const periods = aggregateTrendPeriods(weeks, 'week');
    expect(periods).toHaveLength(2);
    expect(periods.map((p) => p.periodKey)).toEqual(['2026-05-04', '2026-05-11']);
  });

  it('merges weeks into the same month bucket, summing counts', () => {
    const periods = aggregateTrendPeriods(weeks, 'month');
    expect(periods).toHaveLength(1);
    expect(periods[0].papers).toBe(15);
    expect(periods[0].flaggedPapers).toBe(4);
    expect(periods[0].uniqueLanguages).toBe(2);
    // Sorted by count desc: Tamil (4+1=5)? no - Tamil only appears once with count 4, Sinhala 2+1=3
    expect(periods[0].languageCounts).toEqual([
      { language: 'Tamil', count: 4 },
      { language: 'Sinhala', count: 3 },
    ]);
  });

  it('sorts merged periods chronologically', () => {
    const reversed = [...weeks].reverse();
    const periods = aggregateTrendPeriods(reversed, 'week');
    expect(periods.map((p) => p.periodKey)).toEqual(['2026-05-04', '2026-05-11']);
  });

  it('skips language-count entries with no language', () => {
    const weeksWithBlank = [{
      weekStart: '2026-05-04',
      papers: 1,
      flaggedPapers: 1,
      languageCounts: [{ language: '', count: 5 }, { language: 'Sinhala', count: 2 }],
      classCounts: [],
    }];
    const periods = aggregateTrendPeriods(weeksWithBlank, 'week');
    expect(periods[0].languageCounts).toEqual([{ language: 'Sinhala', count: 2 }]);
  });
});

describe('buildBumpSegmentPath', () => {
  it('builds a cubic-bezier path between two points', () => {
    const path = buildBumpSegmentPath({ x: 0, y: 0 }, { x: 10, y: 10 });
    expect(path).toBe('M 0 0 C 5 0 5 10 10 10');
  });
});

describe('scrollableChartWidth', () => {
  const margins = { left: 10, right: 10 };

  it('returns null when the period count fits within maxVisible', () => {
    expect(scrollableChartWidth(700, margins, 5, 5)).toBeNull();
  });

  it('computes an expanded width when there are more periods than maxVisible', () => {
    const width = scrollableChartWidth(700, margins, 10, 5);
    expect(width).toBeGreaterThan(700);
  });
});

describe('coverageSliceLines / coverageSliceLinesWithPercent', () => {
  it('special-cases the "Papers with language mentions" label onto two lines', () => {
    expect(coverageSliceLines('Papers with language mentions', 7)).toEqual([
      'Papers with',
      'language mentions (7)',
    ]);
  });

  it('returns a single line for any other label', () => {
    expect(coverageSliceLines('Not flagged', 3)).toEqual(['Not flagged (3)']);
  });

  it('appends a percentage when a total is given', () => {
    expect(coverageSliceLinesWithPercent('Not flagged', 1, 4)).toEqual(['Not flagged (1) (25.0%)']);
  });

  it('omits the percentage when total is zero', () => {
    expect(coverageSliceLinesWithPercent('Not flagged', 1, 0)).toEqual(['Not flagged (1)']);
  });

  it('special-cases "Papers with language mentions" with a percentage too', () => {
    expect(coverageSliceLinesWithPercent('Papers with language mentions', 1, 4)).toEqual([
      'Papers with',
      'language mentions (1) (25.0%)',
    ]);
  });
});

describe('coverageSlicePercent', () => {
  it('delegates to formatPercent', () => {
    expect(coverageSlicePercent(1, 4)).toBe(formatPercent(1, 4));
  });
});

describe('roundedTopRect / roundedBottomRect', () => {
  it('returns an empty string for a non-positive height', () => {
    expect(roundedTopRect(0, 0, 10, 0, 4)).toBe('');
    expect(roundedBottomRect(0, 0, 10, -1, 4)).toBe('');
  });

  it('produces a path string for a positive height', () => {
    expect(roundedTopRect(0, 0, 10, 20, 4)).toMatch(/^M /);
    expect(roundedBottomRect(0, 0, 10, 20, 4)).toMatch(/^M /);
  });
});

describe('fillWeekSeries', () => {
  it('fills a full Mon–Sun week, using existing points where present', () => {
    const series = [{ date: '2026-05-19', papers: 7, flagged: 2 }]; // Tuesday
    const filled = fillWeekSeries(series, '2026-05-18'); // Monday
    expect(filled).toHaveLength(7);
    expect(filled[0].date).toBe('2026-05-18');
    expect(filled[6].date).toBe('2026-05-24');
    expect(filled[1]).toEqual({ date: '2026-05-19', papers: 7, flagged: 2 });
    expect(filled[2]).toEqual({ date: '2026-05-20', papers: 0, flagged: 0 });
  });

  it('shifts a Sunday weekStart forward to the following Monday', () => {
    const filled = fillWeekSeries([], '2026-05-17'); // Sunday
    expect(filled[0].date).toBe('2026-05-18');
  });

  it('returns the series unchanged when weekStart is missing', () => {
    const series = [{ date: '2026-05-19', papers: 1, flagged: 0 }];
    expect(fillWeekSeries(series, undefined)).toBe(series);
  });
});

describe('markerPath', () => {
  it('returns a distinct, deterministic path per shape index', () => {
    const shapes = Array.from({ length: 10 }, (_, i) => markerPath(i, 0, 0, 5));
    expect(new Set(shapes).size).toBe(10);
    expect(markerPath(0, 0, 0, 5)).toBe(markerPath(10, 0, 0, 5)); // wraps every 10
  });
});
