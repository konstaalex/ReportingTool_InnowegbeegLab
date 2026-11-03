"""Experiment CLI.

    python -m experiments.cli run SMOKE --workspace workspace           # fake model, plumbing only
    python -m experiments.cli run E1 --workspace workspace              # full Oss reports, 3 seeds, scored
    python -m experiments.cli run E2 --seed 42 --cold --tag pilot-week1 # one report, cold start, not counted
    python -m experiments.cli run E2 --seed 42 --cold --cpu-only        # same, model kept off the GPU (SQ4)
    python -m experiments.cli run E1 --case corlaer-nijkerk --model local_llama8b
    python -m experiments.cli summary --workspace workspace             # all runs
    python -m experiments.cli summary --tag pilot-week1                 # only the week-1 pilot runs
    python -m experiments.cli summary --counted                         # only untagged runs
"""
from __future__ import annotations

import argparse
import json
import sys

from app.services import Workspace

from .runner import run_experiment, summarise_runs


def _pct(x) -> str:
    return "  –" if x is None else f"{round(100 * x):3d}%"


def _mean(sm: dict, k: str):
    v = sm.get(k)
    return v.get("mean") if isinstance(v, dict) else v


def _print_summary(rows: list[dict]) -> None:
    if not rows:
        print("no runs")
        return
    print(f"{'run':34s} {'exp':12s} {'pipeline':9s} {'model':14s} {'cpu':3s} {'n':>2s} {'fail':>4s} "
          f"{'min/unit':>8s} {'model GB':>8s} {'limit':>5s} {'unsupp':>6s} {'ground':>6s} {'cov.sup':>7s} {'ceiling':>7s}")
    for r in rows:
        sm = r["summary"]
        wall = _mean(sm, "wall_s")
        gb = r["model_peak_mb"] / 1024 if r["model_peak_mb"] else None
        lim = {True: "ok", False: "OVER", None: "–"}[r.get("within_memory_limit")]
        print(f"{r['run_id']:34s} {r['experiment']:12s} {r['pipeline']:9s} {str(r['model'] or 'none'):14s} "
              f"{'yes' if r['cpu_only'] else 'no':3s} {sm['n']:2d} {sm['failed']:4d} "
              f"{(wall / 60 if wall is not None else 0):8.1f} {(f'{gb:.1f}' if gb else '–'):>8s} {lim:>5s} "
              f"{_pct(_mean(sm, 'unsupported_rate')):>6s} {_pct(_mean(sm, 'grounded_rate')):>6s} "
              f"{_pct(_mean(sm, 'coverage_of_supportable')):>7s} {_pct(_mean(sm, 'ceiling')):>7s}")
    print("\nmin/unit = minutes per full report (unit report) or per section draft (unit section); "
          "model GB = peak memory of the model server; limit = 60% of 16 GB (config/models.toml); "
          "unsupp/ground = verifier; cov.sup = coverage of what the data supports, vs. the original report.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="experiments")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("experiment")
    run.add_argument("--workspace", default="workspace")
    run.add_argument("--case", action="append")
    run.add_argument("--section", action="append", help="limit to these sections")
    run.add_argument("--model")
    run.add_argument("--seed", action="append", type=int)
    run.add_argument("--cpu-only", action="store_true",
                     help="keep the model off the GPU (Ollama num_gpu 0): closest to the CPU-only target laptop")
    run.add_argument("--cold", action="store_true",
                     help="unload Ollama models before each report: time includes loading, memory is the run's own")
    run.add_argument("--tag", help="label kept in the record, e.g. pilot-week1 (tagged runs do not count)")
    run.add_argument("--no-evaluate", action="store_true", help="do not score against the original report")
    run.add_argument("--reference", help="path to the original report (PDF), if the workspace cannot find it")
    s = sub.add_parser("summary")
    s.add_argument("--workspace", default="workspace")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--tag", help="only runs with this tag")
    g.add_argument("--counted", action="store_true", help="only untagged runs (the ones that count)")
    s.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ws = Workspace(args.workspace)
    if not ws.has_data():
        print("Workspace has no ingested data. Run: python -m ingestion.cli ingest-wijkscan <folder> "
              f"--out {args.workspace} --case-id euterpepark-oss", file=sys.stderr)
        return 2
    if args.cmd == "run":
        rec = run_experiment(ws, args.experiment, cases=args.case, sections=args.section, model=args.model,
                             seeds=args.seed, cpu_only=args.cpu_only, cold=args.cold, tag=args.tag,
                             evaluate=False if args.no_evaluate else None, reference_pdf=args.reference)
        print()
        _print_summary([r for r in summarise_runs(ws) if r["run_id"] == rec["run_id"]])
        print(f"\nRun record: {rec['path']}")
        return 0
    if args.cmd == "summary":
        rows = summarise_runs(ws, tag="" if args.counted else args.tag)
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            _print_summary(rows)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
