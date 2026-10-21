"""Metrics scoring a Draft against the frozen gold set.

These are structural proxies, not NLP/LLM judges: with no UC2 draft
generator built yet, there is no free-text model output to run RAGAS/
FActScore-style claim decomposition over. What CAN be tested now is the
harness's own logic -- given a claim and the SourceRecord ids it cites, is
that citation set correct? -- using the same id-overlap arithmetic the
literature in the Resources doc uses once free text enters the picture
(ALCE's citation precision/recall; RAGAS/FActScore's "supported claims /
total claims" for faithfulness; AttributionBench/PaperTrail's injected-
error catch rate). TODO once UC2 exists: add an LLM-judge layer that maps
free-text claims to gold claims and to specific SourceRecord ids before
these functions run -- the arithmetic below does not change, only the
column that is currently supplied for free (Claim.gold_id, Claim.cited_ids)
would need to come from that extraction step instead.
"""
from __future__ import annotations

from .gold_set import GOLD_SET
from .models import Claim, Draft, GoldClaim


def _gold_by_id(gold_set: tuple[GoldClaim, ...] = GOLD_SET) -> dict[str, GoldClaim]:
    return {g.id: g for g in gold_set}


def _matched_claims(draft: Draft, gold_set: tuple[GoldClaim, ...] = GOLD_SET) -> list[tuple[Claim, GoldClaim]]:
    """Claims the harness can score: those naming a gold_id that exists in
    the gold set. A claim with no gold_id (or an unknown one) cannot be
    scored and is excluded here -- callers that need to penalise
    unscoreable output should compare len(draft.claims) to this length."""
    by_id = _gold_by_id(gold_set)
    out: list[tuple[Claim, GoldClaim]] = []
    for claim in draft.claims:
        gold = by_id.get(claim.gold_id) if claim.gold_id else None
        if gold is not None:
            out.append((claim, gold))
    return out


def citation_precision(draft: Draft, gold_set: tuple[GoldClaim, ...] = GOLD_SET) -> float:
    """Of the SourceRecord ids a claim cites, what fraction actually
    support it (ALCE-style). Averaged per claim over claims that cited at
    least one id; a claim with zero citations contributes nothing here --
    it is penalised by fabrication_rate instead, not double-counted."""
    scored = []
    for claim, gold in _matched_claims(draft, gold_set):
        if not claim.cited_ids:
            continue
        correct = len(set(claim.cited_ids) & set(gold.supporting_ids))
        scored.append(correct / len(claim.cited_ids))
    return sum(scored) / len(scored) if scored else 0.0


def citation_recall(draft: Draft, gold_set: tuple[GoldClaim, ...] = GOLD_SET) -> float:
    """Of the SourceRecord ids that actually support a claim, what
    fraction did the claim cite (ALCE-style). Averaged over every matched
    claim, including ones with zero citations (recall 0.0) -- unlike
    precision, recall must charge for a claim that cited nothing."""
    scored = []
    for claim, gold in _matched_claims(draft, gold_set):
        correct = len(set(claim.cited_ids) & set(gold.supporting_ids))
        scored.append(correct / len(gold.supporting_ids))
    return sum(scored) / len(scored) if scored else 0.0


def fabrication_rate(draft: Draft, gold_set: tuple[GoldClaim, ...] = GOLD_SET) -> float:
    """Fraction of matched claims asserted with NO citation at all -- a
    claim that cannot be verified because it points nowhere (distinct
    from misgrounding_rate, which points somewhere real but wrong)."""
    matched = _matched_claims(draft, gold_set)
    if not matched:
        return 0.0
    uncited = sum(1 for claim, _ in matched if not claim.cited_ids)
    return uncited / len(matched)


def misgrounding_rate(draft: Draft, gold_set: tuple[GoldClaim, ...] = GOLD_SET) -> float:
    """Fraction of matched claims that cite at least one real SourceRecord
    id, but NONE of the ids actually support the claim -- the Stanford
    RegLab "real but wrong" failure mode, distinct from fabrication."""
    matched = _matched_claims(draft, gold_set)
    if not matched:
        return 0.0
    misgrounded = 0
    for claim, gold in matched:
        if claim.cited_ids and not (set(claim.cited_ids) & set(gold.supporting_ids)):
            misgrounded += 1
    return misgrounded / len(matched)


def faithfulness(draft: Draft, gold_set: tuple[GoldClaim, ...] = GOLD_SET) -> float:
    """Fraction of matched claims that are fully supported: at least one
    citation, and every cited id actually in the claim's supporting set
    (RAGAS/FActScore "supported claims / total claims", at the id-overlap
    level rather than the natural-language-entailment level)."""
    matched = _matched_claims(draft, gold_set)
    if not matched:
        return 0.0
    supported = 0
    for claim, gold in matched:
        if claim.cited_ids and set(claim.cited_ids) <= set(gold.supporting_ids):
            supported += 1
    return supported / len(matched)


def injected_error_catch_rate(draft: Draft, gold_set: tuple[GoldClaim, ...] = GOLD_SET) -> float:
    """Of the claims deliberately marked injected_error=True (a
    PaperTrail/AttributionBench-style planted mistake -- a citation that
    looks plausible but does not actually support the claim), what
    fraction does faithfulness correctly reject? This is the harness's
    placeholder for UC3's eventual verification step: until a real
    verifier exists, this checks whether citation-overlap arithmetic alone
    already catches a planted error, which sets the floor UC3 must beat."""
    injected = [(c, g) for c, g in _matched_claims(draft, gold_set) if c.injected_error]
    if not injected:
        return 0.0
    caught = sum(1 for c, g in injected if not (c.cited_ids and set(c.cited_ids) <= set(g.supporting_ids)))
    return caught / len(injected)


def score_draft(draft: Draft, gold_set: tuple[GoldClaim, ...] = GOLD_SET) -> dict[str, float]:
    """All metrics at once -- the shape runner.py stores per RunResult."""
    return {
        "faithfulness": faithfulness(draft, gold_set),
        "citation_precision": citation_precision(draft, gold_set),
        "citation_recall": citation_recall(draft, gold_set),
        "fabrication_rate": fabrication_rate(draft, gold_set),
        "misgrounding_rate": misgrounding_rate(draft, gold_set),
        "injected_error_catch_rate": injected_error_catch_rate(draft, gold_set),
    }
