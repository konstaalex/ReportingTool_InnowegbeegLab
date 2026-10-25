"""Drafting pipelines - the experimental variable of SQ3.

All pipelines: (EvidencePack, provider, PipelineConfig) -> SectionDraft.
Same input, same output contract, so experiments compare like with like.

    template  0 model calls. Deterministic claims from the most salient
              evidence. Floor for fluency, ceiling for faithfulness.
    naive     1 call. Whole (selected) pack -> all claims.          (E1)
    staged    1 call per perspective for findings + 1 synthesis call. (E2/E3)

The two non-theme sections have their own single-call prompt whatever the
pipeline (naive or staged), because they have no perspectives to stage:
    Conclusies   cross-theme pack (prompt 'conclusions', from prompt v2)
    Aanleiding   project-context notes (prompt 'aanleiding', from prompt v2)

Verification is NOT part of a pipeline: it always runs afterwards
(verification.checks.verify_section) so every variant is measured the same way.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from evidence.models import AANLEIDING, CONCLUSIES, SECTION_CODES, Evidence, EvidencePack
from llm.base import GenerationRequest, LLMProvider, Message, PrivacyViolation
from llm.privacy import check_request

from .models import CLAIM_TYPES, DraftClaim, SectionDraft, StageLog
from .prompts import prompt_sha, render, system_prompt

PIPELINES = ("template", "naive", "staged")

CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "type": {"type": "string", "enum": list(CLAIM_TYPES)},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "paragraph": {"type": "integer"},
                },
                "required": ["text", "type", "evidence_ids"],
            },
        }
    },
    "required": ["claims"],
}
FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {"findings": CLAIMS_SCHEMA["properties"]["claims"]},
    "required": ["findings"],
}

_TYPE_ALIASES = {"finding": "bevinding", "interpretation": "interpretatie", "conclusion": "conclusie",
                 "recommendation": "aanbeveling", "observatie": "bevinding", "feit": "bevinding"}
_ID_CLEAN = re.compile(r"[\[\]\s()]")


@dataclass
class PipelineConfig:
    pipeline_id: str = "naive"
    prompt_version: str = "v2"
    selection: str = "all"               # "all" | "top_k:<n>"
    temperature: float = 0.2
    seed: Optional[int] = 42
    max_output_tokens: int = 1800
    case_label: str = ""
    allow_resident_text_to_hosted: bool = False
    include_quotes: bool = True          # drop resident quote evidence entirely (e.g. for hosted runs)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


# ------------------------------------------------------------------ helpers

def select_evidence(pack: EvidencePack, cfg: PipelineConfig) -> EvidencePack:
    p = pack
    if not cfg.include_quotes:
        p = p.select([e.evidence_id for e in p.items if e.kind != "quote"])
    if cfg.selection.startswith("top_k:"):
        p = p.top_k(int(cfg.selection.split(":", 1)[1]))
    return p


def _evidence_block(items: list[Evidence]) -> str:
    return "\n".join(e.prompt_line() for e in items) or "(geen)"


def _normalise_claims(raw: list, prefix: str, start: int, stage: str, default_paragraph: int = 1) -> list[DraftClaim]:
    claims = []
    for i, c in enumerate(raw or [], start=start):
        if not isinstance(c, dict) or not str(c.get("text", "")).strip():
            continue
        ctype = str(c.get("type", "bevinding")).strip().lower()
        ctype = _TYPE_ALIASES.get(ctype, ctype)
        ids = c.get("evidence_ids") or []
        if isinstance(ids, str):
            ids = re.split(r"[,;]", ids)
        ids = [_ID_CLEAN.sub("", str(x)) for x in ids if str(x).strip()]
        para = c.get("paragraph")
        claims.append(DraftClaim(
            claim_id=f"{prefix}-{i:02d}", text=" ".join(str(c["text"]).split()), claim_type=ctype,
            evidence_ids=list(dict.fromkeys(ids)), paragraph=int(para) if isinstance(para, int) else default_paragraph,
            origin="model", stage=stage,
        ))
    return claims


def _call(provider: LLMProvider, cfg: PipelineConfig, task: str, user: str, schema: dict,
          items: list[Evidence], draft: SectionDraft) -> Optional[dict]:
    req = GenerationRequest(
        task=task,
        messages=[Message("system", system_prompt(cfg.prompt_version)), Message("user", user)],
        temperature=cfg.temperature, max_output_tokens=cfg.max_output_tokens, json_schema=schema, seed=cfg.seed,
        contains_resident_free_text=any(e.contains_free_text for e in items),
    )
    info = provider.model_info
    log = StageLog(stage=task, provider=info.provider, model=info.model, prompt_version=cfg.prompt_version,
                   prompt_sha=req.prompt_sha(), evidence_sent=[e.evidence_id for e in items])
    try:
        log.privacy = check_request(req, info, cfg.allow_resident_text_to_hosted)
    except PrivacyViolation as exc:
        log.parsed_ok, log.error = False, str(exc)
        draft.stages.append(log)
        draft.errors.append(f"{task}: {exc}")
        return None
    res = provider.generate(req)
    log.latency_s, log.input_tokens, log.output_tokens = res.latency_s, res.input_tokens, res.output_tokens
    log.retries, log.raw_output = res.retries, res.text
    if res.error or not isinstance(res.parsed, dict):
        log.parsed_ok = False
        log.error = res.error or "no JSON object in output"
        draft.errors.append(f"{task}: {log.error}")
        draft.stages.append(log)
        return None
    draft.stages.append(log)
    return res.parsed


def _new_draft(pack: EvidencePack, cfg: PipelineConfig, provider: Optional[LLMProvider]) -> SectionDraft:
    return SectionDraft(
        draft_id=uuid.uuid4().hex[:10], case_id=pack.case_id, section=pack.section, pipeline_id=cfg.pipeline_id,
        model=provider.model_info.to_dict() if provider else None,
        evidence_ids_available=pack.ids(), unavailable_inputs=list(pack.unavailable), config=cfg.to_dict(),
    )


def _prefix(section: str) -> str:
    return SECTION_CODES.get(section, "AL")


# ------------------------------------------------------------------ pipelines

def _theme_picks(items: list[Evidence]) -> list[tuple[Evidence, str, int]]:
    """House-style order (as in the Oss report): 1 data/maps, 2 experts, 3 residents, 4 synthesis."""
    picks: list[tuple[Evidence, str, int]] = []
    data = [e for e in items if e.perspective == "data"]
    rule = [e for e in data if e.kind == "map_measurement" and e.data.get("tests_rule") and e.data.get("area") == "onderzoeksgebied"]
    picks += [(e, "bevinding", 1) for e in (rule[:2] or data[:1])]
    exp = [e for e in items if e.perspective == "expert" and e.kind != "note"]
    avg = next((e for e in exp if e.kind == "theme_average"), None)
    if avg:
        picks.append((avg, "bevinding", 2))
    stm = sorted([e for e in exp if e.kind == "expert_statement"], key=lambda e: -e.salience)[:3]
    picks += [(e, "bevinding", 2) for e in stm]
    notes = sorted([e for e in items if e.kind == "note"], key=lambda e: -e.salience)[:2]
    picks += [(e, "bevinding", 2 if e.perspective == "expert" else 3) for e in notes]
    res = [e for e in items if e.perspective == "bewoners" and e.kind not in ("quote", "note")]
    picks += [(e, "bevinding", 3) for e in sorted(res, key=lambda e: -e.salience)[:3]]
    quotes = sorted([e for e in items if e.kind == "quote" and e.data.get("count", 1) >= 2], key=lambda e: -e.salience)[:2]
    picks += [(e, "bevinding", 3) for e in quotes]
    picks += [(e, "interpretatie", 4) for e in items if e.kind == "comparison"]
    return picks


def _conclusion_picks(items: list[Evidence]) -> list[tuple[Evidence, str, int]]:
    cross = [e for e in items if e.kind == "cross_theme"]
    rankings = [e for e in cross if "means" in e.data]
    rules = [e for e in cross if "rules" in e.data and "term" not in e.data]
    terms = [e for e in cross if "term" in e.data][:4]
    return ([(e, "bevinding", 1) for e in rankings] + [(e, "bevinding", 2) for e in rules]
            + [(e, "bevinding", 3) for e in terms])


def template_pipeline(pack: EvidencePack, cfg: PipelineConfig) -> SectionDraft:
    draft = _new_draft(pack, cfg, None)
    t0 = time.perf_counter()
    items = pack.items
    if pack.section == CONCLUSIES:
        picks = _conclusion_picks(items)
    elif pack.section == AANLEIDING:
        picks = [(e, "bevinding", 1) for e in items if e.kind == "note"]
    else:
        picks = _theme_picks(items)
    prefix = _prefix(pack.section)
    for i, (e, ctype, para) in enumerate(picks, start=1):
        text = e.data.get("text") if (pack.section == AANLEIDING and e.data.get("text")) else e.statement.split(" Let op:")[0]
        draft.claims.append(DraftClaim(claim_id=f"{prefix}-{i:02d}", text=text, claim_type=ctype,
                                       evidence_ids=[e.evidence_id], paragraph=para, origin="template",
                                       stage="template"))
    draft.stages.append(StageLog(stage="template", provider="none", model="none",
                                 latency_s=round(time.perf_counter() - t0, 4),
                                 evidence_sent=[e.evidence_id for e, _, _ in picks], privacy="no model call"))
    return draft


def naive_pipeline(pack: EvidencePack, provider: LLMProvider, cfg: PipelineConfig) -> SectionDraft:
    draft = _new_draft(pack, cfg, provider)
    sel = select_evidence(pack, cfg)
    user = render(cfg.prompt_version, "naive_section", section=pack.section, case_label=cfg.case_label or pack.case_id,
                  unavailable="\n".join(f"- {u}" for u in pack.unavailable) or "- (niets)",
                  evidence=_evidence_block(sel.items))
    parsed = _call(provider, cfg, "naive_section", user, CLAIMS_SCHEMA, sel.items, draft)
    if parsed:
        draft.claims = _normalise_claims(parsed.get("claims", []), _prefix(pack.section), 1, "naive_section")
    return draft


STAGED_GROUPS = (("data", ("data",)), ("expert", ("expert",)), ("bewoners", ("bewoners",)))


def staged_pipeline(pack: EvidencePack, provider: LLMProvider, cfg: PipelineConfig) -> SectionDraft:
    draft = _new_draft(pack, cfg, provider)
    sel = select_evidence(pack, cfg)
    prefix = _prefix(pack.section)
    findings: list[DraftClaim] = []
    for para, (label, persp) in enumerate(STAGED_GROUPS, start=1):
        items = [e for e in sel.items if e.perspective in persp]
        if not items:
            continue
        user = render(cfg.prompt_version, "staged_findings", section=pack.section,
                      case_label=cfg.case_label or pack.case_id, perspective=label, evidence=_evidence_block(items))
        parsed = _call(provider, cfg, f"staged_findings_{label}", user, FINDINGS_SCHEMA, items, draft)
        if parsed:
            new = _normalise_claims(parsed.get("findings", []), prefix, len(findings) + 1,
                                    f"staged_findings_{label}", default_paragraph=para)
            for c in new:
                c.paragraph = para   # citations are left exactly as the model gave them; the verifier judges them
            findings.extend(new)
    synth_items = [e for e in sel.items if e.perspective in ("cross", "reference", "context")]
    finding_lines = "\n".join(f"- {c.text} [{', '.join(c.evidence_ids)}]" for c in findings) or "(geen)"
    user = render(cfg.prompt_version, "staged_synthesis", section=pack.section,
                  case_label=cfg.case_label or pack.case_id, findings=finding_lines,
                  evidence=_evidence_block(synth_items))
    parsed = _call(provider, cfg, "staged_synthesis", user, CLAIMS_SCHEMA,
                   synth_items + [e for e in sel.items if any(e.evidence_id in c.evidence_ids for c in findings)], draft)
    if parsed:
        synth = _normalise_claims(parsed.get("claims", []), prefix, len(findings) + 1, "staged_synthesis",
                                  default_paragraph=len(STAGED_GROUPS) + 1)
        for c in synth:
            c.paragraph = len(STAGED_GROUPS) + 1
        findings.extend(synth)
    draft.claims = findings
    return draft


SINGLE_CALL_SECTIONS = {CONCLUSIES: "conclusions", AANLEIDING: "aanleiding"}


def single_call_pipeline(pack: EvidencePack, provider: LLMProvider, cfg: PipelineConfig, prompt: str) -> SectionDraft:
    """Conclusies / Aanleiding: one call with a section-specific prompt."""
    from .prompts import PROMPTS_DIR
    draft = _new_draft(pack, cfg, provider)
    if not (PROMPTS_DIR / cfg.prompt_version / f"{prompt}.txt").exists():
        draft.errors.append(f"Promptversie {cfg.prompt_version} heeft geen prompt '{prompt}' voor '{pack.section}' "
                            f"(gebruik v2 of later).")
        return draft
    sel = select_evidence(pack, cfg)
    user = render(cfg.prompt_version, prompt, case_label=cfg.case_label or pack.case_id,
                  unavailable="\n".join(f"- {u}" for u in pack.unavailable) or "- (niets)",
                  evidence=_evidence_block(sel.items))
    parsed = _call(provider, cfg, prompt, user, CLAIMS_SCHEMA, sel.items, draft)
    if parsed:
        draft.claims = _normalise_claims(parsed.get("claims", []), _prefix(pack.section), 1, prompt)
    return draft


def run_pipeline(pack: EvidencePack, cfg: PipelineConfig, provider: Optional[LLMProvider] = None) -> SectionDraft:
    if not pack.items:
        d = _new_draft(pack, cfg, provider)
        hint = (" Voeg notities van de soort 'Projectcontext' toe (Onderzoekersnotities)." if pack.section == AANLEIDING else "")
        d.errors.append(f"Geen bewijsstukken voor '{pack.section}': er wordt niets gegenereerd (UC2 alternate flow).{hint}")
        return d
    if cfg.pipeline_id == "template":
        return template_pipeline(pack, cfg)
    if provider is None:
        raise ValueError(f"pipeline {cfg.pipeline_id!r} needs a model provider")
    if pack.section in SINGLE_CALL_SECTIONS:
        return single_call_pipeline(pack, provider, cfg, SINGLE_CALL_SECTIONS[pack.section])
    if cfg.pipeline_id == "naive":
        return naive_pipeline(pack, provider, cfg)
    if cfg.pipeline_id == "staged":
        return staged_pipeline(pack, provider, cfg)
    raise ValueError(f"unknown pipeline {cfg.pipeline_id!r}; known: {PIPELINES}")
