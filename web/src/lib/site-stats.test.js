import { describe, expect, it } from 'vitest';
import { compactNumber, goatCounterTotalUrl, parseGoatCounterCount, parseRepoStats, repoApiUrl } from './site-stats.js';

describe('compactNumber', () => {
  it.each([
    [0, '0'],
    [999, '999'],
    [1000, '1k'],
    [1250, '1.3k'],
    [12_400, '12k'],
    [2_000_000, '2M'],
    [2_450_000, '2.5M'],
  ])('%d -> %s', (n, expected) => {
    expect(compactNumber(n)).toBe(expected);
  });

  it('rejects non-numbers and negatives', () => {
    expect(compactNumber(undefined)).toBeNull();
    expect(compactNumber(Number.NaN)).toBeNull();
    expect(compactNumber(-1)).toBeNull();
  });
});

describe('parseGoatCounterCount', () => {
  it('parses formatted strings with any thousands separator', () => {
    expect(parseGoatCounterCount({ count: '1,234' })).toBe(1234);
    expect(parseGoatCounterCount({ count: '1.234' })).toBe(1234);
    expect(parseGoatCounterCount({ count: '1 234' })).toBe(1234);
  });

  it('accepts plain numbers', () => {
    expect(parseGoatCounterCount({ count: 42 })).toBe(42);
  });

  it('returns null for missing/garbage payloads', () => {
    expect(parseGoatCounterCount(null)).toBeNull();
    expect(parseGoatCounterCount({})).toBeNull();
    expect(parseGoatCounterCount({ count: 'n/a' })).toBeNull();
  });
});

describe('parseRepoStats', () => {
  it('extracts stars and forks', () => {
    expect(parseRepoStats({ stargazers_count: 12, forks_count: 3, other: 1 })).toEqual({ stars: 12, forks: 3 });
  });

  it('returns null for error payloads (e.g. rate-limit message)', () => {
    expect(parseRepoStats({ message: 'API rate limit exceeded' })).toBeNull();
    expect(parseRepoStats(null)).toBeNull();
  });
});

describe('URL helpers', () => {
  it('builds the GoatCounter TOTAL endpoint only when a code is configured', () => {
    expect(goatCounterTotalUrl('langtrend')).toBe('https://langtrend.goatcounter.com/counter/TOTAL.json');
    expect(goatCounterTotalUrl('')).toBeNull();
    expect(goatCounterTotalUrl(undefined)).toBeNull();
  });

  it('maps a GitHub repo URL to its REST endpoint', () => {
    expect(repoApiUrl('https://github.com/kavindu-w/langtrend')).toBe('https://api.github.com/repos/kavindu-w/langtrend');
    expect(repoApiUrl('https://github.com/kavindu-w/langtrend/')).toBe('https://api.github.com/repos/kavindu-w/langtrend');
    expect(repoApiUrl('https://example.com')).toBeNull();
  });
});
