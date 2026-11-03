"""Repeatable experiment runs (brief section 15). Callable without the UI.

    record = run_experiment(workspace, "E1")            # uses config/experiments.toml
    summarise_runs(workspace)                           # mean/stdev per experiment x metric

Two units of work, set per experiment with `unit` in config/experiments.toml:

  unit = "report"   (default for the comparison) one full report per case x seed, through
                    reports.runs.generate_report_run, then scored against the original report
                    (reports.evaluate) when the case has one. The drafts, the Word report and
                    the evaluation stay in <workspace>/reports/<case>/<report run>/; the
                    experiment record links them and keeps every number needed to compare.
  unit = "section"  the listed sections only, one draft per case x section x seed (quick
                    checks such as SMOKE); every draft is kept in the record.

Each run writes <workspace>/runs/<run_id>.json with: config, git commit, dataset checksums,
config + prompt fingerprints, machine, time, tokens, memory (Python AND the model server:
llm.resources) and the scores. Failed sections are recorded, never dropped (research
validity, brief section 18).
"""
from __future__ import annotations

import json
import platform
import statistics
import time
import uuid
from typing import Optional

from app.services import Workspace
from drafting.models import now_iso
from drafting.pipelines import PipelineConfig, run_pipeline
from evidence.builder import evidence_index
from llm.providers import OllamaProvider
from llm.registry import get_provider, validate_model_config
from llm.resources import MemoryMonitor, machine_info, ollama_version, unload_ollama_models
from verification.checks import verify_section

from .provenance import fingerprints, git_commit

METRICS = ("grounded_rate", "weak_rate", "unsupported_rate", "citation_validity", "number_support_rate",
           "style_flags_per_claim", "coverage_salient", "n_claims")

# per full report: from the run's own checks (totals) and from the comparison with the original
REPORT_METRICS = ("n_claims", "grounded_rate", "weak_rate", "unsupported_rate", "wall_s", "model_calls",
                  "tokens_in", "tokens_out", "model_load_s", "prompt_tokens_per_s", "output_tokens_per_s",
                  "model_peak_mb")
EVAL_METRICS = ("structure", "coverage", "ceiling", "coverage_of_supportable", "numbers_agree_rate",
                "possible_number_conflicts", "addition_rate", "figures")

_git_commit = git_commit          # kept for older imports


def experiment_definition(exp_id: str) -> dict:
    from ingestion.config import load_config
    cfg = load_config("experiments.toml")
    if exp_id not in cfg["experiment"]:
        raise KeyError(f"unknown experiment {exp_id!r}; known: {', '.join(cfg['experiment'])}")
    d = dict(cfg["defaults"])
    d.update(cfg["experiment"][exp_id])
    d["id"] = exp_id
    return d


def _base_record(ws: Workspace, run_id: str, d: dict, cases: list[str], provider, model_id: Optional[str],
                 tag: Optional[str]) -> dict:
    docs = {x["filename"]: x["checksum"] for x in ws.documents()
            if x.get("status") == "parsed" and (x.get("case_id") in cases or x.get("perspective") == "reference")}
    return {
        "run_id": run_id, "experiment": d, "unit": d.get("unit", "section"), "tag": tag, "started_at": now_iso(),
        "model_id": model_id if provider else None,
        "model": provider.model_info.to_dict() if provider else None,
        "code_version": git_commit(), "python": platform.python_version(), "platform": platform.platform(),
        "machine": machine_info(),
        "fingerprints": fingerprints(d["prompt_version"]),
        "dataset_checksums": docs,
        "results": [],
    }


def _provider_for(d: dict, model_id: Optional[str]):
    if d["pipeline"] == "template":
        return None
    problems = validate_model_config(model_id)
    if problems:
        raise ValueError("; ".join(problems))
    return get_provider(model_id)


