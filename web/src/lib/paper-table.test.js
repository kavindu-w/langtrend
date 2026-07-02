import { describe, expect, it } from 'vitest';
import {
  buildPaperItem,
  buildWeekApiPaper,
  chipFromEntry,
  classFromEntry,
  formatDate,
  formatSectionTitle,
  normalizeLanguage,
  sourcesOfEntry,
} from './paper-table.js';

describe('normalizeLanguage', () => {
  it('returns string entries as-is', () => {
    expect(normalizeLanguage('Sinhala')).toBe('Sinhala');
  });

  it('takes the first element of tuple entries', () => {
    expect(normalizeLanguage(['Tamil', 2])).toBe('Tamil');
  });

  it('reads .language or .name off object entries', () => {
    expect(normalizeLanguage({ language: 'Hindi' })).toBe('Hindi');
    expect(normalizeLanguage({ name: 'Urdu' })).toBe('Urdu');
  });

  it('falls back to an empty string for unrecognized shapes', () => {
    expect(normalizeLanguage({})).toBe('');
  });
});

describe('classFromEntry', () => {
  it('reads the class from a [language, class] tuple', () => {
    expect(classFromEntry(['Tamil', 2])).toBe(2);
  });

  it('reads .class off an object entry', () => {
    expect(classFromEntry({ language: 'Hindi', class: 3 })).toBe(3);
  });

  it('returns null when there is no explicit class', () => {
    expect(classFromEntry('Sinhala')).toBeNull();
    expect(classFromEntry({ language: 'Hindi' })).toBeNull();
  });
});

describe('sourcesOfEntry', () => {
  it('returns an empty array for string and tuple entries', () => {
    expect(sourcesOfEntry('Sinhala')).toEqual([]);
    expect(sourcesOfEntry(['Tamil', 2])).toEqual([]);
  });

  it('reads .sources off object entries', () => {
    expect(sourcesOfEntry({ language: 'Hindi', sources: ['html', 'pdf'] })).toEqual(['html', 'pdf']);
  });

  it('defaults to an empty array when .sources is missing', () => {
    expect(sourcesOfEntry({ language: 'Hindi' })).toEqual([]);
  });
});

describe('formatSectionTitle', () => {
  it('prefixes an Appendix letter with a period before the rest of the title', () => {
    expect(formatSectionTitle('Appendix AExperiments')).toBe('Appendix A. Experiments');
  });

  it('splits a numbered section prefix from its title', () => {
    expect(formatSectionTitle('3.2Related Work')).toBe('3.2. Related Work');
  });

  it('splits a single-letter section prefix', () => {
    expect(formatSectionTitle('AIntroduction')).toBe('A. Introduction');
  });

  it('collapses internal whitespace and trims', () => {
    expect(formatSectionTitle('  Related   Work  ')).toBe('Related Work');
  });

  it('falls back to "Untitled section" for a blank name', () => {
    expect(formatSectionTitle('   ')).toBe('Untitled section');
  });

  it('leaves a title with no recognizable prefix unchanged', () => {
    expect(formatSectionTitle('Conclusion')).toBe('Conclusion');
  });
});

describe('chipFromEntry', () => {
  it('flags a two-letter language code as needing review', () => {
    const chip = chipFromEntry('en', {});
    expect(chip.needsReview).toBe(true);
    expect(chip.flagReason).toMatch(/2-letter language code/);
  });

  it('flags a language present in the false-positive map', () => {
    const chip = chipFromEntry('GAN', {}, { GAN: 'very common ML acronym' });
    expect(chip.needsReview).toBe(true);
    expect(chip.flagReason).toBe('very common ML acronym');
  });

  it('respects an explicit needs_review/flag_reason on the entry', () => {
    const chip = chipFromEntry({ language: 'Sinhala', needs_review: true, flag_reason: 'custom reason' }, {});
    expect(chip.needsReview).toBe(true);
    expect(chip.flagReason).toBe('custom reason');
  });

  it('does not flag an ordinary multi-letter language with no warnings', () => {
    const chip = chipFromEntry('Sinhala', {});
    expect(chip.needsReview).toBe(false);
    expect(chip.flagReason).toBe('');
  });

  it('prefers an explicit class on the entry over the langClasses lookup', () => {
    const chip = chipFromEntry(['Tamil', 4], { 1: ['Tamil'] });
    expect(chip.borderClass).toBe(4);
  });
});

