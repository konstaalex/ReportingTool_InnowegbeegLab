"""Full-report runs: one model drafts every section of a case.

A run is the unit for comparing the three selected models (SQ3): same evidence,
same prompts, same pipeline, a different model. Each run is stored on its own,
rendered as a Wijkscan report in Word, and can be evaluated against the original
report (reports.evaluate). Runs never touch the review sessions; a researcher
picks one run and adopts it for review (adopt_for_review).

    run = generate_report_run(ws, "euterpepark-oss", "local_qwen7b", pipeline="staged")
    list_runs(ws, "euterpepark-oss")
    adopt_for_review(ws, "euterpepark-oss", run["run_id"], reviewer="anouk")

Layout: <workspace>/reports/<case>/<run_id>/run.json, drafts/<section>.json,
        <kind>_<case>_<model>.docx (+ .pdf), evaluation/ (after evaluation)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from drafting.models import SectionDraft, now_iso
from drafting.pipelines import PipelineConfig, run_pipeline
from evidence.builder import evidence_index
from evidence.models import AANLEIDING, CONCLUSIES, GENERAL
from ingestion.models import THEMES
from llm.registry import get_provider, validate_model_config
from review.session import ReviewSession
from verification.checks import verify_section

REPORT_SECTIONS = [*THEMES, GENERAL, CONCLUSIES, AANLEIDING]
TEMPLATE = "template"


def _slug(s: str) -> str:
    return s.lower().replace(" ", "-")


def runs_dir(ws, case_id: str) -> Path:
    return ws.p("reports", case_id)


def preflight(provider) -> None:
    """Fail before a run starts when a local model cannot answer (Ollama not running, model not
    downloaded), instead of recording nine failed sections."""
    from llm.providers import OllamaProvider
    if isinstance(provider, OllamaProvider):
        from llm.cli import _is_pulled
        pulled = provider.available_models()            # raises ProviderError when Ollama is not running
        tag = provider.model_info.model
        if not _is_pulled(tag, pulled):
            raise RuntimeError(f"model {tag} is not downloaded in Ollama; run: ollama pull {tag}")


def _stage_timings(stages) -> dict:
    """Sum of the provider-reported timings over model calls (seconds), if the provider reports them."""
    keys = ("load_s", "prompt_eval_s", "eval_s")
    tot = {k: 0.0 for k in keys}
    seen = False
    for s in stages:
        t = getattr(s, "timings", None) or {}
        for k in keys:
            if t.get(k):
                tot[k] += t[k]
                seen = True
    return {k: round(v, 2) for k, v in tot.items()} if seen else {}


def generate_report_run(ws, case_id: str, model_id: Optional[str], pipeline: str = "staged",
                        prompt_version: str = "v2", sections: Optional[list[str]] = None, seed: int = 42,
                        selection: str = "all", render: bool = True, pdf: bool = False,
                        progress: Optional[Callable[[str], None]] = None, temperature: float = 0.2,
                        include_quotes: bool = True, allow_resident_text_to_hosted: bool = False,
                        cpu_only: bool = False, cold: bool = False, tag: Optional[str] = None,
                        experiment_id: Optional[str] = None) -> dict:
    """Draft every section with one model (or the template baseline when model_id is None/'template').

    cpu_only: tell Ollama to keep every model layer off the GPU (num_gpu 0), to approximate the
              16 GB CPU-only target laptop on a machine that has a GPU (SQ4).
    cold:     unload all Ollama models first, so the time includes loading the model and the
              memory measured is this run's own.
    tag:      free label kept in the record, e.g. 'pilot-week1' for runs that do not count.
    """
    from experiments.provenance import fingerprints, git_commit
    from llm.providers import OllamaProvider
    from llm.resources import MemoryMonitor, machine_info, ollama_version, unload_ollama_models

    if not model_id or model_id == TEMPLATE:
        model_id, pipeline, provider = None, TEMPLATE, None
    else:
        problems = validate_model_config(model_id)
        if problems:
            raise ValueError("; ".join(problems))
        provider = get_provider(model_id)
        preflight(provider)
    ollama_url = provider.base_url if isinstance(provider, OllamaProvider) else None
    if cpu_only and not ollama_url:
        cpu_only = False                      # only meaningful for a local Ollama model
    if cpu_only:
        provider.num_gpu = 0
        provider.timeout_s = max(provider.timeout_s, 1800.0)   # a long prompt on a CPU can exceed 10 minutes
    unloaded = unload_ollama_models(ollama_url) if (cold and ollama_url) else []
    label = model_id or TEMPLATE
    parts = [time.strftime("%Y%m%d-%H%M%S"), label, pipeline]
    if selection != "all":
        parts.append(selection.replace("top_k:", "top").replace(":", ""))
    parts.append(f"s{seed}")
    if cpu_only:
        parts.append("cpu")
    run_id = "-".join(parts)
    out = runs_dir(ws, case_id) / run_id
    (out / "drafts").mkdir(parents=True, exist_ok=True)
    packs = ws.evidence(case_id)
    index = evidence_index(packs)
    drafts: dict[str, SectionDraft] = {}
    per_section: dict[str, dict] = {}
    t_run = time.perf_counter()
    with MemoryMonitor(base_url=ollama_url) as monitor:
        for sec in sections or REPORT_SECTIONS:
            pack = packs.get(sec)
            if pack is None or not pack.items:
                per_section[sec] = {"skipped": "geen bewijsstukken"}
                continue
            if progress:
                progress(f"{label}: {sec}")
            cfg = PipelineConfig(pipeline_id=pipeline, prompt_version=prompt_version, selection=selection, seed=seed,
                                 temperature=temperature, case_label=ws.case_label(case_id),
                                 allow_resident_text_to_hosted=allow_resident_text_to_hosted,
                                 include_quotes=include_quotes)
            t0 = time.perf_counter()
            try:
                draft = run_pipeline(pack, cfg, provider)
            except Exception as exc:  # noqa: BLE001 - a failed section is recorded, never dropped
                per_section[sec] = {"error": f"{type(exc).__name__}: {exc}", "wall_s": round(time.perf_counter() - t0, 2)}
                continue
            wall = time.perf_counter() - t0
            verify_section(draft, pack, index)
            (out / "drafts" / f"{_slug(sec)}.json").write_text(json.dumps(draft.to_dict(), indent=2, ensure_ascii=False),
                                                                encoding="utf-8")
            drafts[sec] = draft
            m = (draft.section_checks or {}).get("metrics", {})
            per_section[sec] = {
                "draft_id": draft.draft_id, "n_claims": len(draft.claims), "errors": draft.errors,
                "grounded_rate": m.get("grounded_rate"), "unsupported_rate": m.get("unsupported_rate"),
                "coverage_salient": m.get("coverage_salient"), "wall_s": round(wall, 2),
                "model_calls": sum(1 for s in draft.stages if s.provider != "none"),
                "tokens_in": sum(s.input_tokens or 0 for s in draft.stages),
                "tokens_out": sum(s.output_tokens or 0 for s in draft.stages),
                "timings": _stage_timings(draft.stages),
            }
        drafting_s = round(time.perf_counter() - t_run, 1)
    resources = monitor.summary()
    claims = [c for d in drafts.values() for c in d.claims]
    statuses = [(c.verification or {}).get("status") for c in claims]
    timings = _stage_timings([s for d in drafts.values() for s in d.stages])
    tokens_in = sum(v.get("tokens_in", 0) for v in per_section.values())
    tokens_out = sum(v.get("tokens_out", 0) for v in per_section.values())
    run = {
        "run_id": run_id, "case_id": case_id, "case_label": ws.case_label(case_id), "created_at": now_iso(),
        "model_id": model_id, "model": provider.model_info.to_dict() if provider else None,
        "pipeline": pipeline, "prompt_version": prompt_version, "selection": selection, "seed": seed,
        "temperature": temperature, "tag": tag, "experiment_id": experiment_id,
        "code_version": git_commit(), "fingerprints": fingerprints(prompt_version),
        "machine": machine_info(),
        "runtime": {"cpu_only": cpu_only, "cold_start": bool(cold and ollama_url), "unloaded_before": unloaded,
                    "num_ctx": getattr(provider, "num_ctx", None), "timeout_s": getattr(provider, "timeout_s", None),
                    "ollama_version": ollama_version(ollama_url) if ollama_url else None},
        "evidence_built_with": next(iter(packs.values())).built_with if packs else {},
        "sections": per_section,
        "totals": {
            "sections_drafted": len(drafts), "n_claims": len(claims),
            "grounded_rate": round(statuses.count("grounded") / len(claims), 3) if claims else None,
            "weak_rate": round(statuses.count("weak") / len(claims), 3) if claims else None,
            "unsupported_rate": round(statuses.count("unsupported") / len(claims), 3) if claims else None,
            "wall_s": drafting_s,
            "model_calls": sum(v.get("model_calls", 0) for v in per_section.values()),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            **({"model_" + k: v for k, v in timings.items()}),
            **({"prompt_tokens_per_s": round(tokens_in / timings["prompt_eval_s"], 1)}
               if timings.get("prompt_eval_s") else {}),
            **({"output_tokens_per_s": round(tokens_out / timings["eval_s"], 1)} if timings.get("eval_s") else {}),
            "model_peak_mb": resources.get("model_peak_mb"),
            "within_memory_limit": resources.get("within_limit"),
            "failed_sections": [s for s, v in per_section.items() if v.get("error") or v.get("errors")],
        },
        "resources": resources,
        "usable": bool(claims),
        "files": {},
    }
    if render and drafts:
        from export.content import from_drafts
        from export.docx_report import render_docx, to_pdf
        t_render = time.perf_counter()
        content = from_drafts(ws, case_id, drafts, {
            "label": f"{label} · {pipeline}", "model": (provider.model_info.model if provider else "geen (template)"),
            "pipeline": pipeline, "prompt_version": prompt_version, "run_id": run_id,
            "source": "onbeoordeeld concept van één model (run)"})
        docx, figures = render_docx(content, out, filename=f"{content.report_kind.lower()}_{case_id}_{label}.docx")
        run["files"]["docx"] = docx.name
        run["figures"] = [f.__dict__ for f in figures]
        if pdf:
            p = to_pdf(docx)
            if p:
                run["files"]["pdf"] = p.name
        run["totals"]["render_s"] = round(time.perf_counter() - t_render, 1)
    (out / "run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")
    return run


def latest_runs(ws, case_id: str) -> dict[str, str]:
    """{model id or 'template': run id of its latest run that produced claims}."""
    out: dict[str, str] = {}
    for r in list_runs(ws, case_id):
        if r.get("usable", r["totals"].get("n_claims", 0) > 0):
            out[r.get("model_id") or TEMPLATE] = r["run_id"]
    return out


def list_runs(ws, case_id: str) -> list[dict]:
    d = runs_dir(ws, case_id)
    out = []
    for p in sorted(d.glob("*/run.json")):
        run = json.loads(p.read_text(encoding="utf-8"))
        ev = p.parent / "evaluation" / "evaluation.json"
        if ev.exists():
            run["evaluation"] = json.loads(ev.read_text(encoding="utf-8")).get("summary")
        out.append(run)
    return out


def load_run(ws, case_id: str, run_id: str) -> tuple[dict, dict[str, SectionDraft]]:
    d = runs_dir(ws, case_id) / run_id
    run = json.loads((d / "run.json").read_text(encoding="utf-8"))
    drafts = {}
    for sec, info in run["sections"].items():
        f = d / "drafts" / f"{_slug(sec)}.json"
        if f.exists():
            drafts[sec] = SectionDraft.from_dict(json.loads(f.read_text(encoding="utf-8")))
    return run, drafts


def run_path(ws, case_id: str, run_id: str) -> Path:
    return runs_dir(ws, case_id) / run_id


def adopt_for_review(ws, case_id: str, run_id: str, reviewer: str) -> list[str]:
    """Make this run's drafts the active review sessions (earlier sessions are archived, NFR11)."""
    _, drafts = load_run(ws, case_id, run_id)
    for sec, draft in drafts.items():
        ws.save_draft(draft)
        current = ws.p("reviews", case_id, f"{ws._section_slug(sec)}.json")   # noqa: SLF001
        if current.exists():
            archive = ws.p("reviews", case_id, "archive", f"{ws._section_slug(sec)}-{draft.created_at.replace(':', '')}.json")  # noqa: SLF001
            archive.parent.mkdir(parents=True, exist_ok=True)
            current.rename(archive)
        ws.save_session(ReviewSession.start(draft, reviewer))
    return list(drafts)
