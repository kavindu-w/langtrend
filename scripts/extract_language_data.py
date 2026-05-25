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
        "lang_classes": {str(k): list(v) for k, v in lang_classes.items()},
        "languages_to_ignore": sorted(_LANGUAGES_TO_IGNORE),
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
