import { describe, expect, it } from 'vitest';
import { CLASS_LABELS, colorForIndex, colorForLanguage, formatAuthors } from './data.js';

describe('colorForIndex', () => {
  it('cycles through the palette by index', () => {
    expect(colorForIndex(0)).toBe(colorForIndex(8));
  });

  it('returns different colors for different indices within one cycle', () => {
    expect(colorForIndex(0)).not.toBe(colorForIndex(1));
  });
});

describe('colorForLanguage', () => {
  it('is deterministic for the same language', () => {
    expect(colorForLanguage('Sinhala')).toBe(colorForLanguage('Sinhala'));
  });

  it('returns different colors for different languages', () => {
    expect(colorForLanguage('Sinhala')).not.toBe(colorForLanguage('Tamil'));
  });
});

describe('formatAuthors', () => {
  it('falls back to "Unknown authors" when there is no author list', () => {
    expect(formatAuthors(undefined)).toEqual({ display: 'Unknown authors', title: 'Unknown authors' });
    expect(formatAuthors([])).toEqual({ display: 'Unknown authors', title: 'Unknown authors' });
  });

  it('shows the full list when it fits within maxLength', () => {
    const result = formatAuthors(['Alice', 'Bob'], 72);
    expect(result).toEqual({ display: 'Alice, Bob', title: 'Alice, Bob' });
  });

  it('truncates to the first 3 authors with "et al." when the list is too long', () => {
    const authors = ['Alice', 'Bob', 'Carol', 'Dave', 'Eve'];
    const result = formatAuthors(authors, 10);
    expect(result.display).toBe('Alice, Bob, Carol et al.');
    expect(result.title).toBe('Alice, Bob, Carol, Dave, Eve');
  });
});

describe('CLASS_LABELS', () => {
  it('has one label per resource class, 0 through 5', () => {
    expect(CLASS_LABELS).toEqual(['Class 0', 'Class 1', 'Class 2', 'Class 3', 'Class 4', 'Class 5']);
  });
});
