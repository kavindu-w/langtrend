// Pure, DOM-free logic behind the paper table's client-side search/filter UI
// (extracted from the inline <script> in PaperTable.astro so it's unit-testable).

/** Delays `fn` until `delay` ms after the last call — collapses a burst of calls into one. */
export function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

/** Parses a paper row's dataset strings (as set by PaperTable.astro) into structured fields. */
export function parseRowMeta(dataset, fallbackWeek) {
  return {
    week: dataset.week ?? fallbackWeek,
    search: dataset.search || '',
    languages: (dataset.languages || '').split('|').filter(Boolean),
    classes: (dataset.classes || '').split(',').filter(Boolean),
    verdicts: (dataset.verdicts || '').split(',').filter(Boolean),
  };
}

/**
 * Which weeks are in view for the current period. `availableWeeks` is oldest→newest;
 * `currentWeekSlug` anchors the "from" range when the page has no explicit week of its own.
 */
export function activeWeekSlugsFor({ periodParam, fromParam }, availableWeeks, currentWeekSlug) {
  if (periodParam === 'all') return [...availableWeeks];
  if (fromParam) {
    const fromIdx = availableWeeks.indexOf(fromParam);
    const anchorIdx = availableWeeks.indexOf(currentWeekSlug);
    if (fromIdx !== -1 && anchorIdx !== -1) {
      return availableWeeks.slice(Math.min(fromIdx, anchorIdx), Math.max(fromIdx, anchorIdx) + 1);
    }
  }
  return [currentWeekSlug];
}

/**
 * Core per-row filter predicate for the paper table. `filters.searchTerm` must already be
 * folded (see foldTitleSearchText, so hyphens/dashes match spaces the same way `meta.search`
 * does); `activeWeekSet`/`activeLanguages`/`enabledVerdicts` are Sets.
 * `inPeriod` is reported separately from `inScope` because chip/verdict counts in the UI are
 * scoped to the active period only, ignoring the verdict/search/language/class filters.
 */
export function matchPaperRow(meta, filters) {
  const inPeriod = filters.activeWeekSet.has(meta.week);
  const matchesVerdict = meta.verdicts.some(v => filters.enabledVerdicts.has(v));
  const inScope = inPeriod && matchesVerdict;
  const matchesLanguage = filters.activeLanguages.size === 0 || meta.languages.some(l => filters.activeLanguages.has(l));
  const matchesSearch = !filters.searchTerm || meta.search.includes(filters.searchTerm);
  const matchesClass = filters.classFilter === 'all' || meta.classes.includes(filters.classFilter);
  const show = inScope && matchesLanguage && matchesSearch && matchesClass;
  return { inPeriod, inScope, show };
}

/** Comparator for the paper list's sort dropdown, operating on {langCount, minClass, title, index}. */
export function compareEntries(mode) {
  switch (mode) {
    case 'lang-desc':     return (a, b) => b.langCount - a.langCount;
    case 'lang-asc':      return (a, b) => a.langCount - b.langCount;
    case 'resource-asc':  return (a, b) => a.minClass - b.minClass;
    case 'resource-desc': return (a, b) => b.minClass - a.minClass;
    case 'title-asc':     return (a, b) => a.title.localeCompare(b.title);
    case 'title-desc':    return (a, b) => b.title.localeCompare(a.title);
    default:               return (a, b) => a.index - b.index;
  }
}

/**
 * Clamps a requested page into range and computes the [start, end) slice bounds.
 * `pageSize` is a positive integer, or the string 'all' to disable paging entirely.
 * totalPages is always >= 1, even for zero items, so "page 1 of 1" is well-defined.
 */
export function paginate(totalItems, requestedPage, pageSize) {
  if (pageSize === 'all') {
    return { page: 1, totalPages: 1, start: 0, end: totalItems };
  }
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const page = Math.min(Math.max(1, requestedPage || 1), totalPages);
  const start = (page - 1) * pageSize;
  const end = Math.min(start + pageSize, totalItems);
  return { page, totalPages, start, end };
}

/**
 * Compact page-button layout: full run of pages if it fits, otherwise
 * first + last pinned with `null` gaps (render as an ellipsis) around a
 * window centered on the current page. siblingCount is pages shown on
 * each side of the current page within that window.
 */
export function paginationWindow(currentPage, totalPages, siblingCount = 1) {
  if (totalPages <= 1) return [1];
  // Below this size, showing every page number outright is more useful than any ellipsis.
  const totalNumbers = siblingCount * 2 + 5;
  if (totalPages <= totalNumbers) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  const left = Math.max(currentPage - siblingCount, 1);
  const right = Math.min(currentPage + siblingCount, totalPages);
  const items = [1];
  if (left > 2) items.push(null);
  for (let p = Math.max(left, 2); p <= Math.min(right, totalPages - 1); p++) items.push(p);
  if (right < totalPages - 1) items.push(null);
  items.push(totalPages);
  return items;
}
