"""Image step CLI.

    # deterministic map measurement only (no model needed)
    python -m imaging.cli analyse --workspace workspace --case euterpepark-oss

    # + model reading of infographics / symbol maps (Ollama vision model)
    python -m imaging.cli analyse --workspace workspace --case euterpepark-oss --vision-model vision_qwen25vl --samples 3

    # accuracy of the model readings against a hand-checked gold file (SQ3: compare vision models).
    # Gold files are derived from confidential case material: keep them OUTSIDE this repository.
    python -m imaging.cli eval-reading --workspace workspace --case euterpepark-oss --gold "$HOME/Desktop/AppliedIT_Masters/Data-Evidence_prj/gold/oss_demografie_readings.toml"
"""
from __future__ import annotations

import argparse
import json
import sys

from app.services import Workspace


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="imaging")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyse")
    a.add_argument("--workspace", default="workspace")
    a.add_argument("--case", required=True)
    a.add_argument("--vision-model")
    a.add_argument("--samples", type=int, default=3)
    e = sub.add_parser("eval-reading")
    e.add_argument("--workspace", default="workspace")
    e.add_argument("--case", required=True)
    e.add_argument("--gold", required=True)
    args = ap.parse_args(argv)

    ws = Workspace(args.workspace)
    if not ws.has_data():
        print("Workspace has no ingested data yet.", file=sys.stderr)
        return 2
    if args.cmd == "analyse":
        summary = ws.analyse_images(args.case, args.vision_model, args.samples)
        for img, item in summary["images"].items():
            print(f"{img:22s} {item['kind']:14s} {item.get('status')}")
            for w in item.get("warnings", []) or []:
                print(f"{'':37s} ! {w}")
        print(f"\n{summary['records_added']} image records; overlays and raw outputs in {ws.images_dir(args.case)}")
        return 0
    if args.cmd == "eval-reading":
        from .evaluate import load_gold, score_readings
        result = score_readings(ws.image_records(args.case, ("image_reading",)), load_gold(args.gold))
        print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
        for m in result["misses"][:30]:
            print("  missed:", m)
        for w in result["wrong"][:30]:
            print("  wrong: ", w)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
