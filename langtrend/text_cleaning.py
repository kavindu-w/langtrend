from __future__ import annotations

import functools
import json
import re
import threading
import unicodedata
from collections import Counter
from pathlib import Path

_APOSTROPHE_VARIANTS = "''ʼʻʹʽ`"
_HYPHEN_VARIANTS = "-‐‑‒–—−"
_LANGUAGE_NAME_EXTRA_CHARS = "()|!‡"
_SPECIAL_CHARS = set(_APOSTROPHE_VARIANTS + _HYPHEN_VARIANTS + _LANGUAGE_NAME_EXTRA_CHARS)


def _should_keep_language_char(ch: str) -> bool:
    if ch.isspace():
        return True
    if ch.isalpha():
        return True
    return ch in _SPECIAL_CHARS


def _normalize_text_for_screening(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).replace(" ", " ")
    return normalized.replace("et al .", "et al.")


def replace_non_letters_with_spaces(input_string: str) -> str:
    return "".join(ch if _should_keep_language_char(ch) else " " for ch in input_string)


# --- Regex patterns ---

_CITATION_AUTHOR_YEAR_RE = re.compile(
    r"\b\w+\s+et\s+al\.?\s*[(\[]\s*(?:19|20)\d{2}[a-z]?\s*[)\]]",
    re.IGNORECASE,
)
_CITATION_PAREN_RE = re.compile(
    r"\((?=[^()]{0,260}\b(?:et\s+al\.?|(?:19|20)\d{2}[a-z]?)\b)[^()]{0,300}\)",
    re.IGNORECASE,
)
_CITATION_BRACKET_RE = re.compile(
    r"\[(?=[^\[\]]{0,260}\b(?:et\s+al\.?|(?:19|20)\d{2}[a-z]?)\b)[^\[\]]{0,300}\]",
    re.IGNORECASE,
)
_INLINE_MATH_RE = re.compile(r"\$[^$]+\$|\\\([^)]*\\\)|\\\[[^\]]*\\\]")
_MATH_COMMAND_RE = re.compile(
    r"\\(?:math[a-zA-Z]+|frac|left|right|mathrm|mathbf|mathsf|mathtt|begin|end|color|definecolor"
    r"|text|operatorname|dots|ldots|cdots|vdots|ddots|quad|qquad"
    r"|hat|bar|tilde|vec|widehat|widetilde|overline|underline"
    r"|sum|prod|int|oint|min|max|arg|log|exp|sin|cos|tan|lim|inf|sup)\b(?:\{[^{}]*\})?",
    re.IGNORECASE,
)
_SUBSCRIPT_SUPERSCRIPT_RE = re.compile(r"[_^]\{[^{}]*\}")
# Lowercase math function notation: deg ( ), deg ( M ) — skeletons left after number removal
_MATH_FUNC_NOTATION_RE = re.compile(r"\b[a-z]{2,8}\s*\(\s*[A-Z]?\s*\)")
_COLOR_BLOCK_RE = re.compile(
    r"\{\\color[^{}]*\}|\\color\[[^\]]*\]\{[^{}]*\}|\\definecolor\[[^\]]*\]\{[^{}]*\}",
    re.IGNORECASE,
)
_STANDALONE_NUMBER_RE = re.compile(r"(?<!\w)\d+(?:\.\d+)?(?!\w)")
_ALNUM_TOKEN_RE = re.compile(r"(?<!\w)(?:\d+[a-zA-Z]+|[a-zA-Z]+\d+[a-zA-Z]*)(?!\w)")
_LATEX_ARTIFACT_TOKEN_RE = re.compile(
    r"\b(?:rgb|named|definecolor|pgfstrokecolor|color)\b", re.IGNORECASE
)
_DEF_FUNCTION_BLOCK_RE = re.compile(
    r"\bdef\s+[A-Za-z_]\w*\s*\([^\)]*\)\s*:.*?\breturn\b.*?(?=\bdef\s+[A-Za-z_]\w*\s*\([^\)]*\)\s*:|$)",
    re.IGNORECASE | re.DOTALL,
)
_COL_ASSIGNMENT_RE = re.compile(r"\bcol(?:[@$\\])?\s*=\s*[^\n]+", re.IGNORECASE)
_COL_SLICE_RE = re.compile(r"\bcol(?:[@$\\])?\s*\[[^\]]+\]", re.IGNORECASE)
_COL_MATH_MARKER_RE = re.compile(r"\bcol[@$\\]+", re.IGNORECASE)
_MULTI_SPACE_RE = re.compile(r"\s+")
_ACRONYM_DEFINITION_RE = re.compile(
    r"(?:[A-Za-z][a-z]*\s+(?:(?:and|or|of|the|a|for|in|with|to)\s+)*){2,}\([A-Z]{2,8}\)"
)
# Helper to extract the acronym string from a definition match
_ACRO_PARENS_RE = re.compile(r"\(([A-Z]{2,8})\)")

