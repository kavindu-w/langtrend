# LangTrend: A repository for analyzing language inclusion in research papers

## Setup Instructions

## Dependencies

Install Python dependencies using:

```bash
pip install -r requirements.txt
```

Python version: 3.13.3 (see `.python-version`)

## GitHub Actions

The repo includes a private-safe workflow at `.github/workflows/langtrend.yml`.
It runs the snapshot pipeline, builds the Astro site, and uploads artifacts only;
it does not publish the site publicly.

## One-command local run

From the repo root:

```bash
make dev
```

To regenerate data and build the site:

```bash
make build
```


### Submodules

This repository includes the following submodule:

```bash
git submodule add https://github.com/NisansaDdS/Some-Languages-are-More-Equal-than-Others.git
```

To update submodules to their latest versions:

```bash
git submodule update --remote
```

