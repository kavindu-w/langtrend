// Author, language, and paper-title text routinely carries diacritics,
// typographic apostrophes (e.g. "é", "N'ko"), superscript digits (e.g.
// "Dim³"), or raw LaTeX source -- arXiv titles are unrendered LaTeX, so
// "DiM\textsuperscript{3}" or "R^3" show up literally. Fold both sides of a
// search match through this so a plain "dim3"/"r3" query still hits without
// the user needing to type LaTeX syntax or find the superscript character.
//
// Kept dependency-free in its own module: this is imported directly by the
// client-side search/filter script in PaperTable.astro, and must not drag in
// server-only, heavier modules (e.g. katex via paper-table.js/abstract-math.js)
// into the browser bundle.
const SUPERSCRIPT_DIGITS = { '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4', '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9' };

// Unwraps \textsuperscript{...}/\textsubscript{...} down to their inner text
// (looping to also unwrap nested macros), then drops the leftover LaTeX
// math-mode noise ($, ^, braces) that isn't part of any recognized macro —
// e.g. "M$^3$Eval" -> "M3Eval". None of these characters carry meaning in a
// paper title on their own, so it's safe to just discard them for matching.
function stripLatexNoise(s) {
  let prev;
  do {
    prev = s;
    s = s.replace(/\\text(?:superscript|subscript)\{([^{}]*)\}/g, '$1');
  } while (s !== prev);
  return s.replace(/[$^{}]/g, '');
}

export function foldSearchText(s) {
  return stripLatexNoise(s)
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[⁰¹²³⁴⁵⁶⁷⁸⁹]/g, (d) => SUPERSCRIPT_DIGITS[d])
    // Normalized to a plain quote, not dropped: some taxonomy entries are
    // genuinely distinct only by a trailing apostrophe (e.g. "Kwa"/"Kwa'",
    // "Abu"/"Abu'"), so this must stay an identity-preserving fold — used for
    // exact-match/"already active" checks as well as substring search.
    .replace(/[‘’ʼ]/g, "'")
    .toLowerCase();
}

// Drops the apostrophe entirely on top of foldSearchText, for substring/
// prefix matching only (e.g. the language typeahead and the chip-row filter)
// so "n'ko" and "nko" are treated as the same query there. Must NOT be used
// for exact-equality/identity checks (already-active filter, exact chip
// match) — see the note in foldSearchText above.
export function foldForSubstringMatch(s) {
  return foldSearchText(s).replace(/'/g, '');
}
