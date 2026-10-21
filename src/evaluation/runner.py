"""Runs one or more conditions against the frozen gold set, multiple times
each, and scores every repeat -- the harness itself, not a specific model
integration. A "condition" is any callable that turns the gold set into a
Draft, standing in for a model: for now, synthetic stand-ins the tests use
to prove the scoring logic is correct (see tests/run_stdlib_evaluation.py);
later, a thin adapter that calls the actual local model (llama.cpp/Ollama,
per the Resources doc's cost-comparison) and turns its output into Claims
plugs in here without changing this module.

Repeats exist because LLM generation is stochastic: scoring a condition
once and reporting that number as "the" score risks mistaking noise for a
real difference between conditions (arXiv:2509.24086, "Do Repetitions
Matter?"). results_log.py is what turns a list of per-repeat RunResults
into a mean +/- stdev per condition.
"""
from __future__ import annotations

from typing import Callable

from .gold_set import GOLD_SET
from .metrics import score_draft
from .models import Draft, GoldClaim, RunResult

# A condition turns the gold set into a Draft -- the seam where a real
# model call will eventually plug in.
ConditionFn = Callable[[tuple[GoldClaim, ...]], Draft]


def run_condition(name: str, condition_fn: ConditionFn, gold_set: tuple[GoldClaim, ...] = GOLD_SET,
                   repeats: int = 3) -> list[RunResult]:
    """Runs one condition `repeats` times against `gold_set`, scoring each
    repeat independently. Each call to condition_fn must be independent
    (no shared state across repeats) so that variance between repeats
    reflects the model's own stochasticity, not the harness."""
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    results = []
    for i in range(repeats):
        draft = condition_fn(gold_set)
        if draft.condition != name:
            draft.condition = name
        scores = score_draft(draft, gold_set)
        results.append(RunResult(condition=name, repeat_index=i, scores=scores,
                                  n_claims=len(draft), n_gold=len(gold_set)))
    return results


def run_conditions(conditions: dict[str, ConditionFn], gold_set: tuple[GoldClaim, ...] = GOLD_SET,
                    repeats: int = 3) -> list[RunResult]:
    """Runs every named condition (e.g. different models or prompt
    variants) the same number of repeats against the same gold set, so
    results_log.py can compare them on equal footing."""
    all_results: list[RunResult] = []
    for name, fn in conditions.items():
        all_results.extend(run_condition(name, fn, gold_set, repeats))
    return all_results
