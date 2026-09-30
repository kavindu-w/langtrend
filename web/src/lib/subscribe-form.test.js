import { describe, expect, it } from 'vitest';
import { MAX_LANGUAGES, languagesFromParam, languagesMetadata, rankLanguageOptions, subscribeActionUrl, suggestLanguages } from './subscribe-form.js';

const CLASSES = { 0: ['Abau', "N'Ko", 'Gan'], 2: ['Sinhala', 'Sindhi'], 3: ['Tamil'], 5: ['English'] };
const WEEKS = [
  { languageCounts: [{ language: 'English', count: 200 }, { language: 'Tamil', count: 5 }] },
  { languageCounts: [{ language: 'Sinhala', count: 3 }, { language: 'Tamil', count: 1 }] },
];

describe('rankLanguageOptions', () => {
  it('orders by total detections, then alphabetically, and drops ignored names', () => {
    expect(rankLanguageOptions(CLASSES, ['Gan'], WEEKS)).toEqual(['English', 'Tamil', 'Sinhala', 'Abau', "N'Ko", 'Sindhi']);
  });

  it('handles missing inputs', () => {
    expect(rankLanguageOptions(undefined)).toEqual([]);
    expect(rankLanguageOptions({ 1: ['B', 'A'] })).toEqual(['A', 'B']);
  });
});

describe('suggestLanguages', () => {
  const options = rankLanguageOptions(CLASSES, [], WEEKS);

  it('puts prefix matches before substring matches, keeping rank order', () => {
    expect(suggestLanguages('in', options)).toEqual(['Sinhala', 'Sindhi']);
    expect(suggestLanguages('s', options)).toEqual(['Sinhala', 'Sindhi', 'English']);
  });

  it('ignores case and diacritics/apostrophes', () => {
    expect(suggestLanguages('nko', options)).toEqual(["N'Ko"]);
    expect(suggestLanguages('TAM', options)).toEqual(['Tamil']);
  });

  it('skips already-selected languages and respects the limit', () => {
    expect(suggestLanguages('sin', options, ['Sinhala'])).toEqual(['Sindhi']);
    expect(suggestLanguages('', options, ['English'], 2)).toEqual(['Tamil', 'Sinhala']);
  });
});

describe('languagesFromParam', () => {
  const options = ['English', 'Sinhala', 'Tamil'];

  it('keeps known languages only, deduplicated', () => {
    expect(languagesFromParam('Sinhala, Tamil,Klingon,Sinhala', options)).toEqual(['Sinhala', 'Tamil']);
  });

  it('caps the selection', () => {
    const many = Array.from({ length: MAX_LANGUAGES + 5 }, (_, i) => `L${i}`);
    expect(languagesFromParam(many.join(','), many)).toHaveLength(MAX_LANGUAGES);
  });

  it('returns nothing for an empty param', () => {
    expect(languagesFromParam(null, options)).toEqual([]);
    expect(languagesFromParam('', options)).toEqual([]);
  });
});

describe('subscribeActionUrl', () => {
  it('builds the Buttondown embed endpoint', () => {
    expect(subscribeActionUrl('kavindu')).toBe('https://buttondown.com/api/emails/embed-subscribe/kavindu');
    expect(subscribeActionUrl('')).toBeNull();
  });
});

describe('languagesMetadata', () => {
  it('delimits on both ends so substring names cannot match', () => {
    const value = languagesMetadata(['Malayalam', 'Tamil']);
    expect(value).toBe(',Malayalam,Tamil,');
    expect(value.includes(',Malay,')).toBe(false);
  });

  it('is empty for no selection and drops names containing the delimiter', () => {
    expect(languagesMetadata([])).toBe('');
    expect(languagesMetadata(undefined)).toBe('');
    expect(languagesMetadata(['A,B', 'Sinhala'])).toBe(',Sinhala,');
  });
});
