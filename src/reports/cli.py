"""Full reports per model, and how close each one comes to the original report.

    # the three drafting models that are compared (config/models.toml [selection]) and whether they are ready
    python -m reports.cli models

    # one full report (every section, Word + optional PDF) drafted by one model, or the template baseline
    python -m reports.cli generate --case euterpepark-oss --model local_qwen7b --pipeline staged --pdf
    python -m reports.cli generate --case euterpepark-oss --model template

    # draft with all three selected models (and the template baseline), then compare them with the original
    python -m reports.cli compare --case euterpepark-oss --generate --include-template

    # (re)evaluate one run against the original report; optionally let a local model judge the alignment
    python -m reports.cli evaluate --case euterpepark-oss --run latest
    python -m reports.cli evaluate --case euterpepark-oss --run latest --judge local_qwen7b

    # agreement with a researcher's labels (fill in human_label / human_evidence_label in alignment.csv first)
    python -m reports.cli calibrate --csv workspace/reports/euterpepark-oss/<run>/evaluation/alignment.csv

    # list runs; make one run the drafts that the researcher reviews
    python -m reports.cli list --case euterpepark-oss
    python -m reports.cli adopt --case euterpepark-oss --run <run id> --reviewer anouk

The original report is found through the case folder recorded at ingestion (status
'excluded_reference_report'); pass --reference to point at it directly. It is used
for evaluation only. Everything is written under <workspace>/reports/<case>/, which
is git-ignored: the evaluation files quote the original.
"""
from __future__ import annotations

import argparse
import json
import sys

from app.services import Workspace


def _pct(x) -> str:
    return "–" if x is None else f"{round(100 * x)}%"


def _resolve_run(ws, case: str, run: str) -> str:
    from .runs import list_runs
    runs = list_runs(ws, case)
    if not runs:
        raise SystemExit(f"no runs for {case} yet: python -m reports.cli generate --case {case} --model ...")
    if run == "latest":
        return runs[-1]["run_id"]
    matches = [r["run_id"] for r in runs if r["run_id"] == run or r["run_id"].startswith(run)]
    if len(matches) != 1:
        raise SystemExit(f"run {run!r} not found (or ambiguous); see python -m reports.cli list --case {case}")
    return matches[0]