def run_experiment(ws: Workspace, exp_id: str, cases: Optional[list[str]] = None,
                   sections: Optional[list[str]] = None, model: Optional[str] = None,
                   seeds: Optional[list[int]] = None, verbose: bool = True, cpu_only: bool = False,
                   cold: bool = False, tag: Optional[str] = None, evaluate: Optional[bool] = None,
                   reference_pdf: Optional[str] = None) -> dict:
    """cpu_only: Ollama keeps the model off the GPU (num_gpu 0). cold: unload Ollama models before
    each report (time includes loading; memory is the run's own). tag: e.g. 'pilot-week1' for runs
    that do not count in the comparison. evaluate: score against the original report (default:
    the experiment's `evaluate`, true for unit = "report")."""
    d = experiment_definition(exp_id)
    cases = cases or d["cases"]
    model_id = model or d.get("model")
    seeds = seeds or d["seeds"]
    if d.get("unit", "section") == "report":
        return _run_reports(ws, d, cases, sections or d.get("report_sections"), model_id, seeds, verbose,
                            cpu_only, cold, tag, d.get("evaluate", True) if evaluate is None else evaluate,
                            reference_pdf)
    return _run_sections(ws, d, cases, sections or d["sections"], model_id, seeds, verbose, cpu_only, cold, tag)


# ------------------------------------------------------------------ unit = "report"

def _eval_metrics(summary: dict) -> dict:
    c = summary["content"]
    f = summary.get("figures") or {}
    return {
        "method": summary.get("method", "word-overlap"),
        "structure": summary["structure"]["coverage"],
        "reference_statements": c["reference_statements"],
        "coverage": c["coverage"], "ceiling": c["ceiling"], "coverage_of_supportable": c["coverage_of_supportable"],
        "numbers_in_reference": c["numbers_in_reference"], "numbers_agree": c["numbers_agree"],
        "numbers_agree_rate": (round(c["numbers_agree"] / c["numbers_in_reference"], 3)
                               if c["numbers_in_reference"] else None),
        "possible_number_conflicts": c["possible_number_conflicts"], "addition_rate": c["addition_rate"],
        "figures": f.get("coverage_maps_and_charts"),
    }


def _run_reports(ws: Workspace, d: dict, cases: list[str], sections: Optional[list[str]], model_id: Optional[str],
                 seeds: list[int], verbose: bool, cpu_only: bool, cold: bool, tag: Optional[str], evaluate: bool,
                 reference_pdf: Optional[str]) -> dict:
    from reports.evaluate import _reference, evaluate_run
    from reports.runs import generate_report_run

    exp_id = d["id"]
    provider = _provider_for(d, model_id)
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{exp_id}-{uuid.uuid4().hex[:4]}"
    record = _base_record(ws, run_id, d, cases, provider, model_id, tag)
    record["runtime"] = {"cpu_only": cpu_only and isinstance(provider, OllamaProvider), "cold_start": cold}
    t_run = time.perf_counter()
    for case_id in cases:
        reference, ref_note = None, None
        if evaluate:
            try:
                reference = _reference(ws, case_id, reference_pdf)
            except FileNotFoundError as exc:
                ref_note = f"not evaluated: {exc}"
                if verbose:
                    print(f"[{exp_id}] {case_id}: {ref_note}")
        for seed in seeds:
            res = {"case_id": case_id, "seed": seed, "report_run_id": None, "failed": True, "errors": []}
            try:
                rr = generate_report_run(
                    ws, case_id, None if provider is None else model_id, pipeline=d["pipeline"],
                    prompt_version=d["prompt_version"], sections=sections, seed=seed,
                    selection=d.get("selection", "all"), temperature=d.get("temperature", 0.2),
                    include_quotes=d.get("include_quotes", True),
                    allow_resident_text_to_hosted=d.get("allow_resident_text_to_hosted", False),
                    render=d.get("render", True), cpu_only=cpu_only, cold=cold, tag=tag, experiment_id=exp_id,
                    progress=(lambda m: print("  ", m)) if verbose else None)
                t = rr["totals"]
                res.update({"report_run_id": rr["run_id"], "failed": not rr["usable"],
                            "failed_sections": t["failed_sections"], "wall_s": t["wall_s"],
                            "totals": t, "resources": rr["resources"]})
            except Exception as exc:  # noqa: BLE001 - record and continue
                res["errors"].append(f"{type(exc).__name__}: {exc}")
            if res["report_run_id"] and reference is not None:
                try:
                    ev = evaluate_run(ws, case_id, res["report_run_id"], reference=reference)
                    res["evaluation"] = _eval_metrics(ev["summary"])
                except Exception as exc:  # noqa: BLE001
                    res["errors"].append(f"evaluation: {type(exc).__name__}: {exc}")
            elif ref_note:
                res["evaluation_note"] = ref_note
            record["results"].append(res)
            if verbose:
                print(_report_line(exp_id, res))
    record["finished_at"] = now_iso()
    record["total_wall_s"] = round(time.perf_counter() - t_run, 2)
    record["summary"] = summarise_reports(record["results"])
    return _save(ws, record)


