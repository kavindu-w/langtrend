"""
Model-swap acceptance test for the LLM judge stage (langtrend/judge.py).

Hits a REAL LLM endpoint using whatever LLM_JUDGE_* env vars are configured
(base URL, model, key) — costs quota/tokens and needs network, so it is
NOT part of the default suite. Opt in explicitly:

    RUN_JUDGE_MODEL_EVAL=1 pytest tests/test_judge_model_eval.py -v

Run this whenever the current judge provider/model is going away (quota
sunset, deprecation) and you're evaluating a replacement: point
LLM_JUDGE_BASE_URL/LLM_JUDGE_MODEL/LLM_JUDGE_API_KEY at the candidate and
see whether it still gets these cases right before flipping the real
config over. Every case below is hand-written and synthetic — no text from
any real arXiv paper — so this file is safe to keep in a public repo.

Coverage is deliberately aimed at the failure modes JUDGE_SYSTEM_PROMPT
calls out explicitly: tool/model names, acronym backronyms, script vs.
language, proper nouns, and benchmark names that share a language's name
without the text being about that language — plus the two genuine
verdicts (studied vs. mentioned_only) a competent replacement model must
still tell apart.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langtrend.judge import JudgeContext, Snippet, build_messages, validate_verdicts
from langtrend.llm_client import LLMClientConfig, OpenAICompatClient, extract_json

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_JUDGE_MODEL_EVAL") != "1",
    reason=(
        "Hits a live LLM endpoint and spends real quota — opt in with "
        "RUN_JUDGE_MODEL_EVAL=1 when evaluating a new judge model/provider."
    ),
)

CASES = [
    dict(
        id="studied-dataset",
        language="Yoruba", cls=2,
        title="Building Annotated Resources for a Low-Resource West African Language",
        abstract="We introduce a new named entity recognition corpus and evaluate transformer models fine-tuned on it.",
        section="Data Collection",
        snippet=(
            "We collect 50,000 sentences of Yoruba text from local news outlets and "
            "annotate them for named entities. We then fine-tune a multilingual BERT "
            "model on this Yoruba corpus and report F1 scores across five entity types."
        ),
        expected="studied",
        note="paper builds a dataset for and evaluates models on the language itself",
    ),
    dict(
        id="studied-mt",
        language="Quechua", cls=2,
        title="Neural Machine Translation for Andean Languages",
        abstract="We train and evaluate translation systems for several under-resourced languages.",
        section="Experiments",
        snippet=(
            "Our Quechua-Spanish translation model is trained on a parallel corpus of "
            "12,000 sentence pairs. We report BLEU scores of 18.4 on the held-out "
            "Quechua test set, outperforming the baseline by 4 points."
        ),
        expected="studied",
        note="model trained/evaluated specifically for this language",
    ),
    dict(
        id="studied-vs-baseline",
        language="Xhosa", cls=2,
        title="Adapting Multilingual Models to a Low-Resource Bantu Language",
        abstract="We adapt a multilingual language model to Xhosa and compare against high-resource baselines.",
        section="Experiments",
        snippet=(
            "We continue pretraining on 40M tokens of Xhosa text and fine-tune on a "
            "downstream sentiment classification task, achieving 81% accuracy on our "
            "Xhosa test set."
        ),
        expected="studied",
        note="clear target-language experiments, phrased alongside an (unshown) baseline comparison",
    ),
    dict(
        id="mentioned-related-work",
        language="Amharic", cls=2,
        title="Cross-Lingual Transfer for Text Classification",
        abstract="We study cross-lingual transfer of English-trained classifiers to five target languages.",
        section="Related Work",
        snippet=(
            "Prior work has explored low-resource transfer to languages such as Amharic "
            "(Roe et al., 2021) and Tigrigna (Doe et al., 2020), though these approaches "
            "require language-specific tokenizers that our method avoids."
        ),
        expected="mentioned_only",
        note="cited as related work, not part of this paper's own experiments",
    ),
    dict(
        id="mentioned-future-work",
        language="Tagalog", cls=2,
        title="Scalable Text Classification for Southeast Asian Languages",
        abstract="We present a classification pipeline evaluated on Indonesian and Vietnamese text.",
        section="Conclusion",
        snippet="Extending our pipeline to additional languages such as Tagalog is left to future work.",
        expected="mentioned_only",
        note="explicitly future work / not studied; thin evidence should not become false_positive",
    ),
    dict(
        id="fp-tool-name",
        language="Jina", cls=0,
        title="Efficient Passage Retrieval for Open-Domain Question Answering",
        abstract="We propose a retrieval pipeline combining dense embeddings with a lightweight reranker.",
        section="Method",
        snippet=(
            "We encode all passages using the Jina embedding model "
            "(jina-embeddings-v2-base-en) before indexing them with FAISS for "
            "approximate nearest-neighbor search."
        ),
        expected="false_positive",
        note="'Jina' here names an embedding model, not the Jina language",
    ),
    dict(
        id="fp-acronym-backronym",
        language="Basque", cls=4,
        title="A Batch-Adaptive Framework for Query Understanding",
        abstract="We introduce a system for adaptive query rewriting in enterprise search.",
        section="System Overview",
        snippet=(
            "Our Batch-Adaptive System for Query Understanding and Search Efficiency "
            "(BASQUE) rewrites queries in real time using a lightweight scoring model "
            "trained on click logs."
        ),
        expected="false_positive",
        note="BASQUE is a backronym for the system name, unrelated to the Basque language",
    ),
    dict(
        id="fp-script-not-language",
        language="Latin", cls=3,
        title="Robust Tokenization for Multilingual Web Text",
        abstract="We study tokenization strategies for noisy multilingual web corpora.",
        section="Preprocessing",
        snippet=(
            "All input text is first transliterated into the Latin script before "
            "tokenization, regardless of the source language, to normalize "
            "character-level variation across scripts."
        ),
        expected="false_positive",
        note="'Latin' refers to the Latin alphabet/script here, not the Latin language",
    ),
    dict(
        id="fp-proper-noun",
        language="Irish", cls=2,
        title="Crowd-Sourced Data Collection for Speech Recognition",
        abstract="We describe a crowd-sourcing pipeline for collecting speech data in New York City.",
        section="Data Collection",
        snippet=(
            "We partnered with a local venue, The Irish Pub, to recruit volunteer "
            "speakers for our English-language speech recording sessions over several "
            "weekends."
        ),
        expected="false_positive",
        note="'Irish' names a venue here, not the Irish language, and the recordings are English",
    ),
    dict(
        id="fp-benchmark-name",
        language="Spanish", cls=5,
        title="Synthetic Benchmarks for Question Answering Evaluation",
        abstract="We construct several synthetically named benchmarks to stress-test QA systems.",
        section="Benchmarks",
        snippet=(
            "Despite its name, the SPANISH-QA benchmark consists entirely of "
            "English-language question-answer pairs generated by a template-based "
            "synthetic pipeline; the name refers only to the internal project codename."
        ),
        expected="false_positive",
        note="benchmark name coincidentally matches a language name; content is English",
    ),
]


def _run_case(client: OpenAICompatClient, case: dict) -> tuple[str, str]:
    context = JudgeContext(
        head=f"TITLE: {case['title']}\n\nABSTRACT: {case['abstract']}",
        snippets=[
            Snippet(
                section=case["section"],
                start=0,
                end=len(case["snippet"]),
                text=case["snippet"],
                languages=[case["language"]],
                sections=[case["section"]],
            )
        ],
        coverage="abstract+html",
    )
    targets = [{"language": case["language"], "class": case["cls"], "sections": [case["section"]]}]
    messages = build_messages(context, targets)
    reply = client.chat(messages)
    parsed = extract_json(reply)
    verdicts = validate_verdicts(parsed, targets)
    result = verdicts.get(case["language"], {})
    return result.get("verdict", ""), result.get("reason", "")


class TestJudgeModelEval:
    """Ground-truth acceptance suite: point LLM_JUDGE_* at any candidate model/provider and run this."""

    @pytest.fixture(scope="class")
    def client(self):
        config = LLMClientConfig.from_env()
        client = OpenAICompatClient(config)
        client.ping()
        return client

    @pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
    def test_case(self, client, case):
        predicted, reason = _run_case(client, case)
        assert predicted == case["expected"], (
            f"{case['id']}: expected {case['expected']!r}, got {predicted!r} "
            f"(model reason: {reason!r}; why we expect this: {case['note']})"
        )
