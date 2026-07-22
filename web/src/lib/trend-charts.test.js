import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  aggregateTrendPeriods,
  buildBumpSegmentPath,
  buildLinePath,
  buildPieSlices,
  classColorForId,
  classColorForLanguage,
  computeRankTrajectoryStats,
  coverageSliceLines,
  coverageSliceLinesWithPercent,
  coverageSlicePercent,
  fillWeekSeries,
  formatEffectiveLanguageCount,
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

describe('formatSliceLabel', () => {
  it('formats a slice label with its count', () => {
    expect(formatSliceLabel('English', 5)).toBe('English (5)');
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
    // No studied/mentioned_only fields on the fixture entries -> treated as fully studied.
    expect(periods[0].languageCounts).toEqual([
      { language: 'Tamil', count: 4, studied: 4, mentioned_only: 0 },
      { language: 'Sinhala', count: 3, studied: 3, mentioned_only: 0 },
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
    expect(periods[0].languageCounts).toEqual([{ language: 'Sinhala', count: 2, studied: 2, mentioned_only: 0 }]);
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
  it('special-cases the "Papers with detected languages" label onto two lines', () => {
    expect(coverageSliceLines('Papers with detected languages', 7)).toEqual([
      'Papers with',
      'detected languages (7)',
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

  it('special-cases "Papers with detected languages" with a percentage too', () => {
    expect(coverageSliceLinesWithPercent('Papers with detected languages', 1, 4)).toEqual([
      'Papers with',
      'detected languages (1) (25.0%)',
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

  it('every V/H command takes a single coordinate (no stray x in vertical line)', () => {
    // A V command with two comma-separated values (e.g. "V 300,44") draws a
    // spurious segment to y=cx before the intended point — the case-5 marker bug.
    for (let i = 0; i < 10; i++) {
      const d = markerPath(i, 300, 44, 4);
      const strayVertical = /[VH]\s*-?\d[\d.]*,-?\d/.test(d);
      expect(strayVertical, `shape ${i}: "${d}"`).toBe(false);
    }
  });
});

describe('computeRankTrajectoryStats', () => {
  it('picks a fastest-rising language that never leads the tier and would never appear in a top-N cutoff', () => {
    // Fourteen flat "background" languages plus one that goes from 0 to a
    // meaningful share only in the back half — with a low enough count it
    // would never make a top-10 cut, but it's still the biggest share-gainer.
    const weeklyCounts = new Map();
    for (let i = 0; i < 14; i++) {
      weeklyCounts.set(`Flat${i}`, [10, 10, 10, 10]);
    }
    weeklyCounts.set('RisingMinor', [0, 0, 3, 3]);
    const stats = computeRankTrajectoryStats(weeklyCounts, 4);
    expect(stats.rising).not.toBeNull();
    expect(stats.rising.language).toBe('RisingMinor');
    expect(stats.rising.delta).toBeGreaterThan(0);
    // The dominant "leader" is one of the flat languages (tied totals — first
    // one encountered wins), never the minor riser.
    expect(stats.leader.language).not.toBe('RisingMinor');
  });

  it('leader is the language with the largest total share across the window', () => {
    const weeklyCounts = new Map([
      ['English', [50, 50]],
      ['French', [10, 10]],
      ['Xhosa', [1, 1]],
    ]);
    const stats = computeRankTrajectoryStats(weeklyCounts, 2);
    expect(stats.leader).toEqual({ language: 'English', share: 50 / 61 });
  });

  it('diversity is the full language count when attention is spread perfectly evenly', () => {
    const weeklyCounts = new Map([
      ['A', [5, 5]],
      ['B', [5, 5]],
      ['C', [5, 5]],
      ['D', [5, 5]],
    ]);
    const stats = computeRankTrajectoryStats(weeklyCounts, 2);
    expect(stats.diversityEffective).toBeCloseTo(4, 5);
  });

  it('diversity collapses toward 1 when a single language dominates', () => {
    const weeklyCounts = new Map([
      ['Dominant', [1000, 1000]],
      ['Tiny', [1, 1]],
    ]);
    const stats = computeRankTrajectoryStats(weeklyCounts, 2);
    expect(stats.diversityEffective).toBeGreaterThan(1);
    expect(stats.diversityEffective).toBeLessThan(1.05);
  });

  it('returns nulls and zero diversity when no language has any studied mentions', () => {
    const weeklyCounts = new Map([
      ['Ghost', [0, 0, 0]],
    ]);
    const stats = computeRankTrajectoryStats(weeklyCounts, 3);
    expect(stats.rising).toBeNull();
    expect(stats.leader).toBeNull();
    expect(stats.diversityEffective).toBe(0);
  });

  it('rising is null when every language holds a perfectly constant share (proportional volume swings do not count as rising)', () => {
    // Both languages shrink in absolute count together, but each holds exactly
    // 50% of the tier throughout — no real share shift, just less total volume.
    const weeklyCounts = new Map([
      ['A', [10, 10, 2, 2]],
      ['B', [10, 10, 2, 2]],
    ]);
    const stats = computeRankTrajectoryStats(weeklyCounts, 4);
    expect(stats.rising).toBeNull();
  });
});

describe('formatEffectiveLanguageCount', () => {
  it('rounds to an integer at 10 and above', () => {
    expect(formatEffectiveLanguageCount(64.3)).toEqual({ value: 64, text: '64', isSingular: false });
  });

  it('keeps one decimal place below 10', () => {
    expect(formatEffectiveLanguageCount(3.44)).toEqual({ value: 3.4, text: '3.4', isSingular: false });
  });

  it('is reachably singular when the rounded value is exactly 1', () => {
    expect(formatEffectiveLanguageCount(1.0)).toEqual({ value: 1, text: '1', isSingular: true });
  });

  it('rounds a near-1 value up into the singular case', () => {
    // Regression guard: a naive `.toFixed(1)` would print "1.0", which can
    // never strictly-equal the string "1" — the original dead-code bug.
    expect(formatEffectiveLanguageCount(0.96)).toEqual({ value: 1, text: '1', isSingular: true });
  });
});

// TrendCharts.astro's <script is:inline> can't `import` this module (it's a
// plain, non-bundled script tag, not an ES module), so it keeps hand-synced
// duplicates of computeRankTrajectoryStats/formatEffectiveLanguageCount named
// computeRankTrajectoryStatsJs/formatEffectiveLanguageCountJs — matching this
// file's existing pattern for buildBumpSegmentPath, markerPath, etc. Nothing
// enforces the two copies stay identical, so a future edit to only one would
// ship silently. This suite pulls the live source text for the *Js functions
// straight out of the .astro file, evaluates it, and runs it through the same
// fixtures as the tested lib versions above — any drift fails here instead of
// in production.
describe('inline <script> duplicates stay in sync with the tested lib versions', () => {
  const astroSourcePath = join(dirname(fileURLToPath(import.meta.url)), '../components/TrendCharts.astro');
  const astroSource = readFileSync(astroSourcePath, 'utf-8');

  /** Extracts `function <name>(...) { ... }` from source via brace balancing (safe here: neither function contains string/template literals with braces). */
  function extractFunctionSource(source, name) {
    const startIdx = source.indexOf(`function ${name}(`);
    if (startIdx === -1) {
      throw new Error(`Could not find "function ${name}(" in TrendCharts.astro — was it renamed or removed?`);
    }
    const braceStart = source.indexOf('{', startIdx);
    let depth = 0;
    let endIdx = -1;
    for (let i = braceStart; i < source.length; i++) {
      if (source[i] === '{') depth++;
      else if (source[i] === '}') {
        depth--;
        if (depth === 0) { endIdx = i; break; }
      }
    }
    if (endIdx === -1) {
      throw new Error(`Unbalanced braces while extracting "${name}" from TrendCharts.astro`);
    }
    return source.slice(startIdx, endIdx + 1);
  }

  /** Compiles an extracted inline function so it can be called directly from a test. */
  function loadInlineFunction(name) {
    const source = extractFunctionSource(astroSource, name);
    // eslint-disable-next-line no-new-func -- deliberately evaluating the shipped inline-script source, not arbitrary input
    const factory = new Function(`'use strict'; ${source}; return ${name};`);
    return factory();
  }

  const computeRankTrajectoryStatsJs = loadInlineFunction('computeRankTrajectoryStatsJs');
  const formatEffectiveLanguageCountJs = loadInlineFunction('formatEffectiveLanguageCountJs');

  it('computeRankTrajectoryStatsJs produces identical output to computeRankTrajectoryStats across representative fixtures', () => {
    const fixtures = [
      // Full-tier rising scope (the bug this stat was fixed for).
      { weekCount: 4, counts: (() => {
        const m = new Map();
        for (let i = 0; i < 14; i++) m.set(`Flat${i}`, [10, 10, 10, 10]);
        m.set('RisingMinor', [0, 0, 3, 3]);
        return m;
      })() },
      // Clear leader.
      { weekCount: 2, counts: new Map([['English', [50, 50]], ['French', [10, 10]], ['Xhosa', [1, 1]]]) },
      // Perfectly even diversity.
      { weekCount: 2, counts: new Map([['A', [5, 5]], ['B', [5, 5]], ['C', [5, 5]], ['D', [5, 5]]]) },
      // Dominated diversity.
      { weekCount: 2, counts: new Map([['Dominant', [1000, 1000]], ['Tiny', [1, 1]]]) },
      // All-zero (no studied mentions at all).
      { weekCount: 3, counts: new Map([['Ghost', [0, 0, 0]]]) },
      // Proportional decline — no real share shift, rising should be null.
      { weekCount: 4, counts: new Map([['A', [10, 10, 2, 2]], ['B', [10, 10, 2, 2]]]) },
      // Odd week count (uneven halves) and a single-language tier.
      { weekCount: 5, counts: new Map([['Solo', [1, 2, 3, 4, 5]]]) },
      // Empty tier.
      { weekCount: 3, counts: new Map() },
    ];

    for (const { weekCount, counts } of fixtures) {
      const libResult = computeRankTrajectoryStats(counts, weekCount);
      const jsResult = computeRankTrajectoryStatsJs(counts, weekCount);
      expect(jsResult).toEqual(libResult);
    }
  });

  it('formatEffectiveLanguageCountJs produces identical output to formatEffectiveLanguageCount', () => {
    const values = [64.3, 3.44, 1.0, 0.96, 0, 9.96, 10, 9.949, 200];
    for (const value of values) {
      expect(formatEffectiveLanguageCountJs(value)).toEqual(formatEffectiveLanguageCount(value));
    }
  });
});
