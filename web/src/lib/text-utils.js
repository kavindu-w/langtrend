// Author and language names routinely carry diacritics or typographic
// apostrophes (e.g. "é", "N'ko") that a user won't type on a
// plain keyboard -- fold both sides of a search match through this so
// an ASCII-only query still hits.
//
// Kept dependency-free in its own module: this is imported directly by the
// client-side search/filter script in PaperTable.astro, and must not drag in
// server-only, heavier modules (e.g. katex via paper-table.js/abstract-math.js)
// into the browser bundle.
export function foldSearchText(s) {
  return s
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[‘’ʼ]/g, "'")
    .toLowerCase();
}
