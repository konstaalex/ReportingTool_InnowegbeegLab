"""Experiment CLI.

    python -m experiments.cli run E1 --workspace workspace
    python -m experiments.cli run SMOKE --workspace workspace           # fake model, plumbing only
    python -m experiments.cli run E1 --case passewaaij-tiel --section Sporten --model local_llama8b
    python -m experiments.cli summary --workspace workspace
"""
from __future__ import annotations

import argparse
import json
import sys

from app.services import Workspace

from .runner import run_experiment, summarise_runs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="experiments")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("experiment")
    run.add_argument("--workspace", default="workspace")
    run.add_argument("--case", action="append")
    run.add_argument("--section", action="append")
    run.add_argument("--model")
    run.add_argument("--seed", action="append", type=int)
    s = sub.add_parser("summary")
    s.add_argument("--workspace", default="workspace")
    args = ap.parse_args(argv)

    ws = Workspace(args.workspace)
    if not ws.has_data():
        print("Workspace has no ingested data. Run: python -m ingestion.cli ingest-buurtscan <folder> "
              f"--out {args.workspace}/ingest", file=sys.stderr)
        return 2
    if args.cmd == "run":
        rec = run_experiment(ws, args.experiment, cases=args.case, sections=args.section, model=args.model,
                             seeds=args.seed)
        print(json.dumps(rec["summary"], indent=2))
        print(f"\nRun record: {rec['path']}")
        return 0
    if args.cmd == "summary":
        for row in summarise_runs(ws):
            sm = row["summary"]
            g = sm.get("grounded_rate", {}).get("mean", 0)
            u = sm.get("unsupported_rate", {}).get("mean", 0)
            c = sm.get("coverage_salient", {}).get("mean", 0)
            print(f"{row['run_id']:40s} {row['experiment']:6s} {row['pipeline']:9s} {str(row['model']):18s} "
                  f"n={sm['n']:3d} failed={sm['failed']:2d} grounded={g:.2f} unsupported={u:.2f} coverage={c:.2f} "
                  f"wall={sm.get('wall_s', {}).get('mean', 0)}s")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
