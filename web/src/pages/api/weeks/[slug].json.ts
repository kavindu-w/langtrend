import type { APIRoute } from 'astro';
import { getAvailableWeeks, loadSiteData } from '../../../lib/data.js';
import { languageBorderClass, languageFillColor } from '../../../lib/language-colors.js';

export function getStaticPaths() {
  return getAvailableWeeks().map((weekStart) => ({ params: { slug: weekStart } }));
}

type LangEntry = string | [string, number] | { language?: string | null; class?: number; name?: string; needs_review?: boolean; flag_reason?: string };

function normalizeLanguage(entry: LangEntry): string {
  if (typeof entry === 'string') return entry;
  if (Array.isArray(entry)) return entry[0] ?? '';
  return entry?.language ?? entry?.name ?? '';
}

function classFromEntry(entry: LangEntry): number | null {
  if (Array.isArray(entry) && typeof entry[1] === 'number') return entry[1];
  if (!Array.isArray(entry) && typeof entry === 'object' && entry && typeof (entry as any).class === 'number') return (entry as any).class;
  return null;
}

export const GET: APIRoute = ({ params }) => {
  const data = loadSiteData(params.slug);
  const langClasses = data.languageData?.lang_classes ?? {};

  const papers = data.flaggedPapers.map((item) => {
    const paper = item.paper;
    const languages = [...item.languages].filter(Boolean).map((entry) => {
      const language = normalizeLanguage(entry as LangEntry);
      const borderClass = classFromEntry(entry as LangEntry) ?? languageBorderClass(language, langClasses);
      const needsReview = (!Array.isArray(entry) && typeof entry === 'object' && !!(entry as any)?.needs_review)
        || (typeof language === 'string' && /^[A-Za-z]{2}$/.test(language.trim()));
      return { language, borderClass, fillColor: languageFillColor(language), needsReview };
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
  });

  return new Response(JSON.stringify(papers), {
    headers: { 'Content-Type': 'application/json' },
  });
};
