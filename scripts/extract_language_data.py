#!/usr/bin/env python3
"""
Extract language data from the submodule and save to data/processed/language_data.json.

Usage:
    python scripts/extract_language_data.py
    python scripts/extract_language_data.py --submodule-path /path/to/submodule
    python scripts/extract_language_data.py --output data/processed/language_data.json
"""

import argparse
import json
from pathlib import Path

_DEFAULT_SUBMODULE = Path(__file__).parent.parent / "Some-Languages-are-More-Equal-than-Others"
_DEFAULT_OUTPUT = Path(__file__).parent.parent / "data/processed/language_data.json"

# Languages that are real but frequently produce false positives in CS/NLP papers.
# Detections for these will be kept but flagged with needs_review=True and a reason
# so they can be surfaced for manual inspection on the website.
_POSSIBLE_FALSE_POSITIVE_LANGUAGES: dict[str, str] = {
    # Acronyms commonly used in CS/NLP/ML papers
    "Agi":      "AGI = Artificial General Intelligence — very common acronym in CS/NLP papers",
    "Aka":      "'aka' = also known as — common abbreviation",
    "Ami":      "AMI = meeting corpus dataset; also a real Formosan language of Taiwan",
    "Ari":      "ARI = Adjusted Rand Index — common clustering evaluation metric acronym",
    "Bo":       "BO = Bayesian Optimization (GP-BO); also could be a common given name appearing in author lists",
    "Carrier":  "English technical term (carrier signal, carrier edge, carrier layer) in CS/EE papers",
    "Cora":     "CORA = citation network benchmark dataset — common in GNN/graph learning papers",
    "Dass":     "DASS = Depression Anxiety Stress Scales (psychology tool acronym)",
    "Dla":      "DLA = Document Layout Analysis — common NLP/document understanding acronym",
    "Ega":      "EGA = Energy Gate Attention — method name acronym in vision/NLP papers",
    "Elu":      "ELU = Ease of Language Understanding model or activation function acronym",
    "Gan":      "GAN = Generative Adversarial Network — very common deep learning acronym",
    "Gofa":     "GOFA = GNN-ODE with Feature Alignment — GNN method name acronym",
    "Jina":     "Possible embedding model name",
    "Kwa":      "Often matches RL/ML technical terms",
    "Lave":     "LAVE = visual evaluation metric in VQA papers; also a Mon-Khmer language",
    "Leco":     "LeCo = uncertainty estimation method acronym in ML papers",
    "Lipo":     "Lipophilicity = chemistry benchmark dataset; Lipo abbreviation in drug discovery papers",
    "Mae":      "MAE = Mean Absolute Error — very common ML evaluation metric acronym",
    "Mand":     "Abbreviation for Mandarin (Mand↔Min = Mandarin↔Minority) in translation papers",
    "Mape":     "MAPE = Mean Absolute Percentage Error (evaluation metric acronym)",
    "Mbe":      "MBE = Model-Based Engineering or Mean Bias Error — common technical acronym",
    "Sake":     "English word 'sake' in 'for the sake of' — common academic phrase, not a language",
    "Sapo":     "SAPO = RL optimization algorithm name acronym",
    "Sentinel": "sentinel token = standard NLP/ML term for mask/placeholder tokens",
    "Seri":     "SERI-DST = dialogue state tracking system acronym; also a real language isolate of Mexico",
    "Toto":     "Toto-Base = time-series forecasting model name; also possible false positive",
    "Uri":      "URI = Uniform Resource Identifier — standard web/CS term",
    # Math subscript rendering artifacts in arXiv HTML
    "Aja":      "Math subscript artifact: a_j (IRT parameter) rendered as 'aja' in arXiv HTML",
    "Ata":      "Math subscript artifact: a_t^a rendered as 'ata' in arXiv HTML",
    "Tat":      "Math subscript artifact: T_a rendered as 'TaT' in arXiv HTML",
    "Utu":      "Math subscript artifact: U_t rendered as 'UtU' in arXiv HTML",
    # Multi-column PDF layout / space-loss artifacts
    "Andh":     "PDF space-loss artifact: 'and h' (math variable) merged as 'andh' in multi-column PDFs",
    "Fwe":      "FWE = Family-Wise Error — statistics acronym common in neuroscience/NLP papers",
    "Maa":      "PDF artifact: 'maa.org' URL domain or 'maa' token from multi-column text merge",
    "Mpi":      "MPI = Max Planck Institute or Message Passing Interface — common acronym in NLP/ML papers",
    "Nai":      "PDF split artifact: 'nAI' suffix from 'OpenAI' split at column boundary",
    "Ona":      "PDF space-loss artifact: 'on a' merged as 'ona' in multi-column layout",
    # Short/generic words that match many common terms
    "Batu":     "Possible false positive",
    "Crow":     "Often matches bird keyword lists in corpus studies",
    "Deg":      "Likely matches 'degradation' or similar technical terms",
    "Epie":     "Likely matches part of a technical word (e.g. 'epitome', 'epigenetic')",
    "Fur":      "Likely means hair or matches 'further', 'furniture', etc.",
    "Ik":       "Possible false positive",
    "Mo":       "Too short — matches many common words and abbreviations",
    "Pin":      "Too short/generic — matches many common words",
    "Pole":     "Often matches mathematical 'poles' in geometry/embeddings papers",
    "Tumi":     "Bangla pronoun 'tumi' (informal 'you') — common in Bangla NLP papers",
    "Wa":       "Too short — matches many common words and abbreviations",
    "Yale":     "Possible false positive — Yale University",
}

