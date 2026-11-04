#!/usr/bin/env bash
# Week 1 (3-8 Nov 2026): how long does one full Oss report take, and how much memory does
# the model use? The answer sizes the experiment plan (about 30 full-report runs).
#
#   bash scripts/week1_timing.sh             # install models, smoke-test them, then one report each:
#                                            # template (E0), single prompt A (E1), staged B (E2)
#   CPU=1 bash scripts/week1_timing.sh       # also B with the model kept off the GPU (Ollama num_gpu 0):
#                                            # closest to the 16 GB CPU-only laptop on a Mac; takes longer
#   SKIP_PULL=1 bash scripts/week1_timing.sh # models already downloaded
#   SKIP_INGEST=1 ...                        # keep the workspace as it is (default: re-ingest the Oss
#                                            # case first, because a workspace from before 30 Oct does
#                                            # not know where the original report is)
#
# These are pilot runs (tag pilot-week1). They size the plan and do NOT count in the comparison
# (docs/experiments/hypotheses-and-thresholds.md). Each model run starts cold: Ollama unloads
# every model first, so the time includes loading and the memory is the run's own.
# Close other heavy apps while it runs; keep the laptop on power.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${VIRTUAL_ENV:-}" && -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
export PYTHONPATH=src
WS="${WS:-workspace}"
TAG="${TAG:-pilot-week1}"
OUT="$WS/week1_timing_$(date +%Y%m%d-%H%M).txt"

python -c "import psutil" 2>/dev/null || { echo "psutil missing: pip install -r requirements.txt"; exit 1; }

# 0. Re-ingest the Oss case with the current code and measure its maps (no model). The workspace
#    must know where the original report is, or the runs cannot be scored against it.
OSS="${OSS:-$HOME/Desktop/AppliedIT_Masters/Data-Evidence_prj/Perspectieven Oss}"
if [[ -z "${SKIP_INGEST:-}" ]]; then
  echo "== 0. Ingest the Oss case and measure the maps"
  python -m ingestion.cli ingest-wijkscan "$OSS" --out "$WS" --case-id euterpepark-oss --label "Euterpepark Oss" > /dev/null
  python -m imaging.cli analyse --workspace "$WS" --case euterpepark-oss
fi
python -c "
import sys; sys.path.insert(0, 'src')
from app.services import Workspace
from reports.evaluate import reference_path
p = reference_path(Workspace('$WS'), 'euterpepark-oss')
print('original report:', p.name if p else 'NOT FOUND - runs will not be scored')
"

echo "== 1. The three drafting models (config/models.toml [selection])"
if [[ -z "${SKIP_PULL:-}" ]]; then
  for m in qwen2.5:7b qwen3:8b llama3.1:8b; do ollama pull "$m"; done
fi
python -m llm.cli check
for m in local_qwen7b local_qwen3_8b local_llama8b; do
  python -m llm.cli smoke "$m" || echo "!! smoke test failed for $m (see above)"
done

echo "== 2. One full Oss report each, seed 42, qwen2.5:7b"
python -m experiments.cli run E0 --workspace "$WS" --tag "$TAG"
python -m experiments.cli run E1 --workspace "$WS" --seed 42 --cold --tag "$TAG"
python -m experiments.cli run E2 --workspace "$WS" --seed 42 --cold --tag "$TAG"
if [[ -n "${CPU:-}" ]]; then
  python -m experiments.cli run E2 --workspace "$WS" --seed 42 --cold --cpu-only --tag "$TAG"
fi

echo "== 3. Time and memory per report"
{
  date
  python -c "import platform; print(platform.platform())"
  python -m experiments.cli summary --workspace "$WS" --tag "$TAG"
} | tee "$OUT"
echo
echo "Saved to $OUT (numbers only, no research data). Paste it back to plan the run budget."
