"""Pytest mirror of tests/run_stdlib_evaluation.py -- same cases, pytest
style. Run this on a machine with real pytest; run_stdlib_evaluation.py
is the dependency-free fallback for environments without it. Keep both in
sync when metrics.py or runner.py change.
"""
from __future__ import annotations

from pathlib import Path

from evaluation.gold_set import GOLD_SET
from evaluation.models import Claim, Draft, GoldClaim, RunResult
from evaluation.metrics import (
    citation_precision, citation_recall, fabrication_rate,
    misgrounding_rate, faithfulness, injected_error_catch_rate, score_draft,
)
from evaluation.runner import run_condition, run_conditions
from evaluation.results_log import summarize, append_run_log, load_run_log

import pytest

MINI_GOLD = (
    GoldClaim(id="a", text="claim a", supporting_ids=("s1", "s2")),
    GoldClaim(id="b", text="claim b", supporting_ids=("s3",)),
)


def test_gold_claim_rejects_empty_supporting_ids():
    with pytest.raises(ValueError):
        GoldClaim(id="x", text="t", supporting_ids=())


def test_draft_len_matches_claim_count():
    draft = Draft(condition="c", claims=[Claim(text="t1"), Claim(text="t2")])
    assert len(draft) == 2


def test_perfect_draft_scores_all_metrics_as_expected():
    draft = Draft(condition="perfect", claims=[
        Claim(text="a", cited_ids=("s1", "s2"), gold_id="a"),
        Claim(text="b", cited_ids=("s3",), gold_id="b"),
    ])
    assert faithfulness(draft, MINI_GOLD) == 1.0
    assert citation_precision(draft, MINI_GOLD) == 1.0
    assert citation_recall(draft, MINI_GOLD) == 1.0
    assert fabrication_rate(draft, MINI_GOLD) == 0.0
    assert misgrounding_rate(draft, MINI_GOLD) == 0.0


def test_fabrication_counts_uncited_claims_not_misgrounding():
    draft = Draft(condition="fab", claims=[
        Claim(text="a", cited_ids=(), gold_id="a"),
        Claim(text="b", cited_ids=("s3",), gold_id="b"),
    ])
    assert fabrication_rate(draft, MINI_GOLD) == 0.5
    assert misgrounding_rate(draft, MINI_GOLD) == 0.0
    assert faithfulness(draft, MINI_GOLD) == 0.5
    assert citation_recall(draft, MINI_GOLD) == 0.5


def test_misgrounding_counts_real_but_wrong_citation_not_fabrication():
    draft = Draft(condition="mis", claims=[
        Claim(text="a", cited_ids=("s3",), gold_id="a"),
        Claim(text="b", cited_ids=("s3",), gold_id="b"),
    ])
    assert misgrounding_rate(draft, MINI_GOLD) == 0.5
    assert fabrication_rate(draft, MINI_GOLD) == 0.0
    assert faithfulness(draft, MINI_GOLD) == 0.5
    assert citation_precision(draft, MINI_GOLD) == 0.5


def test_partial_citation_gives_partial_precision_and_recall():
    draft = Draft(condition="partial", claims=[Claim(text="a", cited_ids=("s1",), gold_id="a")])
    assert citation_precision(draft, MINI_GOLD) == pytest.approx(1.0)
    assert citation_recall(draft, MINI_GOLD) == pytest.approx(0.5)
    assert faithfulness(draft, MINI_GOLD) == 1.0


def test_unmatched_claims_with_unknown_gold_id_are_excluded_not_crashed():
    draft = Draft(condition="unknown", claims=[Claim(text="x", cited_ids=("s1",), gold_id="does-not-exist")])
    assert faithfulness(draft, MINI_GOLD) == 0.0
    assert fabrication_rate(draft, MINI_GOLD) == 0.0


def test_injected_error_caught_when_misgrounded():
    draft = Draft(condition="inj", claims=[Claim(text="a", cited_ids=("s3",), gold_id="a", injected_error=True)])
    assert injected_error_catch_rate(draft, MINI_GOLD) == 1.0


def test_injected_error_missed_when_it_happens_to_cite_correctly():
    draft = Draft(condition="inj2", claims=[Claim(text="a", cited_ids=("s1", "s2"), gold_id="a", injected_error=True)])
    assert injected_error_catch_rate(draft, MINI_GOLD) == 0.0


def test_injected_error_catch_rate_is_zero_with_no_injected_claims():
    draft = Draft(condition="none", claims=[Claim(text="a", cited_ids=("s1", "s2"), gold_id="a")])
    assert injected_error_catch_rate(draft, MINI_GOLD) == 0.0


def test_score_draft_returns_all_six_metrics():
    draft = Draft(condition="c", claims=[Claim(text="a", cited_ids=("s1", "s2"), gold_id="a")])
    scores = score_draft(draft, MINI_GOLD)
    assert set(scores) == {
        "faithfulness", "citation_precision", "citation_recall",
        "fabrication_rate", "misgrounding_rate", "injected_error_catch_rate",
    }


def test_run_condition_produces_one_result_per_repeat():
    def always_perfect(gold_set):
        return Draft(condition="x", claims=[Claim(text=g.text, cited_ids=g.supporting_ids, gold_id=g.id) for g in gold_set])

    results = run_condition("perfect", always_perfect, MINI_GOLD, repeats=4)
    assert len(results) == 4
    assert all(r.condition == "perfect" for r in results)
    assert all(r.scores["faithfulness"] == 1.0 for r in results)
    assert [r.repeat_index for r in results] == [0, 1, 2, 3]


def test_run_condition_rejects_zero_repeats():
    with pytest.raises(ValueError):
        run_condition("x", lambda gs: Draft(condition="x"), MINI_GOLD, repeats=0)


def test_run_conditions_covers_every_named_condition():
    def empty(gold_set):
        return Draft(condition="e")

    results = run_conditions({"a": empty, "b": empty}, MINI_GOLD, repeats=2)
    assert {r.condition for r in results} == {"a", "b"}
    assert len(results) == 4


def test_summarize_computes_mean_and_stdev_per_condition_per_metric():
    results = [
        RunResult(condition="c", repeat_index=0, scores={"faithfulness": 1.0}, n_claims=1, n_gold=2),
        RunResult(condition="c", repeat_index=1, scores={"faithfulness": 0.0}, n_claims=1, n_gold=2),
    ]
    summary = summarize(results)
    assert summary["c"]["faithfulness"]["mean"] == pytest.approx(0.5)
    assert summary["c"]["faithfulness"]["stdev"] > 0.0
    assert summary["c"]["faithfulness"]["n"] == 2


def test_summarize_stdev_is_zero_not_error_for_single_repeat():
    results = [RunResult(condition="c", repeat_index=0, scores={"faithfulness": 1.0}, n_claims=1, n_gold=1)]
    summary = summarize(results)
    assert summary["c"]["faithfulness"]["stdev"] == 0.0
    assert summary["c"]["faithfulness"]["n"] == 1


def test_append_run_log_never_overwrites_earlier_runs(tmp_path):
    log_path = tmp_path / "runs.json"
    r1 = [RunResult(condition="c", repeat_index=0, scores={"faithfulness": 1.0}, n_claims=1, n_gold=1)]
    r2 = [RunResult(condition="c", repeat_index=0, scores={"faithfulness": 0.0}, n_claims=1, n_gold=1)]
    append_run_log(log_path, r1, label="first")
    append_run_log(log_path, r2, label="second")
    log = load_run_log(log_path)
    assert len(log) == 2
    assert log[0]["label"] == "first"
    assert log[1]["label"] == "second"
