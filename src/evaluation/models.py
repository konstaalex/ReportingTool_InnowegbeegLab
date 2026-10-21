"""The addressable units the evaluation harness scores against.

A UC2 draft (once built) will produce sentences, each citing one or more
SourceRecord ids from the ingestion store (UC1). Until UC2 exists, this
module defines the same shape as a *gold* fixture -- a GoldClaim says what
a correct, fully-cited claim should look like for a given slice of the
real data -- so the metrics in metrics.py can be written and tested now,
against synthetic claims, and swapped onto real model output later without
changing their contract. See the "Evaluation Approach - Supporting
Resources" doc for the literature (RAGAS/FActScore, ALCE, PaperTrail,
AttributionBench) this shape is modelled on.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GoldClaim:
    """One ground-truth fact: a claim a correct draft should be able to
    make, and exactly which SourceRecord id(s) actually support it."""
    id: str
    text: str
    supporting_ids: tuple[str, ...]
    theme: str | None = None

    def __post_init__(self) -> None:
        if not self.supporting_ids:
            raise ValueError(f"GoldClaim {self.id!r} has no supporting_ids -- a gold claim must be groundable")


@dataclass
class Claim:
    """One claim as produced by a model under test: free text plus the
    SourceRecord ids it cites. `cited_ids=()` means the model asserted
    something with no citation at all -- that can never be verified
    (UC3), which is exactly what fabrication_rate in metrics.py counts."""
    text: str
    cited_ids: tuple[str, ...] = ()
    gold_id: str | None = None  # which GoldClaim this was meant to answer, if known
    injected_error: bool = False  # True when deliberately corrupted, for catch-rate testing


@dataclass
class Draft:
    """A full model output for one gold set: the ordered list of claims it
    produced, under one named condition (a model, a prompt variant, ...)."""
    condition: str
    claims: list[Claim] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.claims)


@dataclass
class RunResult:
    """One scored run of one condition against the gold set -- the atomic
    unit results_log.py aggregates over repeats (per "Do Repetitions
    Matter?", arXiv:2509.24086)."""
    condition: str
    repeat_index: int
    scores: dict[str, float]
    n_claims: int
    n_gold: int
