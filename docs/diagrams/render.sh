#!/usr/bin/env bash
# render one or all diagrams: SVG (for docs) and PNG at 2x (for the thesis)
set -e
cd "$(dirname "$0")"
for f in ${@:-src/*.mmd}; do
  n=$(basename "$f" .mmd)
  mmdc -q -c mermaid-config.json -i "$f" -o "out/$n.svg" -b white
  mmdc -q -c mermaid-config.json -i "$f" -o "out/$n.png" -b white -s 2
  echo "rendered $n"
done