def _print_eval(summary: dict) -> None:
    c, st = summary["content"], summary["structure"]
    print(f"  method: {summary.get('method')}")
    print(f"  chapters of the original present: {_pct(st['coverage'])}  (missing: {', '.join(st['missing']) or '-'})")
    print(f"  statements: {c['reference_statements']}  covered/partial/missing: "
          f"{c['covered']}/{c['partial']}/{c['missing']}  coverage {_pct(c['coverage'])}")
    print(f"  ceiling (supported by the data, at least partly): {_pct(c['ceiling'])}   "
          f"coverage of that: {_pct(c['coverage_of_supportable'])}")
    print(f"  numbers found back: {c['numbers_agree']}/{c['numbers_in_reference']}   "
          f"possibly different numbers: {c['possible_number_conflicts']}   additions: {_pct(c['addition_rate'])}")
    f = summary.get("figures")
    if f:
        print(f"  maps {len(f['map']['matched'])}/{len(f['map']['reference'])}, charts "
              f"{f['chart']['matched']}/{f['chart']['reference']}, photos {f['photo']['generated']}/{f['photo']['reference']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reports", description=__doc__.split("\n\n")[0])
    ap.add_argument("--workspace", default="workspace")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("models", help="the selected drafting models and whether their config is usable")

    g = sub.add_parser("generate", help="draft every section with one model and render the report")
    g.add_argument("--case", required=True)
    g.add_argument("--model", required=True, help="model id from config/models.toml, or 'template'")
    g.add_argument("--pipeline", default="staged", choices=["naive", "staged"])
    g.add_argument("--prompt-version", default="v2")
    g.add_argument("--sections", nargs="*")
    g.add_argument("--pdf", action="store_true", help="also convert the Word file to PDF (needs LibreOffice)")

    c = sub.add_parser("compare", help="evaluate the latest run of each model against the original, side by side")
    c.add_argument("--case", required=True)
    c.add_argument("--models", nargs="*", help="default: [selection] drafting in config/models.toml")
    c.add_argument("--include-template", action="store_true", help="add the template baseline (no model)")
    c.add_argument("--generate", action="store_true", help="draft a new run per model first")
    c.add_argument("--pipeline", default="staged", choices=["naive", "staged"])
    c.add_argument("--pdf", action="store_true")
    c.add_argument("--judge", help="local model id that judges the alignment (default: word overlap only)")
    c.add_argument("--allow-hosted-judge", action="store_true")
    c.add_argument("--reference", help="path to the original report (PDF)")

    e = sub.add_parser("evaluate", help="compare one run with the original report")
    e.add_argument("--case", required=True)
    e.add_argument("--run", default="latest")
    e.add_argument("--judge")
    e.add_argument("--allow-hosted-judge", action="store_true")
    e.add_argument("--reference")

    k = sub.add_parser("calibrate", help="agreement of the automatic labels with a researcher's labels")
    k.add_argument("--csv", required=True)

    li = sub.add_parser("list", help="runs of a case")
    li.add_argument("--case", required=True)

    a = sub.add_parser("adopt", help="make a run's drafts the review sessions of the case")
    a.add_argument("--case", required=True)
    a.add_argument("--run", required=True)
    a.add_argument("--reviewer", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "calibrate":
        from .evaluate import calibrate
        print(json.dumps(calibrate(args.csv), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "models":
        from llm.registry import list_models, selected_models, validate_model_config
        models = list_models()
        for mid in selected_models("drafting"):
            problems = validate_model_config(mid)
            print(f"{mid:18s} {models[mid].get('model', ''):16s} {'ok' if not problems else '; '.join(problems)}")
        print("\nChange the selection in config/models.toml [selection]. Pull the models with: ollama pull <model>")
        return 0

    ws = Workspace(args.workspace)
    if not ws.has_data():
        print("Workspace has no ingested data yet.", file=sys.stderr)
        return 2
    from .runs import adopt_for_review, generate_report_run, list_runs

    if args.cmd == "generate":
        try:
            run = generate_report_run(ws, args.case, args.model, pipeline=args.pipeline,
                                      prompt_version=args.prompt_version, sections=args.sections, pdf=args.pdf,
                                      progress=lambda m: print(" ", m))
        except Exception as exc:  # noqa: BLE001
            print(f"cannot draft with {args.model}: {exc}", file=sys.stderr)
            return 2
        t = run["totals"]
        print(f"\n{run['run_id']}: {t['n_claims']} claims in {t['sections_drafted']} sections, grounded "
              f"{_pct(t['grounded_rate'])}, unsupported {_pct(t['unsupported_rate'])}, {t['wall_s']} s")
        if t["failed_sections"]:
            print("  failed:", ", ".join(t["failed_sections"]))
        print("  files:", ", ".join(run["files"].values()), "in", ws.p("reports", args.case, run["run_id"]))
        return 0

    if args.cmd == "list":
        for r in list_runs(ws, args.case):
            t, ev = r["totals"], r.get("evaluation") or {}
            cov = _pct(ev.get("content", {}).get("coverage")) if ev else "not evaluated"
            print(f"{r['run_id']:45s} {r.get('model_id') or 'template':16s} {r['pipeline']:9s} "
                  f"claims {t['n_claims']:4d}  grounded {_pct(t['grounded_rate']):>4s}  coverage vs original: {cov}")
        return 0

    if args.cmd == "adopt":
        secs = adopt_for_review(ws, args.case, _resolve_run(ws, args.case, args.run), args.reviewer)
        print("review sessions started for:", ", ".join(secs))
        return 0

    from .evaluate import compare_runs, evaluate_run
    if args.cmd == "evaluate":
        res = evaluate_run(ws, args.case, _resolve_run(ws, args.case, args.run), reference_pdf=args.reference,
                           judge_model=args.judge, allow_hosted=args.allow_hosted_judge, progress=print)
        print(res["summary"]["run"]["run_id"])
        _print_eval(res["summary"])
        print("  files:", res["files"]["markdown"], res["files"]["csv"])
        return 0

    if args.cmd == "compare":
        from llm.registry import selected_models
        models = args.models or selected_models("drafting")
        if args.include_template:
            models = ["template", *models]
        if args.generate:
            for m in models:
                print(f"drafting with {m} ...")
                try:
                    generate_report_run(ws, args.case, m, pipeline=args.pipeline, pdf=args.pdf,
                                        progress=lambda msg: print(" ", msg))
                except Exception as exc:  # noqa: BLE001 - one unavailable model should not stop the others
                    print(f"  ! {m} skipped: {exc}")
        from .runs import latest_runs
        latest = latest_runs(ws, args.case)
        run_ids = [latest[m] for m in models if m in latest]
        missing = [m for m in models if m not in latest]
        if missing:
            print("no usable run yet for:", ", ".join(missing), "(use --generate)")
        if not run_ids:
            return 2
        out = compare_runs(ws, args.case, run_ids, reference_pdf=args.reference, judge_model=args.judge,
                           allow_hosted=args.allow_hosted_judge, progress=print)
        print(open(out["markdown"], encoding="utf-8").read())
        print("written:", out["markdown"], out["csv"])
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
