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

  // "Appendix B" is never mistakable for the start of a real title, so this
  // one is safe to match whether the source glued the title on directly
  // (older extraction bug) or already put a space there (fixed extraction).
  const appendixMatch = compact.match(/^(Appendix\s+[A-Z])(?=\s*[A-Z0-9])/);
  if (appendixMatch) {
    const prefix = appendixMatch[1];
    const suffix = compact.slice(prefix.length).replace(/^[\s.]+/, '');
    return suffix ? `${prefix}. ${suffix}` : prefix;
  }

  // Numeric/dotted prefixes ("4.1", "B.3") can't be confused with an English
  // word either, so likewise match with or without a space after them.
  const numericMatch = compact.match(/^([A-Z]\.?\d+(?:\.\d+)*|\d+(?:\.\d+)*)(?=\s*[A-Z])/);
  if (numericMatch) {
    const prefix = numericMatch[1].replace(/\.$/, '');
    const suffix = compact.slice(numericMatch[1].length).replace(/^[\s.]+/, '');
    return suffix ? `${prefix}. ${suffix}` : prefix;
  }

  // A bare single letter ("B") is NOT safe to match with a space allowed —
  // "A Survey of X" is a common real title, indistinguishable from a
  // lettered appendix subsection once the number and title both have a
  // real space between them. Only catch the old no-space glued form here.
  const letterMatch = compact.match(/^([A-Z])(?=[A-Z])/);
  if (letterMatch) {
    const prefix = letterMatch[1];
    const suffix = compact.slice(prefix.length).replace(/^[\s.]+/, '');
    return suffix ? `${prefix}. ${suffix}` : prefix;
  }

  return compact;
}

export function judgeVerdictOfEntry(entry) {
  if (typeof entry === 'string' || Array.isArray(entry)) return '';
  return typeof entry?.judge_verdict === 'string' ? entry.judge_verdict : '';
}

export function isJudgedFalsePositive(entry) {
  return judgeVerdictOfEntry(entry) === 'false_positive';
}

/** @param {object} entry @param {Record<string, unknown[]>} langClasses @param {Record<string, string>} pfpMap */
export function chipFromEntry(entry, langClasses, pfpMap = {}) {
  const language = normalizeLanguage(entry);
  const explicitNeeds = !Array.isArray(entry) && typeof entry === 'object' && !!entry?.needs_review;
  const explicitReason = !Array.isArray(entry) && typeof entry === 'object' ? (entry?.flag_reason ?? '') : '';
  const twoLetterCode = typeof language === 'string' && /^[A-Za-z]{2}$/.test(language.trim());
  const pfpReason = typeof language === 'string' && pfpMap[language] ? String(pfpMap[language]) : '';
  const judgeVerdict = judgeVerdictOfEntry(entry);
  // A "studied" verdict overrides the heuristic review flags — the judge confirmed the language.
  const needsReview = judgeVerdict !== 'studied' && (explicitNeeds || twoLetterCode || !!pfpReason);
  const flagReason = explicitReason || pfpReason || (twoLetterCode ? 'Detected as 2-letter language code — confirm mapping/label.' : '');
  const isObject = !Array.isArray(entry) && typeof entry === 'object' && entry !== null;
  return {
    language,
    borderClass: classFromEntry(entry) ?? languageBorderClass(language, langClasses),
    needsReview,
    flagReason,
    fillColor: languageFillColor(language),
    judgeVerdict,
    judgeReason: isObject ? (entry.judge_reason ?? '') : '',
    mentionedOnly: judgeVerdict === 'mentioned_only',
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

  // Languages the LLM judge rejected are hidden from the chip row (and from
  // filtering/counting) but surfaced in their own popover chips for audit.
  const judgeRejected = [...item.languages].filter(Boolean).filter(isJudgedFalsePositive);
  const chipLanguages = [...item.languages].filter(Boolean).filter((e) => !isJudgedFalsePositive(e)).sort((l, r) => {
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
        .filter((entry) => !isJudgedFalsePositive(entry))
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

  const judgeSuppressedChips = judgeRejected.map((entry) => ({
    language: normalizeLanguage(entry),
    borderClass: classFromEntry(entry) ?? languageBorderClass(normalizeLanguage(entry), langClasses),
    reason: entry.judge_reason ?? '',
    model: entry.judge_model ?? '',
  }));

  return { index, paper, allAuthors, chips, chipLanguageNames, minClass, hasPdf, coverageBadge, searchText, sourcesGroups, sections, pubDateStr, updDateStr, showUpdated, arxivUrl, acronymConflicts, suppressedChips, judgeSuppressedChips, weekStart };
}

/**
 * Shapes a flagged-paper record for the /api/weeks/[slug].json route.
 * needsReview matches chipFromEntry's rule: explicit flag, two-letter code, or a
 * hit in the false-positive-language map.
 * @param {object} item @param {Record<string, unknown[]>} langClasses @param {Record<string, string>} pfpMap
 */
export function buildWeekApiPaper(item, langClasses = {}, pfpMap = {}) {
  const paper = item.paper;
  const languages = [...item.languages].filter(Boolean).filter((entry) => !isJudgedFalsePositive(entry)).map((entry) => {
    const language = normalizeLanguage(entry);
    const borderClass = classFromEntry(entry) ?? languageBorderClass(language, langClasses);
    const judgeVerdict = judgeVerdictOfEntry(entry);
    const needsReview = judgeVerdict !== 'studied' && (
      (!Array.isArray(entry) && typeof entry === 'object' && !!entry?.needs_review)
      || (typeof language === 'string' && /^[A-Za-z]{2}$/.test(language.trim()))
      || (typeof language === 'string' && !!pfpMap[language])
    );
    return { language, borderClass, fillColor: languageFillColor(language), needsReview, judgeVerdict };
  }).sort((a, b) => b.borderClass - a.borderClass || a.language.localeCompare(b.language));

  const languageNames = languages.map((l) => l.language).filter(Boolean);
  const minClass = languages.length > 0 ? Math.min(...languages.map((l) => l.borderClass)) : 5;
  const classes = [...new Set(languages.map((l) => l.borderClass))];

  return {
    id: paper.id ?? '',
    title: paper.title,
    authors: paper.authors,
    abstract: paper.abstract,
    pdf_url: paper.pdf_url,
    arxiv_url: paper.id ? paper.id.replace('http://', 'https://') : paper.pdf_url,
    published: paper.published ?? '',
    categories: paper.categories ?? [],
    searchText: `${paper.title} ${paper.authors.join(' ')}`.toLowerCase(),
    languages,
    languageNames,
    langCount: languageNames.length,
    minClass,
    classes,
  };
}
