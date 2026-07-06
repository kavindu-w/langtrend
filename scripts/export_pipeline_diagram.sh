#!/usr/bin/env bash
# Re-exports docs/diagrams/langtrend_pipeline.drawio to web/public/images/langtrend-pipeline.svg.
#
# Requires the draw.io desktop CLI (`brew install --cask drawio`, exposes `drawio` on PATH).
#
# drawio's SVG export always leaves the canvas background transparent (even with
# --svg-theme light, which only forces light colors for the shapes/text) and GitHub's
# dark README theme lets that transparency show through as a bare dark page, so this
# also patches in an explicit opaque white backing rect after export.
set -euo pipefail

cd "$(dirname "$0")/.."

SRC="docs/diagrams/langtrend_pipeline.drawio"
OUT="web/public/images/langtrend-pipeline.svg"

drawio --export --format svg --svg-theme light --output "$OUT" "$SRC"

python3 - "$OUT" <<'PYEOF'
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace(
    'style="background: transparent; background-color: transparent; color-scheme: light;"',
    'style="background: #ffffff; background-color: #ffffff; color-scheme: light;"',
    1,
)
content = content.replace(
    "<defs/><g>",
    '<defs/><rect x="0" y="0" width="100%" height="100%" fill="#ffffff"/><g>',
    1,
)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
PYEOF

echo "Exported $OUT"
