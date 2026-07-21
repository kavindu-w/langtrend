import { describe, expect, it, vi } from 'vitest';
import { activeWeekSlugsFor, debounce, matchPaperRow, parseRowMeta } from './paper-filters.js';

describe('debounce', () => {
  it('only invokes fn once after the delay, using the last call\'s args', () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const debounced = debounce(fn, 120);

    debounced('a');
    debounced('b');
    debounced('c');
    expect(fn).not.toHaveBeenCalled();

    vi.advanceTimersByTime(119);
    expect(fn).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith('c');
    vi.useRealTimers();
  });

  it('fires again on a later, separate burst', () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const debounced = debounce(fn, 120);

    debounced();
    vi.advanceTimersByTime(120);
    expect(fn).toHaveBeenCalledTimes(1);

    debounced();
    vi.advanceTimersByTime(120);
    expect(fn).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });
});

describe('parseRowMeta', () => {
  it('splits pipe-delimited languages and comma-delimited classes/verdicts', () => {
    const dataset = { week: '2026-07-06', search: 'a folded title', languages: 'Sinhala|Tamil', classes: '1,2', verdicts: 'studied,mentioned_only' };
    expect(parseRowMeta(dataset, 'fallback-week')).toEqual({
      week: '2026-07-06',
      search: 'a folded title',
      languages: ['Sinhala', 'Tamil'],
      classes: ['1', '2'],
      verdicts: ['studied', 'mentioned_only'],
    });
  });

  it('falls back to the given week when dataset.week is absent', () => {
    expect(parseRowMeta({}, 'fallback-week').week).toBe('fallback-week');
  });

  it('treats missing/empty delimited fields as empty arrays, not [""]', () => {
    const meta = parseRowMeta({ languages: '', classes: '', verdicts: '' }, 'w');
    expect(meta.languages).toEqual([]);
    expect(meta.classes).toEqual([]);
    expect(meta.verdicts).toEqual([]);
  });
});

describe('activeWeekSlugsFor', () => {
  const availableWeeks = ['2026-06-01', '2026-06-08', '2026-06-15', '2026-06-22'];

  it('returns every available week when period=all', () => {
    expect(activeWeekSlugsFor({ periodParam: 'all', fromParam: null }, availableWeeks, '2026-06-08')).toEqual(availableWeeks);
  });

  it('returns the inclusive range between "from" and the anchor week, regardless of order', () => {
    expect(activeWeekSlugsFor({ periodParam: null, fromParam: '2026-06-01' }, availableWeeks, '2026-06-15'))
      .toEqual(['2026-06-01', '2026-06-08', '2026-06-15']);
    // anchor before "from" — same range, order-independent
    expect(activeWeekSlugsFor({ periodParam: null, fromParam: '2026-06-15' }, availableWeeks, '2026-06-01'))
      .toEqual(['2026-06-01', '2026-06-08', '2026-06-15']);
  });

  it('falls back to just the anchor week when "from" is not a known week', () => {
    expect(activeWeekSlugsFor({ periodParam: null, fromParam: 'not-a-week' }, availableWeeks, '2026-06-08'))
      .toEqual(['2026-06-08']);
  });

  it('defaults to just the anchor week with no period/from params', () => {
    expect(activeWeekSlugsFor({ periodParam: null, fromParam: null }, availableWeeks, '2026-06-08'))
      .toEqual(['2026-06-08']);
  });
});

describe('matchPaperRow', () => {
  const baseMeta = { week: '2026-06-08', search: 'a folded title about sinhala', languages: ['Sinhala'], classes: ['2'], verdicts: ['studied'] };
  const baseFilters = () => ({
    activeWeekSet: new Set(['2026-06-08']),
    searchTerm: '',
    classFilter: 'all',
    activeLanguages: new Set(),
    enabledVerdicts: new Set(['studied', 'mentioned_only']),
  });

  it('shows a row that is in period, has an enabled verdict, and matches no active filters', () => {
    expect(matchPaperRow(baseMeta, baseFilters())).toEqual({ inPeriod: true, inScope: true, show: true });
  });

  it('is out of period when the row week is not in the active week set', () => {
    const result = matchPaperRow({ ...baseMeta, week: '2026-01-01' }, baseFilters());
    expect(result).toEqual({ inPeriod: false, inScope: false, show: false });
  });

  it('is in period but out of scope when its only verdict is disabled', () => {
    const filters = { ...baseFilters(), enabledVerdicts: new Set(['mentioned_only']) };
    const result = matchPaperRow(baseMeta, filters);
    expect(result.inPeriod).toBe(true);
    expect(result.inScope).toBe(false);
    expect(result.show).toBe(false);
  });

  it('hides an in-scope row whose languages do not intersect the active language filter', () => {
    const filters = { ...baseFilters(), activeLanguages: new Set(['Tamil']) };
    expect(matchPaperRow(baseMeta, filters).show).toBe(false);
  });

  it('shows an in-scope row when it has any of the active languages', () => {
    const filters = { ...baseFilters(), activeLanguages: new Set(['Tamil', 'Sinhala']) };
    expect(matchPaperRow(baseMeta, filters).show).toBe(true);
  });

  it('hides a row whose folded search text does not include the search term', () => {
    const filters = { ...baseFilters(), searchTerm: 'transformer' };
    expect(matchPaperRow(baseMeta, filters).show).toBe(false);
  });

  it('shows a row whose folded search text includes the search term', () => {
    const filters = { ...baseFilters(), searchTerm: 'sinhala' };
    expect(matchPaperRow(baseMeta, filters).show).toBe(true);
  });

  it('hides a row that does not match the selected resource-class filter', () => {
    const filters = { ...baseFilters(), classFilter: '4' };
    expect(matchPaperRow(baseMeta, filters).show).toBe(false);
  });

  it('reports inPeriod/inScope independently of language/search/class filters (used for chip/verdict counts)', () => {
    const filters = { ...baseFilters(), activeLanguages: new Set(['Tamil']), searchTerm: 'nonmatching', classFilter: '4' };
    const result = matchPaperRow(baseMeta, filters);
    expect(result.inPeriod).toBe(true);
    expect(result.inScope).toBe(true);
    expect(result.show).toBe(false);
  });
});
