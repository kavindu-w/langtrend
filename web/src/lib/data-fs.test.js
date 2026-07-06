import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// dataRoot in data.js is computed once at import time from process.cwd(), so each test
// chdirs into a fresh fixture directory (containing its own data/ tree) and re-imports
// the module fresh via vi.resetModules() to pick up that fixture's dataRoot.

let fixtureDir;
let originalCwd;

function writeJson(path, value) {
  mkdirSync(join(fixtureDir, ...path.split('/').slice(0, -1)), { recursive: true });
  writeFileSync(join(fixtureDir, path), JSON.stringify(value), 'utf-8');
}

function writeText(path, value) {
  mkdirSync(join(fixtureDir, ...path.split('/').slice(0, -1)), { recursive: true });
  writeFileSync(join(fixtureDir, path), value, 'utf-8');
}

async function importDataModule() {
  vi.resetModules();
  return import('./data.js');
}

beforeEach(() => {
  originalCwd = process.cwd();
  fixtureDir = mkdtempSync(join(tmpdir(), 'langtrend-data-test-'));
  mkdirSync(join(fixtureDir, 'data'), { recursive: true });
  process.chdir(fixtureDir);
});

afterEach(() => {
  process.chdir(originalCwd);
  rmSync(fixtureDir, { recursive: true, force: true });
});

describe('getAvailableWeeks', () => {
  it('returns an empty array when the weeks directory does not exist', async () => {
    const { getAvailableWeeks } = await importDataModule();
    expect(getAvailableWeeks()).toEqual([]);
  });

  it('lists week directories as sorted ISO dates, ignoring non-matching entries', async () => {
    mkdirSync(join(fixtureDir, 'data/processed/weeks/20260504_to_20260511'), { recursive: true });
    mkdirSync(join(fixtureDir, 'data/processed/weeks/20260518_to_20260525'), { recursive: true });
    mkdirSync(join(fixtureDir, 'data/processed/weeks/not-a-week-dir'), { recursive: true });

    const { getAvailableWeeks } = await importDataModule();
    expect(getAvailableWeeks()).toEqual(['2026-05-04', '2026-05-18']);
  });
});

describe('loadSiteData (current window)', () => {
  it('reads the default manifest and computes coverage/topLanguages/classCounts', async () => {
    writeJson('data/processed/langtrend_manifest_last_7_days.json', {
      daily_series: [{ date: '2026-05-18', papers: 3, flagged: 1 }],
      class_counts: [{ class_id: 2, count: 1 }],
      flagged_papers: [
        {
          paper: { id: 'http://arxiv.org/abs/1', title: 'A' },
          languages: [{ language: 'Sinhala', count: 1 }],
          sources_checked: ['html'],
          sections: [],
        },
        {
          paper: { id: 'http://arxiv.org/abs/2', title: 'B' },
          languages: [{ language: 'Sinhala', count: 1 }],
          sources_checked: ['pdf'],
          sections: [],
        },
        {
          paper: { id: 'http://arxiv.org/abs/3', title: 'C' },
          languages: [{ language: 'Tamil', count: 1 }],
          sources_checked: [],
          sections: [],
        },
      ],
    });
    writeJson('data/processed/language_data.json', { lang_classes: {}, languages_to_ignore: [] });

    const { loadSiteData } = await importDataModule();
    const result = loadSiteData(undefined, 7);

    expect(result.coverageStats).toEqual({ htmlScanned: 1, pdfOnly: 1, abstractOnly: 1 });
    // No judge_verdict on these entries -> unjudged, counted as "studied" (mirrors build_snapshot_manifest).
    expect(result.languageCounts).toEqual([
      { language: 'Sinhala', count: 2, studied: 2, mentioned_only: 0 },
      { language: 'Tamil', count: 1, studied: 1, mentioned_only: 0 },
    ]);
    expect(result.topLanguages[0]).toMatchObject({ language: 'Sinhala', count: 2 });
    expect(result.weekSeries).toEqual([{ date: '2026-05-18', papers: 3, flagged: 1 }]);
    expect(result.classCounts).toEqual([{ class_id: 2, count: 1 }]);
  });

  it('normalizes string/array/invalid language entry shapes and alphabetizes count ties', async () => {
    writeJson('data/processed/langtrend_manifest_last_7_days.json', {
      daily_series: [],
      class_counts: [],
      flagged_papers: [
        {
          paper: { id: 'http://arxiv.org/abs/1', title: 'A' },
          languages: ['Tamil', ['Sinhala', 2], { notLanguage: true }, null],
          sources_checked: ['html'],
          sections: [],
        },
        {
          paper: { id: 'http://arxiv.org/abs/2', title: 'B' },
          languages: ['Arabic'],
          sources_checked: ['html'],
          sections: [],
        },
      ],
    });
    writeJson('data/processed/language_data.json', { lang_classes: {}, languages_to_ignore: [] });

    const { loadSiteData } = await importDataModule();
    const result = loadSiteData(undefined, 7);

    // Tamil, Sinhala, and Arabic all end up with count 1 -> alphabetical tie-break.
    expect(result.languageCounts).toEqual([
      { language: 'Arabic', count: 1, studied: 1, mentioned_only: 0 },
      { language: 'Sinhala', count: 1, studied: 1, mentioned_only: 0 },
      { language: 'Tamil', count: 1, studied: 1, mentioned_only: 0 },
    ]);
  });

  it('falls back to raw + processed JSONL counts when the manifest file is missing', async () => {
    writeText('data/raw/arxiv_papers_last_7_days.jsonl', '{"id":"1"}\n{"id":"2"}\n');
    writeText('data/processed/papers_with_tracked_langs_last_7_days.jsonl', '{"id":"1"}\n');

    const { loadSiteData } = await importDataModule();
    const result = loadSiteData(undefined, 7);

    expect(result.manifest.counts.papers).toBe(2);
    expect(result.manifest.counts.flagged_papers).toBe(1);
    expect(result.manifest.generated_at).toBeNull();
  });
});