# PDF body trimming — start marker
# We use \b rather than $ because pdfplumber often merges the heading and the first
# sentence onto the same line (e.g. "1. Introduction We study...").
_PDF_INTRO_RE = re.compile(
    r"(?m)^[ \t]*(?:\d+\.?\s+)?introduction\b",
    re.IGNORECASE,
)

# Default end-matter headings for PDF trimming.
# Mirrors html_processor._REMOVE_HEADINGS_DEFAULT (minus Abstract, which is before
# Introduction and naturally excluded; Appendix is kept so post-body appendices are
# still scanned — consistent with the HTML processor not removing them either).
PDF_END_HEADINGS_DEFAULT: list[str] = [
    "References",
    "Bibliography",
    "Related Works",
    "Related Work",
    "Related work",
    "Literature Review",
    "Acknowledgements",
    "Acknowledgement",
    "Acknowledgments",
    "Acknowledgment",
    "Funding",
    "Ethics Statement",
    "Ethics",
]


def _build_pdf_end_re(headings: list[str]) -> re.Pattern:
    # Sort longest first so "Ethics Statement" matches before "Ethics", etc.
    alts = "|".join(re.escape(h) for h in sorted(headings, key=len, reverse=True))
    return re.compile(
        rf"(?m)^[ \t]*(?:\d+\.?\s+)?(?:{alts})\b",
        re.IGNORECASE,
    )


_PDF_END_RE = _build_pdf_end_re(PDF_END_HEADINGS_DEFAULT)


def _language_regex(language_name: str) -> str:
    parts = []
    for ch in language_name:
        if ch in _APOSTROPHE_VARIANTS:
            parts.append(r"[''ʼʻʹʽ`]")
        elif ch in _HYPHEN_VARIANTS:
            parts.append(r"[-‐‑‒–—−]")
        else:
            parts.append(re.escape(ch))
    return r"(?<!\w)" + "".join(parts) + r"(?!\w)"


@functools.lru_cache(maxsize=None)
def _compiled_pattern(language_name: str) -> re.Pattern:
    return re.compile(_language_regex(language_name), re.IGNORECASE)


def _matches_language_name(text: str, language_name: str) -> bool:
    return _compiled_pattern(language_name).search(text) is not None


# --- Acronym false-positive suppression ---

_LANGUAGE_SCREENING_WARNING_PATH = Path(__file__).parent.parent / "data/processed/language_screening_warnings.json"
_warning_lock = threading.Lock()


