from __future__ import annotations

import functools
import re
import unicodedata
from collections import Counter

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
    r"\b\w+\s+et\s+al\.?\s*\(\s*(?:19|20)\d{2}[a-z]?\s*\)",
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
    r"\\(?:math[a-zA-Z]+|frac|left|right|mathrm|mathbf|mathsf|mathtt|begin|end|color|definecolor)\b(?:\{[^{}]*\})?",
    re.IGNORECASE,
)
_COLOR_BLOCK_RE = re.compile(
    r"\{\\color[^{}]*\}|\\color\[[^\]]*\]\{[^{}]*\}|\\definecolor\[[^\]]*\]\{[^{}]*\}",
    re.IGNORECASE,
)
_STANDALONE_NUMBER_RE = re.compile(r"(?<!\w)\d+(?:\.\d+)?(?!\w)")
_ALNUM_TOKEN_RE = re.compile(r"(?<!\w)(?:\d+[a-zA-Z]+|[a-zA-Z]+\d+)(?!\w)")
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


def clean_paper_text_for_language_screening(text: str) -> tuple[list[str], dict]:
    """Return (cleaned_blocks, step_matches) for language screening."""
    if not text:
        return [], {}

    normalized = _normalize_text_for_screening(text)
    cleaned_blocks: list[str] = []
    step_matches: dict = {}

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
                    language_occurrences.append(lang)

    return language_occurrences