describe('formatDate', () => {
  it('formats an ISO date string in UTC as a long-form US date', () => {
    expect(formatDate('2026-05-18T00:00:00Z')).toBe('May 18, 2026');
  });

  it('returns an empty string for a missing date', () => {
    expect(formatDate(undefined)).toBe('');
  });
});

describe('buildPaperItem', () => {
  const paper = {
    id: 'http://arxiv.org/abs/2501.00001',
    title: 'A Study of Sinhala and Tamil',
    authors: ['Alice', 'Bob'],
    abstract: 'abstract text',
    pdf_url: 'http://arxiv.org/pdf/2501.00001',
    published: '2026-05-18T00:00:00Z',
  };

  it('assembles chips, coverage badge, and search text from a flagged paper', () => {
    const item = buildPaperItem(
      {
        paper,
        languages: [['Sinhala', 2], ['Tamil', 3]],
        sourcesChecked: ['abstract', 'pdf'],
        sections: [],
        warnings: [],
      },
      0,
      '2026-05-18',
      {},
      {},
    );

    expect(item.chipLanguageNames).toEqual(['Tamil', 'Sinhala']); // higher class first
    expect(item.minClass).toBe(2);
    expect(item.coverageBadge).toEqual({
      label: 'PDF & Abstract',
      title: 'HTML version could not be extracted — analysis done with PDF and abstract',
    });
    expect(item.searchText).toBe('a study of sinhala and tamil alice bob');
    expect(item.arxivUrl).toBe('https://arxiv.org/abs/2501.00001');
  });

  it('has no coverage badge when the HTML source was scanned', () => {
    const item = buildPaperItem(
      { paper, languages: [], sourcesChecked: ['html'], sections: [], warnings: [] },
      0,
      '2026-05-18',
    );
    expect(item.coverageBadge).toBeNull();
  });

  it('builds per-section chip lists, sorted by class descending', () => {
    const item = buildPaperItem(
      {
        paper,
        languages: [],
        sourcesChecked: ['html'],
        sections: [
          { name: 'Intro', source: 'html', detected_languages: [['Sinhala', 2], ['Tamil', 3]] },
          { name: '', source: 'html', detected_languages: [] }, // no name -> filtered out
        ],
        warnings: [],
      },
      0,
      '2026-05-18',
    );

    expect(item.sections).toHaveLength(1);
    expect(item.sections[0].label).toBe('Intro');
    expect(item.sections[0].chips.map((c) => c.language)).toEqual(['Tamil', 'Sinhala']);
  });

  it('skips language entries that normalize to an empty string when grouping by source', () => {
    const item = buildPaperItem(
      { paper, languages: [{ sources: ['pdf'] }], sourcesChecked: ['pdf'], sections: [], warnings: [] },
      0,
      '2026-05-18',
    );
    expect(item.sourcesGroups).toEqual([]);
  });

  it('ignores malformed acronym-conflict warnings missing a language or acronym', () => {
    const item = buildPaperItem(
      {
        paper,
        languages: [],
        sourcesChecked: ['abstract'],
        sections: [],
        warnings: [
          { step: 'acronym_language_conflict', acronym: 'GAN' }, // no language
          { step: 'acronym_language_conflict', language: 'Gan' }, // no acronym
        ],
      },
      0,
      '2026-05-18',
    );
    expect(item.suppressedChips).toEqual([]);
  });

  it('collects acronym-conflict warnings into suppressedChips, skipping languages already shown as chips', () => {
    const item = buildPaperItem(
      {
        paper,
        languages: ['Sinhala'],
        sourcesChecked: ['abstract'],
        sections: [],
        warnings: [
          { step: 'acronym_language_conflict', language: 'Gan', acronym: 'GAN', language_class: 1 },
          { step: 'acronym_language_conflict', language: 'Sinhala', acronym: 'SIN', language_class: 2 },
          { step: 'other_warning', language: 'Tamil', acronym: 'TAM' },
        ],
      },
      0,
      '2026-05-18',
    );

    // Sinhala is already a visible chip, so its conflict warning is not duplicated as suppressed.
    expect(item.suppressedChips).toEqual([{ language: 'Gan', borderClass: 1, acronyms: ['GAN'] }]);
  });

  it('groups languages by source, ordered abstract → html → pdf', () => {
    const item = buildPaperItem(
      {
        paper,
        languages: [{ language: 'Sinhala', sources: ['pdf'] }, { language: 'Tamil', sources: ['abstract'] }],
        sourcesChecked: ['abstract', 'pdf'],
        sections: [],
        warnings: [],
      },
      0,
      '2026-05-18',
    );

    expect(item.sourcesGroups.map((g) => g.src)).toEqual(['abstract', 'pdf']);
  });
});

