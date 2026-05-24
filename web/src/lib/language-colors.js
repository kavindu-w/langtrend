const BORDER_CLASS_COUNT = 6;

export const LANGUAGE_FILL_PALETTE = [
  '#f7fbfd',
  '#fdf7f2',
  '#f7f8fe',
  '#f8fdf4',
  '#fff7fb',
  '#f7fff9',
  '#fffdf4',
  '#f4f8ff',
  '#faf5ff',
  '#f5fbf8',
  '#fff6f0',
  '#f2fbff',
];

export function hashLanguage(language) {
  let hash = 0;
  for (const char of language) {
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  }
  return hash;
}

export function buildLanguageClassLookup(langClasses = {}) {
  const lookup = {};
  for (const [classId, languages] of Object.entries(langClasses)) {
    const normalizedClassId = Number.parseInt(classId, 10);
    if (Number.isNaN(normalizedClassId)) {
      continue;
    }
    for (const language of languages || []) {
      if (typeof language === 'string' && language.trim()) {
        lookup[language] = normalizedClassId;
      }
    }
  }
  return lookup;
}

export function languageBorderClass(language, langClasses = {}) {
  const lookup = buildLanguageClassLookup(langClasses);
  const classIndex = lookup[language];
  if (typeof classIndex === 'number' && classIndex >= 0 && classIndex < BORDER_CLASS_COUNT) {
    return classIndex;
  }
  return hashLanguage(language) % BORDER_CLASS_COUNT;
}

export function languageFillColor(language) {
  return LANGUAGE_FILL_PALETTE[hashLanguage(language) % LANGUAGE_FILL_PALETTE.length];
}