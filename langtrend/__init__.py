"""LangTrend pipeline utilities."""

from importlib import import_module

# Re-exports resolve lazily (PEP 562), so importing a light submodule such as
# langtrend.digest doesn't drag in html_processor/pdf_processor and their
# third-party dependencies (requests, bs4, tqdm). The weekly email digest step
# in CI relies on this to run with the standard library only.
_EXPORTS = {
    "extract_sections_from_html": ".html_processor",
    "recheck_languages_from_html": ".html_processor",
    "build_snapshot_manifest": ".manifest",
    "save_json": ".manifest",
    "PDFProcessor": ".pdf_processor",
    "clean_paper_text_for_language_screening": ".text_cleaning",
    "detect_languages_in_text": ".text_cleaning",
    "replace_non_letters_with_spaces": ".text_cleaning",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name in _EXPORTS:
        return getattr(import_module(_EXPORTS[name], __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
