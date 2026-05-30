# LangTrend

**LangTrend** is a weekly pipeline that scans arXiv `cs.CL` submissions, detects explicitly mentioned human languages in the text, and publishes an interactive dashboard tracking language representation over time.

Live site: [kavindu-w.github.io/langtrend](https://kavindu-w.github.io/langtrend)

---

## Features

- **Full-text detection, not just abstracts** —  Papers are scanned section-by-section through their arXiv HTML versions, while a PDF fallback is used for those without HTML versions, and, as a final fallback, the paper’s abstract is utilized. This approach ensures that language mentions in the Experiments or Data sections are captured, not just those appearing in the abstract. Additionally, the pipeline excludes references and acknowledgements, thereby avoiding the counting of papers that only mention languages in related works but do not actually investigate them in the study.

- **6-tier language resource classification** — Every detected language is mapped to expanded Joshi et al.'s [3] resource-availability taxonomy (Classes 0: lowest, 5: highest resourced) from the work by Ranathunga and de Silva [2], making it easy to see whether a paper engages with dominant languages or low-resource ones.

- **Transparent false-positive handling** — Short language names that overlap with common acronyms or technical terms are flagged with the reason (for example, “very common ML acronym”) instead of being silently suppressed or blindly included. Each flagged detection also links to the original paper for manual verification, ensuring transparency and traceability.

- **Acronym conflict detection** — When a paper defines an acronym that shares its name with a language (such as defining “GAN” in a paper that also mentions the Gan language), the detection is suppressed. In such cases, a warning appears in the dashboard, so users can check the paper themselves. Furthermore, the pipeline performs thorough text cleaning, such as removing mathematical artifacts, to minimize false positives without suppressing valid detections.

- **Weekly automated updates** — A GitHub Actions workflow runs every Tuesday, covering the previous week’s cs.CL submissions. Historical snapshots are preserved week over week.

- **Open data** — The manifest JSON for each week is committed to the repository. You can download and analyse the raw detection data without running the pipeline.

---

## Pipeline

![LangTrend pipeline diagram](web/public/images/langtrend-pipeline.png)

The pipeline is cache-aware at every step: existing JSONL, `html_cache/`, and `pdf_cache/` files are reused unless explicitly cleared.

---

## Language Classes

Languages class descriptions are adapted from the resource-availability taxonomy from Joshi et al. [3]. Counts and example languages are updated from the <a
          href="https://github.com/NisansaDdS/Some-Languages-are-More-Equal-than-Others"
          target="_blank"
          rel="noreferrer"><u>Some Languages are More Equal than Others</u></a
        > repository, which follows the work from <a
          href="https://doi.org/10.18653/v1/2022.aacl-main.62"
          target="_blank"
          rel="noreferrer"><u>Ranathunga and de Silva [2]</u></a
        >.
| Class | Description | Languages tracked | Examples |
|-------|-------------|:-----------------:|---------|
| 0 | Exceptionally limited resources; rarely considered in language technologies | 6,134 | Pinyin, Irarutu, Himarimã |
| 1 | Some unlabelled data; collecting labelled data is challenging | 130 | Hawaiian, Frisian, Nahuatl |
| 2 | Small set of labelled datasets; active language support communities | 96 | Sinhala, Irish, Zulu |
| 3 | Strong web presence and cultural community; highly benefited by unsupervised pre-training | 30 | Hindi, Tamil, Urdu |
| 4 | Large unlabelled data and significant labelled data; dedicated NLP research communities | 22 | Indonesian, Russian, Italian |
| 5 | Dominant online presence; massive investment in resources and technologies | 7 | Arabic, Chinese, English |

---

## Limitations

- **Detection coverage** — Language detection is based on explicit mentions in titles, abstracts, section text, and cleaned PDF body text. As a result, indirect references, such as citing a multilingual dataset without naming the languages, may be missed. Further, the pipeline may produce false positives, since shorter language names can appear as acronyms, author names, or technical terms. Although text cleaning and acronym filtering reduce these occurrences, users should always verify flagged languages directly in the paper.
- **Extraction fallback** — While HTML extraction is preferred for accuracy, the pipeline depends on the availability of arXiv HTML pages. If the HTML version is missing or incomplete, the pipeline instead falls back to the PDF (via Docling). Should the PDF also be unavailable or withdrawn, only the abstract text will be analysed.
- **cs.CL scope only** — The pipeline covers only papers submitted to the <code>cs.CL</code> arXiv category (It may include multiple categories, which include <code>cs.CL</code>). Multilingual NLP papers appearing in adjacent categories (cs.AI, cs.LG, cs.CV, etc.), which exclude <code>cs.CL</code> are not captured.
- **No paper version tracking** — Papers are processed at fetch time. If an author updates a paper with new or removed language mentions, these are not reflected unless the pipeline re-runs for the same date window.
- **Weekly cadence** —  The pipeline runs once a week (every Tuesday), covering the previous week’s arXiv announcement window. As a result, papers that fall across the date boundary may be missed in a given snapshot.

---

## Setup

### Dependencies

Python version: 3.13.3 (see `.python-version`)

```bash
make setup
```

### Submodule

The language classification data is maintained in a submodule:

```bash
git submodule update --init --recursive
```

To pull the latest classification updates:

```bash
git submodule update --remote
```

---

## Local usage

Run the full data pipeline and build the site:

```bash
make build
```

Start the dev server:

```bash
make dev
```

Individual pipeline steps:

```bash
make fetch            # Step 1 — fetch arXiv metadata (skips if JSONL exists)
make fetch-oai        # Step 1 — force OAI-PMH harvester instead of arXiv API
make process          # Step 2 — extract text and detect languages
make reprocess        # Step 2 — re-run detection on cached text only (no downloads)
make retry-missing    # Step 2 — fill gaps from a previous run (downloads missing PDFs)
make manifest         # Step 3 — rebuild manifest JSON from cached results
make pipeline         # Steps 1–3 — full data run
```

To target a specific week, set `END_DATE`:

```bash
make process END_DATE=2026-05-18
```

**Multi-week targets** loop over a space-separated `DATES` list:

```bash
make fetch-all        # fetch for each date in DATES
make process-all      # process for each date in DATES
make reprocess-all    # reprocess cached text for each date in DATES
make retry-missing-all # fill gaps for each date in DATES
make manifest-all     # rebuild manifests for every week found in the metadata dir
make pipeline-all     # full pipeline for each date in DATES
```

**Parallel multi-week workflow** (run detection across multiple terminals simultaneously):

```bash
# Terminal 1
make process NO_PDF=1 END_DATE=2026-05-04
# Terminal 2
make process NO_PDF=1 END_DATE=2026-05-11
# Once both finish, fill in any papers that needed PDF fallback
make retry-missing-all DATES="2026-05-04 2026-05-11"
```

`NO_PDF=1` skips the docling PDF step so multiple terminals don't compete for the same GPU/CPU resources. `retry-missing` then handles any papers that needed PDF and were skipped.

---

## Repository Structure

```text
Code/
├── langtrend/                           Python package with the core pipeline logic
├── scripts/                             Command-line entry points for fetch/process/build steps
├── web/                                 Astro frontend for the public dashboard
├── data/
│   ├── raw/                             Paper metadata
│   └── processed/                       Generated manifests, detections, and summary tables
├── tests/                               Automated tests for pipeline helpers and processing code
├── notebooks/                           Exploratory notebooks for analysis and chart development
└── Some-Languages-are-More-Equal-than-Others/  Submodule with language-class taxonomy and counts
```

## GitHub Actions

The workflow at `.github/workflows/langtrend.yml` runs automatically every **Tuesday at 12:00 UTC** (after arXiv's Monday announcement window closes). It can also be triggered manually or on push to the main branch.

Steps executed by the workflow:

1. Fetch arXiv `cs.CL` papers for the past 7 days
2. Extract and clean text (HTML → PDF → abstract fallback)
3. Detect language mentions and flag acronym conflicts
4. Build the manifest JSON
5. Build the Astro site
6. Deploy to GitHub Pages

---

## References

1. S. Ranathunga, N. De Silva, D. Jayakody, and A. Fernando, "Shoulders of Giants: A Look at the Degree and Utility of Openness in NLP Research," in *Proc. 62nd Annual Meeting of the Association for Computational Linguistics (Volume 2: Short Papers)*, Bangkok, Thailand, Aug. 2024, pp. 519–529. doi: [10.18653/v1/2024.acl-short.48](https://doi.org/10.18653/v1/2024.acl-short.48)

2. S. Ranathunga and N. de Silva, "Some Languages are More Equal than Others: Probing Deeper into the Linguistic Disparity in the NLP World," in *Proc. 2nd Conference of the Asia-Pacific Chapter of the ACL and the 12th IJCNLP (Volume 1: Long Papers)*, Online, Nov. 2022, pp. 823–848. doi: [10.18653/v1/2022.aacl-main.62](https://doi.org/10.18653/v1/2022.aacl-main.62)

3. P. Joshi, S. Santy, A. Budhiraja, K. Bali, and M. Choudhury, "The State and Fate of Linguistic Diversity and Inclusion in the NLP World," in *Proc. 58th Annual Meeting of the Association for Computational Linguistics*, Online, Jul. 2020, pp. 6282–6293. doi: [10.18653/v1/2020.acl-main.560](https://doi.org/10.18653/v1/2020.acl-main.560)

4. C. Auer et al., "Docling Technical Report," 2024, arXiv. doi: [10.48550/ARXIV.2408.09869](https://doi.org/10.48550/ARXIV.2408.09869)

---

## Acknowledgements

This project builds on the ideas, code, and data from [2] and [4], with assistance from GitHub Copilot and Claude. I thank Prof. Kan Min-Yen for providing both the initial idea and valuable resources, and Dr. Surangika Ranathunga and Dr. Nisansa de Silva for their ideas and code.
Thank you to arXiv for providing open access interoperability.

---
## License

The code and data are released under the [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). By using this code and data, you are agreeing to its usage terms.