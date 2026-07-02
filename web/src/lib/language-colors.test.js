import { describe, expect, it } from 'vitest';
import {
  LANGUAGE_CLASS_COLORS,
  LANGUAGE_FILL_PALETTE,
  buildLanguageClassLookup,
  hashLanguage,
  languageBorderClass,
  languageFillColor,
} from './language-colors.js';

describe('hashLanguage', () => {
  it('is deterministic for the same input', () => {
    expect(hashLanguage('Sinhala')).toBe(hashLanguage('Sinhala'));
  });

  it('returns different hashes for different inputs', () => {
    expect(hashLanguage('Sinhala')).not.toBe(hashLanguage('Tamil'));
  });

  it('returns 0 for an empty string', () => {
    expect(hashLanguage('')).toBe(0);
  });
});

describe('buildLanguageClassLookup', () => {
  it('maps each language to its class id', () => {
    const lookup = buildLanguageClassLookup({ 0: ['English'], 5: ['Sinhala', 'Tamil'] });
    expect(lookup).toEqual({ English: 0, Sinhala: 5, Tamil: 5 });
  });

  it('ignores non-numeric class keys', () => {
    const lookup = buildLanguageClassLookup({ notANumber: ['English'] });
    expect(lookup).toEqual({});
  });

  it('skips blank or non-string language entries', () => {
    const lookup = buildLanguageClassLookup({ 0: ['', '   ', 42, 'English'] });
    expect(lookup).toEqual({ English: 0 });
  });

  it('returns an empty object when given no classes', () => {
    expect(buildLanguageClassLookup()).toEqual({});
  });
});

describe('languageBorderClass', () => {
  it('returns the known class id when the language is classified', () => {
    const langClasses = { 3: ['Hindi'] };
    expect(languageBorderClass('Hindi', langClasses)).toBe(3);
  });

  it('falls back to a hash-derived class for unclassified languages', () => {
    const result = languageBorderClass('Klingon', {});
    expect(result).toBe(hashLanguage('Klingon') % 6);
    expect(result).toBeGreaterThanOrEqual(0);
    expect(result).toBeLessThan(6);
  });

  it('falls back to the hash when the looked-up class id is out of range', () => {
    // Class 6 is outside the 0-5 border-class range, so it must not be trusted as-is.
    const langClasses = { 6: ['OutOfRange'] };
    const result = languageBorderClass('OutOfRange', langClasses);
    expect(result).toBe(hashLanguage('OutOfRange') % 6);
  });
});

describe('languageFillColor', () => {
  it('always returns a color from the fill palette', () => {
    expect(LANGUAGE_FILL_PALETTE).toContain(languageFillColor('English'));
    expect(LANGUAGE_FILL_PALETTE).toContain(languageFillColor(''));
  });

  it('is deterministic for the same language', () => {
    expect(languageFillColor('Sinhala')).toBe(languageFillColor('Sinhala'));
  });
});

describe('palettes', () => {
  it('LANGUAGE_CLASS_COLORS has exactly 6 entries, one per resource class', () => {
    expect(LANGUAGE_CLASS_COLORS).toHaveLength(6);
  });
});
