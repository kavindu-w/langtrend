import katex from 'katex';

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// LaTeX's own dash convention, applied only to the plain-text stretches
// between math segments (KaTeX handles dashes that occur inside math itself).
function normalizeDashes(text) {
  return text.replace(/---/g, '—').replace(/--/g, '–');
}

/**
 * arXiv abstracts are raw LaTeX source, not rendered output. This renders
 * "$...$" inline math with KaTeX and HTML-escapes everything else, so the
 * result is trusted HTML safe to insert directly (no further escaping).
 * This runs server-side only (build time / API route) — the browser never
 * loads the KaTeX JS engine, only the pre-rendered markup plus its CSS.
 */
export function renderAbstractHtml(text) {
  if (typeof text !== 'string' || !text) return '';

  const parts = [];
  const mathDelimiter = /\$([^$\n]+)\$/g;
  let lastIndex = 0;
  let match;
  while ((match = mathDelimiter.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(escapeHtml(normalizeDashes(text.slice(lastIndex, match.index))));
    }
    parts.push(katex.renderToString(match[1], { throwOnError: false, strict: 'ignore' }));
    lastIndex = mathDelimiter.lastIndex;
  }
  if (lastIndex < text.length) {
    parts.push(escapeHtml(normalizeDashes(text.slice(lastIndex))));
  }
  return parts.join('');
}
