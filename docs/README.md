# docs

Project documentation assets that don't belong in the root README or the website.

```text
docs/
└── diagrams/
    └── langtrend_pipeline.drawio   Editable source for the pipeline diagram
```

| Path | Contents |
|------|----------|
| `diagrams/langtrend_pipeline.drawio` | Editable [draw.io](https://app.diagrams.net/) source for the pipeline diagram shown in the root README and the site's About page (`web/public/images/langtrend-pipeline.svg`). Edit the `.drawio` file, then run `scripts/export_pipeline_diagram.sh` to re-export the SVG when the pipeline changes. |
