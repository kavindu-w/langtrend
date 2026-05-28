import fs from 'node:fs';
import path from 'node:path';

// In production (Vercel), data files are bundled into the function dir alongside the code.
// In local dev/build, data lives at ../data relative to web/ (the repo root).
function findDataRoot() {
  const bundled = path.join(process.cwd(), 'data');
  if (fs.existsSync(bundled)) return bundled;
  return path.resolve(process.cwd(), '..', 'data');
}
const dataRoot = findDataRoot();

function readJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  } catch {
    return fallback;
  }
}

function readJsonl(filePath) {
  try {
    return fs
      .readFileSync(filePath, 'utf-8')
      .split('\n')
      .filter(Boolean)
      .map((line) => JSON.parse(line));
  } catch {
    return [];
  }
}

function normalizePaperId(paperId) {
  return typeof paperId === 'string' ? paperId.replace(/^https:\/\//, 'http://') : '';
}

function normalizeLanguageEntry(entry) {
  if (typeof entry === 'string') {
    return entry;
  }

  if (Array.isArray(entry)) {
    return entry[0] || '';
  }

  if (entry && typeof entry === 'object') {
    return entry.language || entry.name || '';
  }

  return '';
}

function countLanguages(flaggedPapers) {
  const counts = new Map();
  for (const item of flaggedPapers) {
    for (const entry of item.languages || []) {
      const language = normalizeLanguageEntry(entry);
      if (!language) {
        continue;
      }
      counts.set(language, (counts.get(language) || 0) + 1);
    }
  }

  return [...counts.entries()]
    .map(([language, count]) => ({ language, count }))
    .sort((left, right) => {
      const countDelta = right.count - left.count;
      if (countDelta !== 0) {
        return countDelta;
      }
      return left.language.localeCompare(right.language);
    });
}

function fallbackManifest(windowDays = 7) {
  const papers = readJsonl(path.join(dataRoot, 'raw', `arxiv_papers_last_${windowDays}_days.jsonl`));
  const flagged = readJsonl(
    path.join(dataRoot, 'processed', `papers_with_tracked_langs_last_${windowDays}_days.jsonl`),
  );
  return {
    generated_at: null,
    window_days: windowDays,
    query: 'cat:cs.CL',
    counts: {
      papers: papers.length,
      flagged_papers: flagged.length,
      unique_languages: 0,
    },
    language_counts: [],
    class_counts: [],
    daily_series: [],
    papers,
    flagged_papers: flagged,
  };
}

function datedManifestPath(weekStart) {
  const start = new Date(weekStart + 'T00:00:00Z');
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() + 7);
  const compact = (d) => d.toISOString().slice(0, 10).replace(/-/g, '');
  const slug = `${compact(start)}_to_${compact(end)}`;
  return path.join(dataRoot, 'processed', 'weeks', slug, 'langtrend_manifest.json');
}

function datedDetectedPath(weekStart) {
  const start = new Date(weekStart + 'T00:00:00Z');
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() + 7);
  const compact = (d) => d.toISOString().slice(0, 10).replace(/-/g, '');
  const slug = `${compact(start)}_to_${compact(end)}`;
  return path.join(dataRoot, 'processed', 'weeks', slug, `arxiv_papers_${slug}_detected.jsonl`);
}

function normalizeSections(sectionMap) {
  if (!sectionMap || typeof sectionMap !== 'object') {
    return [];
  }

  return Object.entries(sectionMap).map(([name, value]) => ({
    name,
    source: typeof value?.source === 'string' ? value.source : '',
    detected_languages: Array.isArray(value?.detected_languages) ? value.detected_languages : [],
  }));
}

export const CLASS_LABELS = ['Class 0', 'Class 1', 'Class 2', 'Class 3', 'Class 4', 'Class 5'];

