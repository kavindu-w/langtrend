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
 * folded (see foldSearchText); `activeWeekSet`/`activeLanguages`/`enabledVerdicts` are Sets.
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
