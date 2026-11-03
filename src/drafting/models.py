"""The typed output contract every drafting pipeline must produce.

Same contract for template / naive / staged / later variants, so they can be
compared on identical inputs (brief section 12). Evidence is never stored
inside a claim - only evidence ids - so a claim cannot carry facts that are
not traceable to the evidence layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

CLAIM_TYPES = ("bevinding", "interpretatie", "conclusie", "aanbeveling")
CLAIM_TYPE_LABELS = {
    "bevinding": "Bevinding (feit uit de data)",
    "interpretatie": "Interpretatie",
    "conclusie": "Conclusie",
    "aanbeveling": "Aanbeveling",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DraftClaim:
    claim_id: str
    text: str
    claim_type: str                      # one of CLAIM_TYPES
    evidence_ids: list[str] = field(default_factory=list)
    paragraph: int = 1                   # groups claims into narrative paragraphs
    origin: str = "model"                # model | template | human
    stage: Optional[str] = None          # pipeline stage that produced it
    verification: Optional[dict] = None  # filled by verification.verify_section

    def __post_init__(self) -> None:
        if self.claim_type not in CLAIM_TYPES:
            self.claim_type = "interpretatie" if self.claim_type else "bevinding"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DraftClaim":
        return cls(**d)


@dataclass
class StageLog:
    """One model call (or deterministic step) inside a pipeline run."""
    stage: str
    provider: str
    model: str
    prompt_version: Optional[str] = None
    prompt_sha: Optional[str] = None
    latency_s: float = 0.0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    retries: int = 0
    parsed_ok: bool = True
    error: Optional[str] = None
    raw_output: Optional[str] = None     # kept for audit / replay
    evidence_sent: list[str] = field(default_factory=list)
    privacy: Optional[str] = None        # what left the device, if anything
    timings: Optional[dict] = None       # provider-reported: load_s, prompt_eval_s, eval_s, tokens per second

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SectionDraft:
    draft_id: str
    case_id: str
    section: str
    pipeline_id: str
    claims: list[DraftClaim] = field(default_factory=list)
    stages: list[StageLog] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    model: Optional[dict] = None
    evidence_ids_available: list[str] = field(default_factory=list)
    unavailable_inputs: list[str] = field(default_factory=list)
    section_checks: Optional[dict] = None   # coverage gaps, repetition, metrics
    errors: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)

    def narrative(self) -> str:
        paras: dict[int, list[str]] = {}
        for c in self.claims:
            paras.setdefault(c.paragraph, []).append(c.text.strip())
        return "\n\n".join(" ".join(p) for _, p in sorted(paras.items()))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["narrative"] = self.narrative()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SectionDraft":
        d = dict(d)
        d.pop("narrative", None)
        d["claims"] = [DraftClaim.from_dict(c) for c in d.get("claims", [])]
        d["stages"] = [StageLog(**s) for s in d.get("stages", [])]
        return cls(**d)
