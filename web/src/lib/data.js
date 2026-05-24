import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '../../..');
const dataRoot = path.join(repoRoot, 'data');

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

export function loadSiteData(windowDays = 7) {
  const manifestPath = path.join(dataRoot, 'processed', `langtrend_manifest_last_${windowDays}_days.json`);
  const manifest = readJson(manifestPath, fallbackManifest(windowDays));
  const languageData = readJson(path.join(dataRoot, 'processed', 'language_data.json'), {
    lang_classes: {},
    languages_to_ignore: [],
  });

  const flaggedPapers = (manifest.flagged_papers || []).map((item) => ({
    paper: item.paper,
    languages: item.languages || [],
  }));

  const languageCounts = manifest.language_counts || [];
  const topLanguages = languageCounts.slice(0, 12).map((item, index) => ({
    ...item,
    color: colorForIndex(index),
  }));

  const weekSeries = manifest.daily_series || [];
  const papers = manifest.papers || [];

  return {
    manifest,
    languageData,
    flaggedPapers,
    languageCounts,
    topLanguages,
    weekSeries,
    papers,
  };
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
