#!/usr/bin/env bash
# Build the portfolio-evidence PDFs from their Markdown sources.
# Needs pandoc (>= 2.9) and xelatex. Run from anywhere:
#   bash scripts/build_evidence_pdfs.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/docs/experiments"
OUT="$SRC/pdf"
mkdir -p "$OUT"

for doc in experimental-design-and-evaluation-framework first-llm-pilot-experiments; do
  pandoc "$SRC/$doc.md" \
    --resource-path="$SRC" \
    --lua-filter="$ROOT/scripts/pdf/table-widths.lua" \
    --include-in-header="$ROOT/scripts/pdf/evidence-pdf.tex" \
    --pdf-engine=xelatex \
    --toc --toc-depth=2 \
    -V mainfont="DejaVu Sans" -V monofont="DejaVu Sans Mono" -V fontsize=10pt \
    -V geometry:a4paper -V geometry:margin=2.1cm \
    -V colorlinks=true -V linkcolor=navy -V urlcolor=navy -V toccolor=black \
    -o "$OUT/$doc.pdf"
  echo "built docs/experiments/pdf/$doc.pdf"
done
