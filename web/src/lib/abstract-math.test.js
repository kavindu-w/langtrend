import { describe, expect, it } from 'vitest';
import { renderAbstractHtml, renderTitleHtml } from './abstract-math.js';

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

  it('renders \\textsuperscript/\\textsubscript as <sup>/<sub>, same as titles', () => {
    expect(renderAbstractHtml('H\\textsubscript{2}O yields x\\textsuperscript{2} error')).toBe(
      'H<sub>2</sub>O yields x<sup>2</sup> error',
    );
  });
});

describe('renderTitleHtml', () => {
  it('renders \\textsuperscript as <sup>', () => {
    expect(renderTitleHtml('DiM\\textsuperscript{3}: Bridging Multilingual and Multimodal Models')).toBe(
      'DiM<sup>3</sup>: Bridging Multilingual and Multimodal Models',
    );
  });

  it('renders \\textsubscript as <sub>', () => {
    expect(renderTitleHtml('H\\textsubscript{2}O in Language Models')).toBe(
      'H<sub>2</sub>O in Language Models',
    );
  });

  it('HTML-escapes plain text so it is safe to insert directly', () => {
    expect(renderTitleHtml('A "Study" of x < y & y > x')).toBe(
      'A &quot;Study&quot; of x &lt; y &amp; y &gt; x',
    );
  });

  it('escapes content inside the macro braces too', () => {
    expect(renderTitleHtml('x\\textsuperscript{<a>}')).toBe('x<sup>&lt;a&gt;</sup>');
  });

  it('handles a nested macro inside the braces', () => {
    expect(renderTitleHtml('x\\textsuperscript{\\textit{3}}')).toBe('x<sup>\\textit{3}</sup>');
    expect(renderTitleHtml('x\\textsuperscript{a\\textsubscript{b}c}')).toBe(
      'x<sup>a<sub>b</sub>c</sup>',
    );
  });

  it('leaves an unbalanced macro as literal (escaped) text instead of dropping it', () => {
    expect(renderTitleHtml('x\\textsuperscript{3')).toBe('x\\textsuperscript{3');
  });

  it('handles missing/non-string/empty input', () => {
    expect(renderTitleHtml(undefined)).toBe('');
    expect(renderTitleHtml('')).toBe('');
  });

  it('renders text with no LaTeX artifacts unchanged (aside from HTML-escaping)', () => {
    expect(renderTitleHtml('A Plain Title')).toBe('A Plain Title');
  });
});