def _report_line(exp_id: str, r: dict) -> str:
    if not r.get("report_run_id"):
        return f"[{exp_id}] {r['case_id']} / seed {r['seed']}: FAILED {r['errors']}"
    t, ev = r["totals"], r.get("evaluation") or {}
    mem = t.get("model_peak_mb")
    pct = lambda x: "–" if x is None else f"{round(100 * x)}%"   # noqa: E731
    return (f"[{exp_id}] {r['case_id']} / seed {r['seed']}: {t['wall_s']} s ({t['wall_s'] / 60:.1f} min), "
            f"{t['n_claims']} claims, unsupported {pct(t.get('unsupported_rate'))}"
            + (f", model memory {mem / 1024:.1f} GB" if mem else "")
            + (f", coverage of what the data supports {pct(ev.get('coverage_of_supportable'))}" if ev else "")
            + (f"  FAILED SECTIONS: {r['failed_sections']}" if r.get("failed_sections") else "")
            + (f"  ERRORS: {r['errors']}" if r["errors"] else ""))


def _stats(vals: list) -> Optional[dict]:
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {"mean": round(statistics.mean(vals), 3), "stdev": round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0,
            "min": min(vals), "max": max(vals), "n": len(vals)}


def summarise_reports(results: list[dict]) -> dict:
    """Mean, sd, min and max over the seeds (and cases) of one experiment run."""
    ok = [r for r in results if r.get("report_run_id")]
    out: dict = {"n": len(results), "failed": sum(1 for r in results if r["failed"]),
                 "with_failed_sections": sum(1 for r in ok if r.get("failed_sections"))}
    for m in REPORT_METRICS:
        s = _stats([r["totals"].get(m) for r in ok])
        if s:
            out[m] = s
    for m in EVAL_METRICS:
        s = _stats([(r.get("evaluation") or {}).get(m) for r in ok])
        if s:
            out[m] = s
    within = [r["totals"].get("within_memory_limit") for r in ok if r["totals"].get("within_memory_limit") is not None]
    out["within_memory_limit"] = all(within) if within else None
    return out


# ------------------------------------------------------------------ unit = "section"

