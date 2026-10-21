"""Evaluation harness entrypoint.

    python -m evaluation.cli demo --repeats 5 --out runs.json
    python -m evaluation.cli report --log runs.json

No UC2 draft generator exists yet, so there is no real model to point this
at. `demo` runs two synthetic baseline conditions through the full
harness (gold set -> draft -> score -> log) so the plumbing can be
exercised and read end to end today; swap `_perfect_condition` /
`_noisy_condition` below for a real adapter that calls the local model
(llama.cpp/Ollama) once UC2 exists -- nothing else in this module needs to
change. `report` reads any run log this harness has written and prints
mean +/- stdev per condition per metric, so two logged runs (e.g.
before/after a prompt change) can be compared without re-running anything.
"""
from __future__ import annotations

import argparse
import random
import sys

from .gold_set import GOLD_SET
from .models import Claim, Draft, GoldClaim
from .results_log import append_run_log, load_run_log, summarize
from .runner import run_conditions


def _perfect_condition(gold_set: tuple[GoldClaim, ...]) -> Draft:
    """Always cites exactly the right SourceRecord ids -- the harness's
    own sanity ceiling: every metric should read as perfect against this."""
    claims = [Claim(text=g.text, cited_ids=g.supporting_ids, gold_id=g.id) for g in gold_set]
    return Draft(condition="perfect", claims=claims)


def _noisy_condition(gold_set: tuple[GoldClaim, ...]) -> Draft:
    """A stand-in 'model' that sometimes fabricates (no citation),
    sometimes misgrounds (cites a real but wrong id), and once in a while
    plants a deliberate injected_error claim -- enough variety that every
    metric moves, so `demo` output isn't suspiciously all-1.0 or all-0.0."""
    claims = []
    for i, g in enumerate(gold_set):
        if i % 4 == 0:
            claims.append(Claim(text=g.text, cited_ids=(), gold_id=g.id))
        elif i % 4 == 1:
            wrong_id = f"wrong-{g.id}"
            claims.append(Claim(text=g.text, cited_ids=(wrong_id,), gold_id=g.id, injected_error=True))
        else:
            claims.append(Claim(text=g.text, cited_ids=g.supporting_ids, gold_id=g.id))
    random.Random(0).shuffle(claims)  # deterministic shuffle, not real stochasticity
    return Draft(condition="noisy", claims=claims)


def _print_summary(summary: dict) -> None:
    for condition, metrics in summary.items():
        print(f"\n{condition}:")
        for metric, stats in metrics.items():
            print(f"  {metric:28s} mean={stats['mean']:.3f}  stdev={stats['stdev']:.3f}  n={stats['n']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evaluation")
    sub = parser.add_subparsers(dest="command", required=True)

    demo_cmd = sub.add_parser("demo", help="Run the two built-in synthetic conditions through the harness and log the result")
    demo_cmd.add_argument("--repeats", type=int, default=3)
    demo_cmd.add_argument("--out", default="runs.json")
    demo_cmd.add_argument("--label", default=None)

    report_cmd = sub.add_parser("report", help="Print mean +/- stdev per condition per metric from a run log")
    report_cmd.add_argument("--log", required=True)

    args = parser.parse_args(argv)

    if args.command == "demo":
        results = run_conditions(
            {"perfect": _perfect_condition, "noisy": _noisy_condition},
            gold_set=GOLD_SET, repeats=args.repeats,
        )
        append_run_log(args.out, results, label=args.label)
        _print_summary(summarize(results))
        print(f"\nLogged {len(results)} runs to {args.out}")
        return 0

    if args.command == "report":
        log = load_run_log(args.log)
        if not log:
            print(f"{args.log} has no runs logged yet")
            return 0
        latest = log[-1]
        print(f"Latest run: {latest['timestamp']} (label={latest.get('label')})")
        _print_summary(latest["summary"])
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
