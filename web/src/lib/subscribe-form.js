// Logic behind SubscribeForm.astro: which languages can be picked, in what
// order they're suggested, and how a ?lang= deep link pre-selects them.
// Selected languages are submitted to Buttondown as subscriber metadata
// (languagesMetadata), which the weekly digest (langtrend/digest.py) matches
// by exact name — so options must be the same canonical names the pipeline
// emits.

import { foldSearchText } from './text-utils.js';

// A UX cap on the picker, enforced in the browser only — a hand-crafted post
// can store anything, which is harmless: the digest only ever tests whether
// ",<language>," occurs in the stored string.
export const MAX_LANGUAGES = 20;

// Language names carry apostrophes/hyphens/spaces ("N'Ko", "Abu'"); drop them
// on both sides so "nko" still finds N'Ko.
const matchKey = (s) => foldSearchText(s).replace(/[^\p{L}\p{N}]/gu, '');

/**
 * All pickable languages, most-detected first (summed over every week), then
 * alphabetical — so "Sin" suggests Sinhala before obscure taxonomy entries.
 */
export function rankLanguageOptions(langClasses, languagesToIgnore = [], weeks = []) {
  const ignore = new Set(languagesToIgnore);
  const totals = new Map();
  for (const week of weeks) {
    for (const row of week.languageCounts ?? []) {
      if (row?.language) totals.set(row.language, (totals.get(row.language) ?? 0) + (row.count ?? 0));
    }
  }
  const names = new Set();
  for (const list of Object.values(langClasses ?? {})) {
    for (const name of list ?? []) {
      if (typeof name === 'string' && name && !ignore.has(name)) names.add(name);
    }
  }
  return [...names].sort((a, b) => (totals.get(b) ?? 0) - (totals.get(a) ?? 0) || a.localeCompare(b));
}

/**
 * Up to `limit` unselected options matching `query`: prefix matches first,
 * then substring matches, each keeping the ranked order. Matching is
 * diacritic/case-insensitive ("nko" finds "N'Ko"). Empty query → top options.
 */
export function suggestLanguages(query, options, selected = [], limit = 8) {
  const taken = new Set(selected);
  const q = matchKey(query ?? '');
  const prefix = [];
  const infix = [];
  for (const name of options) {
    if (taken.has(name)) continue;
    if (!q) {
      prefix.push(name);
    } else {
      const folded = matchKey(name);
      if (folded.startsWith(q)) prefix.push(name);
      else if (folded.includes(q)) infix.push(name);
    }
    if (prefix.length >= limit) break;
  }
  return [...prefix, ...infix].slice(0, limit);
}

/** Languages from a `?lang=A,B` param that are real options, deduped and capped. */
export function languagesFromParam(param, options) {
  if (!param) return [];
  const valid = new Set(options);
  const out = [];
  for (const raw of param.split(',')) {
    const name = raw.trim();
    if (valid.has(name) && !out.includes(name)) out.push(name);
    if (out.length >= MAX_LANGUAGES) break;
  }
  return out;
}

/**
 * Comma-delimited on both ends (",Sinhala,Tamil,") so the digest template can
 * test `",Malay," in languages` without also matching ",Malayalam,".
 * Empty selection → "" (summary-only subscriber).
 */
export function languagesMetadata(selected) {
  const names = (selected ?? []).filter((n) => n && !n.includes(','));
  return names.length ? `,${names.join(',')},` : '';
}

export function subscribeActionUrl(username) {
  return username ? `https://buttondown.com/api/emails/embed-subscribe/${encodeURIComponent(username)}` : null;
}
