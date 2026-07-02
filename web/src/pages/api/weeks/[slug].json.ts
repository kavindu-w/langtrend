import type { APIRoute } from 'astro';
import { getAvailableWeeks, loadSiteData } from '../../../lib/data.js';
import { buildWeekApiPaper } from '../../../lib/paper-table.js';

export function getStaticPaths() {
  return getAvailableWeeks().map((weekStart) => ({ params: { slug: weekStart } }));
}

export const GET: APIRoute = ({ params }) => {
  const data = loadSiteData(params.slug);
  const langClasses = data.languageData?.lang_classes ?? {};

  const papers = data.flaggedPapers.map((item) => buildWeekApiPaper(item, langClasses));

  return new Response(JSON.stringify(papers), {
    headers: { 'Content-Type': 'application/json' },
  });
};
