"""Repeatable experiment runs (brief section 15). Callable without the UI.

    record = run_experiment(workspace, "E1")            # uses config/experiments.toml
    summarise_runs(workspace)                           # mean/stdev per experiment x metric

Each run writes <workspace>/runs/<run_id>.json with: config, git commit,
dataset checksums, config + prompt fingerprints, per-stage latency/tokens,
peak memory, every draft (claims + raw model output) and verifier metrics.
Failed sections are recorded, never dropped (research validity, section 18).
"""
from __future__ import annotations

import json
import platform
import resource
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from app.services import Workspace
from drafting.models import now_iso
from drafting.pipelines import PipelineConfig, run_pipeline
from drafting.prompts import prompt_sha
from evidence.builder import evidence_index
from ingestion.config import REPO_ROOT, config_fingerprint, load_config
from llm.registry import get_provider, validate_model_config
from verification.checks import verify_section

METRICS = ("grounded_rate", "weak_rate", "unsupported_rate", "citation_validity", "number_support_rate",
           "style_flags_per_claim", "coverage_salient", "n_claims")


def _git_commit() -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        dirty = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return (out.stdout.strip() + ("-dirty" if dirty else "")) or None
    except Exception:  # noqa: BLE001
        return None


def _peak_rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / (1024 * 1024) if sys.platform == "darwin" else r / 1024, 1)


def experiment_definition(exp_id: str) -> dict:
    cfg = load_config("experiments.toml")
    if exp_id not in cfg["experiment"]:
        raise KeyError(f"unknown experiment {exp_id!r}; known: {', '.join(cfg['experiment'])}")
    d = dict(cfg["defaults"])
    d.update(cfg["experiment"][exp_id])
    d["id"] = exp_id
    return d


def run_experiment(ws: Workspace, exp_id: str, cases: Optional[list[str]] = None,
                   sections: Optional[list[str]] = None, model: Optional[str] = None,
                   seeds: Optional[list[int]] = None, verbose: bool = True) -> dict:
    d = experiment_definition(exp_id)
    cases = cases or d["cases"]
    sections = sections or d["sections"]
    model_id = model or d.get("model")
    seeds = seeds or d["seeds"]
    provider = None
    if d["pipeline"] != "template":
        problems = validate_model_config(model_id)
        if problems:
            raise ValueError("; ".join(problems))
        provider = get_provider(model_id)

    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{exp_id}-{uuid.uuid4().hex[:4]}"
    docs = {x["filename"]: x["checksum"] for x in ws.documents()
            if x.get("status") == "parsed" and (x.get("case_id") in cases or x.get("perspective") == "reference")}
    record = {
        "run_id": run_id, "experiment": d, "started_at": now_iso(), "model_id": model_id if provider else None,
        "model": provider.model_info.to_dict() if provider else None,
        "code_version": _git_commit(), "python": platform.python_version(), "platform": platform.platform(),
        "machine": {"processor": platform.processor(), "machine": platform.machine()},
        "fingerprints": {
            "prompts": prompt_sha(d["prompt_version"]),
            "survey_mapping": config_fingerprint("survey_mapping.toml"),
            "writing_rules": config_fingerprint("reference/writing_rules.toml"),
            "rivm_rules": config_fingerprint("reference/rivm_vuistregels.toml"),
            "models": config_fingerprint("models.toml"),
        },
        "dataset_checksums": docs,
        "results": [],
    }
    t_run = time.perf_counter()
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
    record["peak_rss_mb_python"] = _peak_rss_mb()
    record["summary"] = summarise_results(record["results"])
    path = ws.p("runs", f"{run_id}.json")
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


def summarise_runs(ws: Workspace) -> list[dict]:
    rows = []
    for p in sorted(ws.p("runs").glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        rows.append({"run_id": r["run_id"], "experiment": r["experiment"]["id"],
                     "pipeline": r["experiment"]["pipeline"], "selection": r["experiment"].get("selection"),
                     "model": r.get("model_id"), "summary": r["summary"], "peak_rss_mb": r.get("peak_rss_mb_python")})
    return rows