/** @param {string | undefined} weekStart @param {number} windowDays */
export function loadSiteData(weekStart = undefined, windowDays = 7) {
  const manifestPath = weekStart
    ? datedManifestPath(weekStart)
    : path.join(dataRoot, 'processed', `langtrend_manifest_last_${windowDays}_days.json`);
  const manifest = readJson(manifestPath, fallbackManifest(windowDays));
  const detectedRecords = weekStart ? readJsonl(datedDetectedPath(weekStart)) : [];
  const detectedByPaperId = new Map(
    detectedRecords.map((record) => [
      normalizePaperId(record?.paper?.id || record?.paper_id || ''),
      record?.sections || {},
    ]),
  );
  const languageData = readJson(path.join(dataRoot, 'processed', 'language_data.json'), {
    lang_classes: {},
    languages_to_ignore: [],
  });

  const flaggedPapers = (manifest.flagged_papers || []).map((item) => ({
    paper: item.paper,
    languages: item.languages || [],
    sourcesChecked: item.sources_checked || [],
    sections: normalizeSections(detectedByPaperId.get(normalizePaperId(item.paper?.id || ''))),
    warnings: item.warnings || [],
  }));

  const coverageStats = flaggedPapers.reduce(
    (acc, item) => {
      const s = item.sourcesChecked;
      if (s.includes('html')) acc.htmlScanned++;
      else if (s.includes('pdf')) acc.pdfOnly++;
      else acc.abstractOnly++;
      return acc;
    },
    { htmlScanned: 0, pdfOnly: 0, abstractOnly: 0 },
  );

  const languageCounts = countLanguages(flaggedPapers);
  const topLanguages = languageCounts.slice(0, 12).map((item, index) => ({
    ...item,
    color: colorForIndex(index),
  }));

  const weekSeries = manifest.daily_series || [];
  const classCounts = manifest.class_counts || [];
  const papers = manifest.papers || [];

  return {
    manifest,
    languageData,
    flaggedPapers,
    coverageStats,
    languageCounts,
    topLanguages,
    weekSeries,
    classCounts,
    papers,
  };
}

export function loadAllWeeksData() {
  return getAvailableWeeks()
    .map((weekStart) => {
      const manifest = readJson(datedManifestPath(weekStart), null);
      if (!manifest) {
        return null;
      }

      return {
        weekStart,
        weekEnd: manifest.week_end ?? null,
        papers: manifest.counts?.papers ?? 0,
        flaggedPapers: manifest.counts?.flagged_papers ?? 0,
        uniqueLanguages: manifest.counts?.unique_languages ?? 0,
        languageCounts: Array.isArray(manifest.language_counts) ? manifest.language_counts : [],
        classCounts: Array.isArray(manifest.class_counts) ? manifest.class_counts : [],
        dailySeries: Array.isArray(manifest.daily_series) ? manifest.daily_series : [],
      };
    })
    .filter(Boolean)
    .sort((left, right) => left.weekStart.localeCompare(right.weekStart));
}

export function getAvailableWeeks() {
  const weeksDir = path.join(dataRoot, 'processed', 'weeks');
  try {
    return fs.readdirSync(weeksDir)
      .filter(name => /^\d{8}_to_\d{8}$/.test(name))
      .map(name => `${name.slice(0,4)}-${name.slice(4,6)}-${name.slice(6,8)}`)
      .sort();
  } catch { return []; }
}

export function colorForIndex(index) {
  const palette = ['#d7263d', '#f46036', '#2e294e', '#1b998b', '#8e7dbe', '#f4d35e', '#33658a', '#c97b84'];
  return palette[index % palette.length];
}

export function colorForLanguage(language) {
  const palette = ['#d7263d', '#f46036', '#2e294e', '#1b998b', '#8e7dbe', '#f4d35e', '#33658a', '#c97b84'];
  let hash = 0;
  for (const char of language) {
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  }
  return palette[hash % palette.length];
}

export function formatAuthors(authors, maxLength = 72) {
  const fullList = Array.isArray(authors) && authors.length > 0 ? authors.join(', ') : 'Unknown authors';

  if (fullList === 'Unknown authors' || fullList.length <= maxLength) {
    return {
      display: fullList,
      title: fullList,
    };
  }

  const shortList = authors.slice(0, 3).join(', ');
  return {
    display: `${shortList} et al.`,
    title: fullList,
  };
}