describe('loadSiteData (specific week)', () => {
  const weekStart = '2026-05-18';
  const weekSlug = '20260518_to_20260525';

  it('prefers the sections array already embedded in the manifest', async () => {
    writeJson(`data/processed/weeks/${weekSlug}/langtrend_manifest.json`, {
      flagged_papers: [
        {
          paper: { id: 'http://arxiv.org/abs/1', title: 'A' },
          languages: [],
          sources_checked: ['html'],
          sections: [{ name: 'Introduction', source: 'html', detected_languages: ['Sinhala'] }],
        },
      ],
    });
    writeJson('data/processed/language_data.json', { lang_classes: {}, languages_to_ignore: [] });

    const { loadSiteData } = await importDataModule();
    const result = loadSiteData(weekStart, 7);

    expect(result.flaggedPapers[0].sections).toEqual([
      { name: 'Introduction', source: 'html', detected_languages: ['Sinhala'] },
    ]);
  });

  it('falls back to the detected.jsonl lookup when the manifest has no embedded sections', async () => {
    writeJson(`data/processed/weeks/${weekSlug}/langtrend_manifest.json`, {
      flagged_papers: [
        {
          paper: { id: 'http://arxiv.org/abs/1', title: 'A' },
          languages: [],
          sources_checked: ['html'],
          sections: [],
        },
      ],
    });
    writeText(
      `data/processed/weeks/${weekSlug}/arxiv_papers_${weekSlug}_detected.jsonl`,
      JSON.stringify({
        paper_id: 'http://arxiv.org/abs/1',
        sections: { Introduction: { source: 'html', detected_languages: ['Sinhala'] } },
      }) + '\n',
    );
    writeJson('data/processed/language_data.json', { lang_classes: {}, languages_to_ignore: [] });

    const { loadSiteData } = await importDataModule();
    const result = loadSiteData(weekStart, 7);

    expect(result.flaggedPapers[0].sections).toEqual([
      { name: 'Introduction', source: 'html', detected_languages: ['Sinhala'] },
    ]);
  });
});

describe('loadAllWeeksData', () => {
  it('skips weeks whose manifest file is missing and summarizes the rest', async () => {
    mkdirSync(join(fixtureDir, 'data/processed/weeks/20260504_to_20260511'), { recursive: true }); // no manifest.json
    writeJson('data/processed/weeks/20260518_to_20260525/langtrend_manifest.json', {
      week_end: '2026-05-25',
      counts: { papers: 5, flagged_papers: 2, unique_languages: 1 },
      language_counts: [{ language: 'Sinhala', count: 2 }],
      class_counts: [{ class_id: 2, count: 2 }],
      daily_series: [{ date: '2026-05-18', papers: 5, flagged: 2 }],
      flagged_papers: [{ sources_checked: ['html'] }, { sources_checked: ['pdf'] }],
    });

    const { loadAllWeeksData } = await importDataModule();
    const result = loadAllWeeksData();

    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      weekStart: '2026-05-18',
      weekEnd: '2026-05-25',
      papers: 5,
      flaggedPapers: 2,
      uniqueLanguages: 1,
      coverageStats: { htmlScanned: 1, pdfOnly: 1, abstractOnly: 0 },
    });
  });

  it('returns an empty array when there are no week directories at all', async () => {
    const { loadAllWeeksData } = await importDataModule();
    expect(loadAllWeeksData()).toEqual([]);
  });

  it('sorts multiple weeks chronologically and counts abstract-only papers', async () => {
    writeJson('data/processed/weeks/20260525_to_20260601/langtrend_manifest.json', {
      counts: { papers: 1, flagged_papers: 1, unique_languages: 1 },
      flagged_papers: [{ sources_checked: ['abstract'] }], // neither html nor pdf
    });
    writeJson('data/processed/weeks/20260518_to_20260525/langtrend_manifest.json', {
      counts: { papers: 1, flagged_papers: 1, unique_languages: 1 },
      flagged_papers: [{ sources_checked: ['html'] }],
    });

    const { loadAllWeeksData } = await importDataModule();
    const result = loadAllWeeksData();

    expect(result.map((w) => w.weekStart)).toEqual(['2026-05-18', '2026-05-25']);
    expect(result[1].coverageStats).toEqual({ htmlScanned: 0, pdfOnly: 0, abstractOnly: 1 });
  });
});
