import { describe, expect, it } from 'vitest';
import { renderAbstractHtml } from './abstract-math.js';

describe('renderAbstractHtml', () => {
  it('renders inline math with KaTeX', () => {
    const html = renderAbstractHtml("the backbone's last-$n$ layers");
    expect(html).toContain('class="katex"');
    expect(html).not.toContain('$n$');
  });

  it('converts LaTeX dash syntax to typeset dashes outside math', () => {
    expect(renderAbstractHtml('coordinate -- approximating -- before')).toBe(
      'coordinate – approximating – before',
    );
    expect(renderAbstractHtml('a---b')).toBe('a—b');
  });

  it('HTML-escapes plain text so it is safe to insert directly', () => {
    expect(renderAbstractHtml('a paper defining "<think>" as a tag & using x < y')).toBe(
      'a paper defining &quot;&lt;think&gt;&quot; as a tag &amp; using x &lt; y',
    );
  });

  it('does not choke on unbalanced or malformed math, falling back gracefully', () => {
    expect(() => renderAbstractHtml('unbalanced $ dollar sign')).not.toThrow();
  });

  it('handles missing/non-string/empty input', () => {
    expect(renderAbstractHtml(undefined)).toBe('');
    expect(renderAbstractHtml('')).toBe('');
  });

  it('renders text with no LaTeX artifacts unchanged (aside from HTML-escaping)', () => {
    expect(renderAbstractHtml('plain abstract text')).toBe('plain abstract text');
  });
});