describe('buildWeekApiPaper', () => {
  const paper = {
    id: 'http://arxiv.org/abs/2501.00001',
    title: 'A Study of Sinhala and Tamil',
    authors: ['Alice', 'Bob'],
    abstract: 'abstract text',
    pdf_url: 'http://arxiv.org/pdf/2501.00001',
    published: '2026-05-18T00:00:00Z',
  };

  it('shapes languages, sorts by class desc, and rewrites the arxiv URL to https', () => {
    const result = buildWeekApiPaper(
      { paper, languages: [['Sinhala', 2], ['Tamil', 3]] },
      {},
    );
    expect(result.arxiv_url).toBe('https://arxiv.org/abs/2501.00001');
    expect(result.languages.map((l) => l.language)).toEqual(['Tamil', 'Sinhala']);
    expect(result.languageNames).toEqual(['Tamil', 'Sinhala']);
    expect(result.langCount).toBe(2);
    expect(result.minClass).toBe(2);
    expect(result.classes).toEqual([3, 2]);
    expect(result.searchText).toBe('a study of sinhala and tamil alice bob');
  });

  it('flags a two-letter language code as needing review even without an explicit flag', () => {
    const result = buildWeekApiPaper({ paper, languages: ['en'] }, {});
    expect(result.languages[0].needsReview).toBe(true);
  });

  it('flags a language present in the false-positive map, matching chipFromEntry', () => {
    const result = buildWeekApiPaper({ paper, languages: ['Gan'] }, {}, { Gan: 'very common ML acronym' });
    expect(result.languages[0].needsReview).toBe(true);
  });

  it('does not flag a language absent from the false-positive map', () => {
    const result = buildWeekApiPaper({ paper, languages: ['Sinhala'] }, {}, { Gan: 'very common ML acronym' });
    expect(result.languages[0].needsReview).toBe(false);
  });

  it('respects an explicit needs_review flag on the entry', () => {
    const result = buildWeekApiPaper(
      { paper, languages: [{ language: 'Sinhala', needs_review: true }] },
      {},
    );
    expect(result.languages[0].needsReview).toBe(true);
  });

  it('defaults minClass to 5 and languages to empty when the paper has no languages', () => {
    const result = buildWeekApiPaper({ paper, languages: [] }, {});
    expect(result.minClass).toBe(5);
    expect(result.languages).toEqual([]);
    expect(result.classes).toEqual([]);
  });

  it('falls back to the pdf_url as arxiv_url when the paper has no id', () => {
    const noIdPaper = { ...paper, id: undefined };
    const result = buildWeekApiPaper({ paper: noIdPaper, languages: [] }, {});
    expect(result.arxiv_url).toBe(paper.pdf_url);
    expect(result.id).toBe('');
  });
});