def _append_language_screening_warning(warning: dict, warning_path: Path) -> None:
    with _warning_lock:
        existing: list = []
        if warning_path.exists():
            try:
                with warning_path.open("r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing.append(warning)
        warning_path.parent.mkdir(parents=True, exist_ok=True)
        with warning_path.open("w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)


def _looks_like_capitalized_acronym_context(text: str, open_paren_index: int) -> bool:
    prefix = text[:open_paren_index].rstrip()
    fragment = re.split(r"[.;:!?\n]", prefix)[-1]
    words = re.findall(r"[A-Za-z][A-Za-z''\-]*", fragment)

    if len(words) < 2:
        if not (len(words) == 1 and re.search(r"[-‐‑‒–—−]", words[0])):
            return False

    titlecase_words = 0
    uppercase_words = 0
    for word in words[-8:]:
        if word.isupper() and len(word) > 1:
            uppercase_words += 1
            continue

        parts = re.split(r"[-‐‑‒–—−]", word)
        sub_titlecase = 0
        for p in parts:
            if len(p) == 0:
                continue
            if p[:1].isupper() and p[1:].islower():
                sub_titlecase += 1

        if sub_titlecase >= 2:
            titlecase_words += sub_titlecase
        elif sub_titlecase == 1 and len(parts) == 1:
            titlecase_words += 1

    return titlecase_words >= 2 or uppercase_words >= 2


def _initials_match_acronym(text: str, open_paren_index: int, acronym: str) -> bool:
    prefix = text[:open_paren_index].rstrip()
    fragment = re.split(r"[.;:!?\n]", prefix)[-1]
    words = re.findall(r"[A-Za-z][A-Za-z''\-]*", fragment)
    n = len(acronym)

    # Try titlecase words only (skips lowercase connectors like "and", "of")
    titlecase = [w for w in words if w[0].isupper()]
    if len(titlecase) >= n:
        if "".join(w[0].upper() for w in titlecase[-n:]) == acronym:
            return True

    # Try all words — handles connectors that contribute a letter (e.g. "of" → O in POI)
    if len(words) >= n:
        if "".join(w[0].upper() for w in words[-n:]) == acronym:
            return True

    return False


def should_ignore_acronym_language_match(
    text: str,
    language_name: str,
    paper_id: str | None = None,
    warning_path: Path = _LANGUAGE_SCREENING_WARNING_PATH,
) -> bool:
    if not text or not language_name:
        return False

    name = language_name.strip()
    if not (2 <= len(name) <= 10):
        return False
    acronym = name.upper()

    pattern = re.compile(r"\(\s*" + re.escape(acronym) + r"\s*\)")
    ignored = False
    for match in pattern.finditer(text):
        if (
            _looks_like_capitalized_acronym_context(text, match.start())
            and _initials_match_acronym(text, match.start(), acronym)
        ):
            ignored = True
            _append_language_screening_warning(
                {
                    "rule": "parenthetical_acronym_context",
                    "language": language_name,
                    "acronym": acronym,
                    "paper_id": paper_id,
                },
                warning_path,
            )

    return ignored


def clean_paper_text_for_language_screening(text: str) -> tuple[list[str], dict]:
    """Return (cleaned_blocks, step_matches) for language screening."""
    if not text:
        return [], {}

    normalized = _normalize_text_for_screening(text)
    cleaned_blocks: list[str] = []
    step_matches: dict = {}

    # Pass 1: collect all acronyms defined anywhere in the text (case-insensitive expansion).
    # This handles cross-paragraph uses: "Document Layout Analysis (DLA)" in paragraph 1
    # → bare "DLA" in paragraph 3 is also stripped.
    all_defined_acronyms: set[str] = {
        m2.group(1)
        for m in _ACRONYM_DEFINITION_RE.finditer(normalized)
        for m2 in [_ACRO_PARENS_RE.search(m.group(0))]
        if m2
    }

    for block in re.split(r"\n{2,}|\r\n{2,}", normalized):
        block = block.strip()
        if not block:
            continue

        step_matches = {}

        # Code/pseudocode cleanup
        matches = _DEF_FUNCTION_BLOCK_RE.findall(block)
        if matches:
            step_matches["def_return_funcs"] = matches[:5]
        block = _DEF_FUNCTION_BLOCK_RE.sub(
            lambda m: _COL_MATH_MARKER_RE.sub(
                " ", _COL_SLICE_RE.sub(" ", _COL_ASSIGNMENT_RE.sub(" ", m.group(0)))
            ),
            block,
        )

        matches = _COL_ASSIGNMENT_RE.findall(block)
        if matches:
            step_matches["col_assignment"] = matches
        block = _COL_ASSIGNMENT_RE.sub(" ", block)

        matches = _COL_SLICE_RE.findall(block)
        if matches:
            step_matches["col_slice"] = matches
        block = _COL_SLICE_RE.sub(" ", block)

        matches = _COL_MATH_MARKER_RE.findall(block)
        if matches:
            step_matches["col_math_marker"] = matches
        block = _COL_MATH_MARKER_RE.sub(" ", block)

        # LaTeX / HTML artifact cleanup
        matches = _COLOR_BLOCK_RE.findall(block)
        if matches:
            step_matches["color_blocks"] = matches
        block = _COLOR_BLOCK_RE.sub(" ", block)

        matches = _INLINE_MATH_RE.findall(block)
        if matches:
            step_matches["inline_math"] = matches
        block = _INLINE_MATH_RE.sub(" ", block)

        matches = _MATH_COMMAND_RE.findall(block)
        if matches:
            step_matches["math_commands"] = matches[:10]
        block = _MATH_COMMAND_RE.sub(" ", block)

        matches = _SUBSCRIPT_SUPERSCRIPT_RE.findall(block)
        if matches:
            step_matches["subscripts_superscripts"] = matches[:10]
        block = _SUBSCRIPT_SUPERSCRIPT_RE.sub(" ", block)

        # Citation cleanup
        matches = _CITATION_AUTHOR_YEAR_RE.findall(block)
        if matches:
            step_matches["citation_author_year"] = matches
        block = _CITATION_AUTHOR_YEAR_RE.sub(" ", block)

        matches = _CITATION_PAREN_RE.findall(block)
        if matches:
            step_matches["citation_parens"] = matches
        block = _CITATION_PAREN_RE.sub(" ", block)

        matches = _CITATION_BRACKET_RE.findall(block)
        if matches:
            step_matches["citation_brackets"] = matches
        block = _CITATION_BRACKET_RE.sub(" ", block)

        matches = _ALNUM_TOKEN_RE.findall(block)
        if matches:
            step_matches["alnum_tokens"] = matches
        block = _ALNUM_TOKEN_RE.sub(" ", block)

        matches = _STANDALONE_NUMBER_RE.findall(block)
        if matches:
            step_matches["standalone_numbers"] = matches[:20]
        block = _STANDALONE_NUMBER_RE.sub(" ", block)

        matches = _LATEX_ARTIFACT_TOKEN_RE.findall(block)
        if matches:
            step_matches["latex_artifacts"] = matches
        block = _LATEX_ARTIFACT_TOKEN_RE.sub(" ", block)

        matches = _MATH_FUNC_NOTATION_RE.findall(block)
        if matches:
            step_matches["math_func_notation"] = matches
        block = _MATH_FUNC_NOTATION_RE.sub(" ", block)

        # Remove acronym introductions (e.g. "Mean Absolute Error (MAE)" → "Mean Absolute Error")
        # then strip all standalone uses of any acronym defined anywhere in the text.
        if all_defined_acronyms:
            step_matches["acronym_catch"] = list(all_defined_acronyms)
        block = _ACRONYM_DEFINITION_RE.sub(
            lambda m: re.sub(r"\s*\([A-Z]{2,8}\)", " ", m.group(0)),
            block,
        )
        for _acro in all_defined_acronyms:
            block = re.sub(r"(?<!\w)" + re.escape(_acro) + r"(?!\w)", " ", block)

        out_chars: list[str] = []
        replaced: list[str] = []
        for ch in block:
            if _should_keep_language_char(ch):
                out_chars.append(ch)
            else:
                out_chars.append(" ")
                replaced.append(ch)
        block = "".join(out_chars)
        if replaced:
            step_matches["replaced_chars"] = Counter(replaced)

        block = _MULTI_SPACE_RE.sub(" ", block).strip()
        if block:
            cleaned_blocks.append(block)

    return cleaned_blocks, step_matches


def detect_languages_in_text(
    text_list: list[str],
    lang_classes: dict[int, set[str]],
    languages_to_ignore: set[str],
    paper_id: str | None = None,
) -> list[str]:
    """Detect language name mentions across a list of cleaned text blocks."""
    language_occurrences: list[str] = []

    # Precompute once — not inside the inner loop
    ignore_lower = {v.lower() for v in languages_to_ignore}
    language_groups = lang_classes.values() if isinstance(lang_classes, dict) else lang_classes

    for text in text_list:
        if not isinstance(text, str):
            continue
        for langs in language_groups:
            candidate_languages = [langs] if isinstance(langs, str) else langs
            for lang in candidate_languages:
                if not isinstance(lang, str):
                    continue
                if lang in languages_to_ignore or lang.lower() in ignore_lower:
                    continue
                if _matches_language_name(text, lang):
                    if not should_ignore_acronym_language_match(text, lang, paper_id=paper_id):
                        language_occurrences.append(lang)

    return language_occurrences


def trim_pdf_text_to_body(
    text: str,
    end_headings: list[str] | None = None,
) -> str:
    """Trim PDF-extracted text to the body of the paper.

    Skips everything before the Introduction (title, authors, abstract — the
    abstract is already scanned from the arXiv API metadata) and stops before
    References / Bibliography / Related Work / Acknowledgements / Funding / Ethics
    (matching the same set of headings removed by the HTML processor).
    Appendix sections are kept, consistent with the HTML processor.

    Pass ``end_headings`` to override the default heading list.
    Returns the original text unchanged if neither marker is found.
    """
    if not text:
        return text

    end_re = _build_pdf_end_re(end_headings) if end_headings is not None else _PDF_END_RE

    start = 0
    intro_match = _PDF_INTRO_RE.search(text)
    if intro_match:
        start = intro_match.start()

    end = len(text)
    end_match = end_re.search(text, start)
    if end_match:
        end = end_match.start()

    trimmed = text[start:end].strip()
    return trimmed if trimmed else text
