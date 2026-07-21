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

// Scans forward from `openIndex` (the index of a '{') for the matching '}',
// honoring nesting (e.g. "\textsuperscript{\textit{3}}"). Returns -1 if the
// brace is never closed, which callers treat as "leave the macro literal".
function findMatchingBrace(text, openIndex) {
  let depth = 0;
  for (let i = openIndex; i < text.length; i++) {
    if (text[i] === '{') depth++;
    else if (text[i] === '}') {
      depth--;
      if (depth === 0) return i;
    }
  }
  return -1;
}

// arXiv titles and abstracts are raw LaTeX source, and both routinely carry
// these two inline-formatting macros (e.g. "DiM\textsuperscript{3}: ...",
// "H\textsubscript{2}O"). Renders them as <sup>/<sub> — recursing so a
// nested macro inside the braces still renders — and HTML-escapes
// everything else in the given (non-math) segment.
function renderScriptMacros(text) {
  const parts = [];
  const macroStart = /\\text(superscript|subscript)\{/g;
  let lastIndex = 0;
  let match;
  while ((match = macroStart.exec(text)) !== null) {
    const openBraceIndex = match.index + match[0].length - 1;
    const closeBraceIndex = findMatchingBrace(text, openBraceIndex);
    if (closeBraceIndex === -1) continue; // unbalanced — leave as literal text

    if (match.index > lastIndex) {
      parts.push(escapeHtml(text.slice(lastIndex, match.index)));
    }
    const tag = match[1] === 'superscript' ? 'sup' : 'sub';
    const inner = text.slice(openBraceIndex + 1, closeBraceIndex);
    parts.push(`<${tag}>${renderScriptMacros(inner)}</${tag}>`);
    lastIndex = closeBraceIndex + 1;
    macroStart.lastIndex = lastIndex;
  }
  parts.push(escapeHtml(text.slice(lastIndex)));
  return parts.join('');
}

/**
 * arXiv abstracts are raw LaTeX source, not rendered output. This renders
 * "$...$" inline math with KaTeX, "\textsuperscript{...}"/"\textsubscript{...}"
 * as <sup>/<sub>, and HTML-escapes everything else, so the result is trusted
 * HTML safe to insert directly (no further escaping). This runs server-side
 * only (build time / API route) — the browser never loads the KaTeX JS
 * engine, only the pre-rendered markup plus its CSS.
 */
export function renderAbstractHtml(text) {
  if (typeof text !== 'string' || !text) return '';

  const parts = [];
  const mathDelimiter = /\$([^$\n]+)\$/g;
  let lastIndex = 0;
  let match;
  while ((match = mathDelimiter.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(renderScriptMacros(normalizeDashes(text.slice(lastIndex, match.index))));
    }
    parts.push(katex.renderToString(match[1], { throwOnError: false, strict: 'ignore' }));
    lastIndex = mathDelimiter.lastIndex;
  }
  if (lastIndex < text.length) {
    parts.push(renderScriptMacros(normalizeDashes(text.slice(lastIndex))));
  }
  return parts.join('');
}

/**
 * arXiv titles are raw LaTeX source too. Renders "\textsuperscript{...}" and
 * "\textsubscript{...}" as <sup>/<sub> and HTML-escapes everything else, so
 * the result is trusted HTML safe to insert directly (no further escaping).
 */
export function renderTitleHtml(text) {
  if (typeof text !== 'string' || !text) return '';
  return renderScriptMacros(text);
}