def _run_sections(ws: Workspace, d: dict, cases: list[str], sections: list[str], model_id: Optional[str],
                  seeds: list[int], verbose: bool, cpu_only: bool, cold: bool, tag: Optional[str]) -> dict:
    exp_id = d["id"]
    provider = _provider_for(d, model_id)
    ollama_url = provider.base_url if isinstance(provider, OllamaProvider) else None
    if cpu_only and ollama_url:
        provider.num_gpu = 0
        provider.timeout_s = max(provider.timeout_s, 1800.0)
    unloaded = unload_ollama_models(ollama_url) if (cold and ollama_url) else []
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{exp_id}-{uuid.uuid4().hex[:4]}"
    record = _base_record(ws, run_id, d, cases, provider, model_id, tag)
    record["runtime"] = {"cpu_only": bool(cpu_only and ollama_url), "cold_start": bool(cold and ollama_url),
                         "unloaded_before": unloaded,
                         "ollama_version": ollama_version(ollama_url) if ollama_url else None}
    t_run = time.perf_counter()
    with MemoryMonitor(base_url=ollama_url) as monitor:
        for case_id in cases:
            packs = ws.evidence(case_id)
            idx = evidence_index(packs)
            for section in sections:
                for seed in seeds:
                    cfg = PipelineConfig(pipeline_id=d["pipeline"], prompt_version=d["prompt_version"],
                                         selection=d.get("selection", "all"), temperature=d.get("temperature", 0.2),
                                         seed=seed, case_label=ws.case_label(case_id),
                                         allow_resident_text_to_hosted=d.get("allow_resident_text_to_hosted", False),
                                         include_quotes=d.get("include_quotes", True))
                    t0 = time.perf_counter()
                    try:
                        draft = run_pipeline(packs[section], cfg, provider)
                        checks = verify_section(draft, packs[section], idx)
                        ws.save_draft(draft)
                        err = draft.errors
                    except Exception as exc:  # noqa: BLE001 - record and continue
                        draft, checks, err = None, None, [f"{type(exc).__name__}: {exc}"]
                    wall = round(time.perf_counter() - t0, 3)
                    res = {
                        "case_id": case_id, "section": section, "seed": seed, "wall_s": wall, "errors": err,
                        "failed": draft is None or (not draft.claims),
                        "metrics": checks["metrics"] if checks else None,
                        "coverage_gaps": checks["coverage_gaps"] if checks else None,
                        "tokens_in": sum(s.input_tokens or 0 for s in draft.stages) if draft else None,
                        "tokens_out": sum(s.output_tokens or 0 for s in draft.stages) if draft else None,
                        "model_latency_s": round(sum(s.latency_s for s in draft.stages), 3) if draft else None,
                        "draft_id": draft.draft_id if draft else None,
                        "draft": draft.to_dict() if draft else None,
                    }
                    record["results"].append(res)
                    if verbose:
                        m = res["metrics"] or {}
                        print(f"[{exp_id}] {case_id} / {section} / seed {seed}: {wall}s, "
                              f"{m.get('n_claims', 0)} claims, grounded {m.get('grounded_rate', 0):.0%}, "
                              f"unsupported {m.get('unsupported_rate', 0):.0%}"
                              + (f"  ERRORS: {err}" if err else ""))
    record["finished_at"] = now_iso()
    record["total_wall_s"] = round(time.perf_counter() - t_run, 2)
    record["resources"] = monitor.summary()
    record["peak_rss_mb_python"] = record["resources"]["python_peak_mb"]
    record["summary"] = summarise_results(record["results"])
    record["summary"]["model_peak_mb"] = record["resources"]["model_peak_mb"]
    record["summary"]["within_memory_limit"] = record["resources"]["within_limit"]
    return _save(ws, record)


def _save(ws: Workspace, record: dict) -> dict:
    path = ws.p("runs", f"{record['run_id']}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    record["path"] = str(path)
    return record


def summarise_results(results: list[dict]) -> dict:
    out = {"n": len(results), "failed": sum(1 for r in results if r["failed"])}
    for m in METRICS:
        vals = [r["metrics"][m] for r in results if r.get("metrics")]
        if vals:
            out[m] = {"mean": round(statistics.mean(vals), 3),
                      "stdev": round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0}
    for k in ("wall_s", "tokens_in", "tokens_out"):
        vals = [r[k] for r in results if r.get(k) is not None]
        if vals:
            out[k] = {"mean": round(statistics.mean(vals), 2), "total": round(sum(vals), 2)}
    return out


def summarise_runs(ws: Workspace, tag: Optional[str] = None) -> list[dict]:
    """One row per experiment run. tag: only runs with this tag; '' = only untagged (counted) runs."""
    rows = []
    for p in sorted(ws.p("runs").glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        if tag is not None and (r.get("tag") or "") != tag:
            continue
        sm = r["summary"]
        rows.append({"run_id": r["run_id"], "experiment": r["experiment"]["id"], "unit": r.get("unit", "section"),
                     "tag": r.get("tag"), "pipeline": r["experiment"]["pipeline"],
                     "selection": r["experiment"].get("selection"), "model": r.get("model_id"),
                     "cpu_only": (r.get("runtime") or {}).get("cpu_only", False),
                     "code_version": r.get("code_version"), "summary": sm,
                     "peak_rss_mb": r.get("peak_rss_mb_python"),
                     "model_peak_mb": (sm.get("model_peak_mb") or {}).get("max")
                     if isinstance(sm.get("model_peak_mb"), dict) else sm.get("model_peak_mb"),
                     "within_memory_limit": sm.get("within_memory_limit")})
    return rows