# taken from https://github.com/dilithjay/Shoulders-of-Giants/blob/main/categorize_filtered_papers.ipynb
_LANGUAGES_TO_IGNORE = set(
    "Apache,Laura,Fang,Mono,Ma,Maria,Sam,Bench,Zhuang,Male,Nara,So,Hu,Kim,Label,The,To,Yong,"
    "Adele,Are,Foma,Kaur,Bau,Kato,Dek,Naman,Dom,As,Dan,E,U,Even,En,"
    "Chung,Dong,Shi,Tai,Thompson,Gao,Ir,Pan,Ali,Rao,Han,Doe,Titan,Ha,Sa,Tu,Lau,Siri,Wan,She,"
    "Dai,Ding,Kang,Ge,Koch,Che,Mann,Zou,Pei,Yao,Lou,Sydney,Ju,Sha,Day,Miwa,Bai,Ko,Ga,Pal,Pe,"
    "Gun,Hung,Con,Cun,Serrano,Sui,Bu,Mehri,Od,Haji,Gal,Gey,Lui,Ho,Furu,Ak,Kao,Aro,Gen,Moro,"
    "Notre,Ido,Ron,Were,Sahu,Dem,Melo,Rama,Hunde,Dii,Yala,Sauri,"
    "Uni,One,Yi,Na,Bit,Pa".split(",")
    + ["are", "as", "e", "en", "even", "one", "so", "to", "apache", "au", "u", "bit", "she", "siri", "day", "gun", "label", "notre"]
)


def extract_language_data(submodule_path: Path, output_path: Path) -> None:
    base = submodule_path / "Language_List/Language_Classes_According_To/DataSet_Availability"

    if not base.exists():
        raise FileNotFoundError(
            f"Submodule data directory not found: {base}\n"
            "Make sure the submodule is initialised: git submodule update --init"
        )

    lang_classes: dict[int, set[str]] = {}
    for i in range(6):
        file_path = base / f"{i}.txt"
        try:
            lang_classes[i] = set(file_path.read_text(encoding="utf-8").strip().split("\n"))
            print(f"  Loaded class {i}: {len(lang_classes[i])} languages")
        except FileNotFoundError:
            print(f"  Warning: missing class file {file_path}")

    if not lang_classes:
        raise RuntimeError("No language class files were loaded.")

    total_unique = len(set.union(*lang_classes.values()))
    print(f"Total unique languages across all classes: {total_unique}")
    print(f"Languages to ignore: {len(_LANGUAGES_TO_IGNORE)}")

    output_data = {
        "lang_classes": {str(k): sorted(v) for k, v in lang_classes.items()},
        "languages_to_ignore": sorted(_LANGUAGES_TO_IGNORE),
        "possible_false_positive_languages": _POSSIBLE_FALSE_POSITIVE_LANGUAGES,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_data, ensure_ascii=False, indent=4), encoding="utf-8")
    print(f"Saved language data to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract language data from submodule")
    parser.add_argument(
        "--submodule-path",
        type=Path,
        default=_DEFAULT_SUBMODULE,
        help=f"Path to submodule directory (default: {_DEFAULT_SUBMODULE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"Output path for language_data.json (default: {_DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()
    extract_language_data(args.submodule_path, args.output)


if __name__ == "__main__":
    main()
