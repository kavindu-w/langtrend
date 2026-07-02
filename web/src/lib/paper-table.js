import { languageBorderClass, languageFillColor } from './language-colors.js';

const SOURCE_LABEL = { abstract: 'Abstract', html: 'Full Text (HTML)', pdf: 'Full Text (PDF)' };
const SOURCE_ORDER = ['abstract', 'html', 'pdf'];

export function normalizeLanguage(entry) {
  if (typeof entry === 'string') return entry;
  if (Array.isArray(entry)) return entry[0] ?? '';
  return entry?.language ?? entry?.name ?? '';
}

export function classFromEntry(entry) {
  if (Array.isArray(entry) && typeof entry[1] === 'number') return entry[1];
  if (!Array.isArray(entry) && typeof entry === 'object' && entry && typeof entry.class === 'number') return entry.class;
  return null;
}

export function sourcesOfEntry(entry) {
  if (typeof entry === 'string' || Array.isArray(entry)) return [];
  return entry?.sources ?? [];
}

export function formatSectionTitle(sectionName) {
  const compact = sectionName.replace(/\s+/g, ' ').trim();
  if (!compact) return 'Untitled section';

  const appendixMatch = compact.match(/^(Appendix\s+[A-Z])(?=[A-Z0-9])/);
  if (appendixMatch) {
    const prefix = appendixMatch[1];
    const suffix = compact.slice(prefix.length).replace(/^[\s.]+/, '');
    return suffix ? `${prefix}. ${suffix}` : prefix;
  }

  const numberedMatch = compact.match(/^((?:[A-Z]\.?\d+(?:\.\d+)*|\d+(?:\.\d+)*|[A-Z]))(?=[A-Z])/);
  if (numberedMatch) {
    const prefix = numberedMatch[1].replace(/\.$/, '');
    const suffix = compact.slice(numberedMatch[1].length).replace(/^[\s.]+/, '');
    return suffix ? `${prefix}. ${suffix}` : prefix;
  }

  return compact;
}

/** @param {object} entry @param {Record<string, unknown[]>} langClasses @param {Record<string, string>} pfpMap */
export function chipFromEntry(entry, langClasses, pfpMap = {}) {
  const language = normalizeLanguage(entry);
  const explicitNeeds = !Array.isArray(entry) && typeof entry === 'object' && !!entry?.needs_review;
  const explicitReason = !Array.isArray(entry) && typeof entry === 'object' ? (entry?.flag_reason ?? '') : '';
  const twoLetterCode = typeof language === 'string' && /^[A-Za-z]{2}$/.test(language.trim());
  const pfpReason = typeof language === 'string' && pfpMap[language] ? String(pfpMap[language]) : '';
  const needsReview = explicitNeeds || twoLetterCode || !!pfpReason;
  const flagReason = explicitReason || pfpReason || (twoLetterCode ? 'Detected as 2-letter language code — confirm mapping/label.' : '');
  return {
    language,
    borderClass: classFromEntry(entry) ?? languageBorderClass(language, langClasses),
    needsReview,
    flagReason,
    fillColor: languageFillColor(language),
  };
}

export function formatDate(iso) {
  if (!iso) return '';
  try { return new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC' }); }
  catch { return iso; }
}

/** @param {object} item @param {number} index @param {string} weekStart @param {Record<string, unknown[]>} langClasses @param {Record<string, string>} pfpMap */
export function buildPaperItem(item, index, weekStart, langClasses = {}, pfpMap = {}) {
  const paper = item.paper;
  const allAuthors = Array.isArray(paper.authors) && paper.authors.length > 0 ? paper.authors.join(', ') : 'Unknown authors';

  const chipLanguages = [...item.languages].filter(Boolean).sort((l, r) => {
    const lc = classFromEntry(l) ?? languageBorderClass(normalizeLanguage(l), langClasses);
    const rc = classFromEntry(r) ?? languageBorderClass(normalizeLanguage(r), langClasses);
    const d = rc - lc;
    return d !== 0 ? d : normalizeLanguage(l).localeCompare(normalizeLanguage(r));
  });

  const chips = chipLanguages.map(entry => chipFromEntry(entry, langClasses, pfpMap));
  const chipLanguageNames = chips.map(c => c.language).filter(Boolean);
  const minClass = chips.length > 0 ? chips.reduce((min, c) => Math.min(min, c.borderClass), 5) : 5;

  const sources = item.sourcesChecked ?? [];
  const hasHtml = sources.includes('html');
  const hasPdf = sources.includes('pdf');
  const coverageBadge = !hasHtml
    ? (hasPdf
        ? { label: 'PDF & Abstract', title: 'HTML version could not be extracted — analysis done with PDF and abstract' }
        : { label: 'Abstract only', title: 'HTML and PDF versions could not be extracted — analysis done with abstract only' })
    : null;

  const searchText = `${paper.title} ${paper.authors.join(' ')}`.toLowerCase();

  const sourcesMap = new Map();
  for (const entry of chipLanguages) {
    const lang = normalizeLanguage(entry);
    if (!lang) continue;
    for (const src of sourcesOfEntry(entry)) {
      if (!sourcesMap.has(src)) sourcesMap.set(src, []);
      sourcesMap.get(src).push(lang);
    }
  }
  const sourcesGroups = [...sourcesMap.entries()]
    .sort(([a], [b]) => {
      const ai = SOURCE_ORDER.indexOf(a);
      const bi = SOURCE_ORDER.indexOf(b);
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    })
    .map(([src, langs]) => ({ src, label: SOURCE_LABEL[src] ?? src, langs }));

  const sections = (item.sections || [])
    .filter((section) => section?.name)
    .map((section) => ({
      name: section.name,
      label: formatSectionTitle(section.name),
      source: section.source,
      sourceLabel: SOURCE_LABEL[section.source] ?? section.source,
      chips: (section.detected_languages || [])
        .filter(Boolean)
        .map((entry) => chipFromEntry(entry, langClasses, pfpMap))
        .sort((left, right) => {
          const classDelta = right.borderClass - left.borderClass;
          return classDelta !== 0 ? classDelta : left.language.localeCompare(right.language);
        }),
    }));

  const pubDateStr = formatDate(paper.published);
  const updDateStr = formatDate(paper.updated);
  const pubDay = paper.published ? paper.published.slice(0, 10) : '';
  const updDay = paper.updated ? paper.updated.slice(0, 10) : '';
  const showUpdated = !!updDay && updDay !== pubDay;
  const arxivUrl = paper.id ? paper.id.replace('http://', 'https://') : paper.pdf_url;

  const acronymConflicts = (item.warnings ?? []).filter(w => w.step === 'acronym_language_conflict');

  const suppressedByLang = new Map();
  for (const w of acronymConflicts) {
    if (!w.language || !w.acronym) continue;
    if (chipLanguageNames.includes(w.language)) continue;
    if (!suppressedByLang.has(w.language)) {
      suppressedByLang.set(w.language, { language: w.language, borderClass: w.language_class ?? 0, acronyms: [] });
    }
    const entry = suppressedByLang.get(w.language);
    if (!entry.acronyms.includes(w.acronym)) entry.acronyms.push(w.acronym);
  }
  const suppressedChips = [...suppressedByLang.values()];

  return { index, paper, allAuthors, chips, chipLanguageNames, minClass, hasPdf, coverageBadge, searchText, sourcesGroups, sections, pubDateStr, updDateStr, showUpdated, arxivUrl, acronymConflicts, suppressedChips, weekStart };
}
