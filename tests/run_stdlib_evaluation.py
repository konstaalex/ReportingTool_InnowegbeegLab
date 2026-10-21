"""Dependency-free test runner (unittest + stdlib only) for the evaluation
harness -- no openpyxl/network needed, unlike run_stdlib.py. Mirrors the
pytest suite in test_evaluation.py; keep both in sync when metrics.py or
runner.py change. The pytest file is the one to run once a machine has
real internet access for pytest itself.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evaluation.gold_set import GOLD_SET
from evaluation.models import Claim, Draft, GoldClaim, RunResult
from evaluation.metrics import (
    citation_precision, citation_recall, fabrication_rate,
    misgrounding_rate, faithfulness, injected_error_catch_rate, score_draft,
)
from evaluation.runner import run_condition, run_conditions
from evaluation.results_log import summarize, append_run_log, load_run_log

MINI_GOLD = (
    GoldClaim(id="a", text="claim a", supporting_ids=("s1", "s2")),
    GoldClaim(id="b", text="claim b", supporting_ids=("s3",)),
)


class ModelTests(unittest.TestCase):
    def test_gold_claim_rejects_empty_supporting_ids(self):
        with self.assertRaises(ValueError):
            GoldClaim(id="x", text="t", supporting_ids=())

    def test_draft_len_matches_claim_count(self):
        draft = Draft(condition="c", claims=[Claim(text="t1"), Claim(text="t2")])
        self.assertEqual(len(draft), 2)


class MetricsTests(unittest.TestCase):
    def test_perfect_draft_scores_all_metrics_as_expected(self):
        draft = Draft(condition="perfect", claims=[
            Claim(text="a", cited_ids=("s1", "s2"), gold_id="a"),
            Claim(text="b", cited_ids=("s3",), gold_id="b"),
        ])
        self.assertEqual(faithfulness(draft, MINI_GOLD), 1.0)
        self.assertEqual(citation_precision(draft, MINI_GOLD), 1.0)
        self.assertEqual(citation_recall(draft, MINI_GOLD), 1.0)
        self.assertEqual(fabrication_rate(draft, MINI_GOLD), 0.0)
        self.assertEqual(misgrounding_rate(draft, MINI_GOLD), 0.0)

    def test_fabrication_counts_uncited_claims_not_misgrounding(self):
        draft = Draft(condition="fab", claims=[
            Claim(text="a", cited_ids=(), gold_id="a"),
            Claim(text="b", cited_ids=("s3",), gold_id="b"),
        ])
        self.assertEqual(fabrication_rate(draft, MINI_GOLD), 0.5)
        self.assertEqual(misgrounding_rate(draft, MINI_GOLD), 0.0)
        self.assertEqual(faithfulness(draft, MINI_GOLD), 0.5)
        self.assertEqual(citation_recall(draft, MINI_GOLD), 0.5)  # a scores 0/2, b scores 1/1

    def test_misgrounding_counts_real_but_wrong_citation_not_fabrication(self):
        draft = Draft(condition="mis", claims=[
            Claim(text="a", cited_ids=("s3",), gold_id="a"),  # real id, but it supports "b" not "a"
            Claim(text="b", cited_ids=("s3",), gold_id="b"),
        ])
        self.assertEqual(misgrounding_rate(draft, MINI_GOLD), 0.5)
        self.assertEqual(fabrication_rate(draft, MINI_GOLD), 0.0)
        self.assertEqual(faithfulness(draft, MINI_GOLD), 0.5)
        self.assertEqual(citation_precision(draft, MINI_GOLD), 0.5)  # a scores 0/1, b scores 1/1

    def test_partial_citation_gives_partial_precision_and_recall(self):
        draft = Draft(condition="partial", claims=[
            Claim(text="a", cited_ids=("s1",), gold_id="a"),  # only 1 of 2 needed ids
        ])
        self.assertAlmostEqual(citation_precision(draft, MINI_GOLD), 1.0)   # the one cited id is correct
        self.assertAlmostEqual(citation_recall(draft, MINI_GOLD), 0.5)      # but only half of what's needed
        self.assertEqual(faithfulness(draft, MINI_GOLD), 1.0)               # cited ids are a subset -> not misgrounded

    def test_unmatched_claims_with_unknown_gold_id_are_excluded_not_crashed(self):
        draft = Draft(condition="unknown", claims=[Claim(text="x", cited_ids=("s1",), gold_id="does-not-exist")])
        self.assertEqual(faithfulness(draft, MINI_GOLD), 0.0)
        self.assertEqual(fabrication_rate(draft, MINI_GOLD), 0.0)

    def test_injected_error_caught_when_misgrounded(self):
        draft = Draft(condition="inj", claims=[
            Claim(text="a", cited_ids=("s3",), gold_id="a", injected_error=True),  # real but wrong -> caught
        ])
        self.assertEqual(injected_error_catch_rate(draft, MINI_GOLD), 1.0)

    def test_injected_error_missed_when_it_happens_to_cite_correctly(self):
        draft = Draft(condition="inj2", claims=[
            Claim(text="a", cited_ids=("s1", "s2"), gold_id="a", injected_error=True),  # happens to be correct
        ])
        self.assertEqual(injected_error_catch_rate(draft, MINI_GOLD), 0.0)

    def test_injected_error_catch_rate_is_zero_with_no_injected_claims(self):
        draft = Draft(condition="none", claims=[Claim(text="a", cited_ids=("s1", "s2"), gold_id="a")])
        self.assertEqual(injected_error_catch_rate(draft, MINI_GOLD), 0.0)

    def test_score_draft_returns_all_six_metrics(self):
        draft = Draft(condition="c", claims=[Claim(text="a", cited_ids=("s1", "s2"), gold_id="a")])
        scores = score_draft(draft, MINI_GOLD)
        self.assertEqual(set(scores), {
            "faithfulness", "citation_precision", "citation_recall",
            "fabrication_rate", "misgrounding_rate", "injected_error_catch_rate",
        })


class RunnerTests(unittest.TestCase):
    def test_run_condition_produces_one_result_per_repeat(self):
        def always_perfect(gold_set):
            return Draft(condition="x", claims=[Claim(text=g.text, cited_ids=g.supporting_ids, gold_id=g.id) for g in gold_set])

        results = run_condition("perfect", always_perfect, MINI_GOLD, repeats=4)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r.condition == "perfect" for r in results))
        self.assertTrue(all(r.scores["faithfulness"] == 1.0 for r in results))
        self.assertEqual([r.repeat_index for r in results], [0, 1, 2, 3])

    def test_run_condition_rejects_zero_repeats(self):
        with self.assertRaises(ValueError):
            run_condition("x", lambda gs: Draft(condition="x"), MINI_GOLD, repeats=0)

    def test_run_conditions_covers_every_named_condition(self):
        def empty(gold_set):
            return Draft(condition="e")

        results = run_conditions({"a": empty, "b": empty}, MINI_GOLD, repeats=2)
        conditions = {r.condition for r in results}
        self.assertEqual(conditions, {"a", "b"})
        self.assertEqual(len(results), 4)


class ResultsLogTests(unittest.TestCase):
    def test_summarize_computes_mean_and_stdev_per_condition_per_metric(self):
        results = [
            RunResult(condition="c", repeat_index=0, scores={"faithfulness": 1.0}, n_claims=1, n_gold=2),
            RunResult(condition="c", repeat_index=1, scores={"faithfulness": 0.0}, n_claims=1, n_gold=2),
        ]
        summary = summarize(results)
        self.assertAlmostEqual(summary["c"]["faithfulness"]["mean"], 0.5)
        self.assertGreater(summary["c"]["faithfulness"]["stdev"], 0.0)
        self.assertEqual(summary["c"]["faithfulness"]["n"], 2)

    def test_summarize_stdev_is_zero_not_error_for_single_repeat(self):
        results = [RunResult(condition="c", repeat_index=0, scores={"faithfulness": 1.0}, n_claims=1, n_gold=1)]
        summary = summarize(results)
        self.assertEqual(summary["c"]["faithfulness"]["stdev"], 0.0)
        self.assertEqual(summary["c"]["faithfulness"]["n"], 1)

    def test_append_run_log_never_overwrites_earlier_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "runs.json"
            r1 = [RunResult(condition="c", repeat_index=0, scores={"faithfulness": 1.0}, n_claims=1, n_gold=1)]
            r2 = [RunResult(condition="c", repeat_index=0, scores={"faithfulness": 0.0}, n_claims=1, n_gold=1)]
            append_run_log(log_path, r1, label="first")
            append_run_log(log_path, r2, label="second")
            log = load_run_log(log_path)
            self.assertEqual(len(log), 2)
            self.assertEqual(log[0]["label"], "first")
            self.assertEqual(log[1]["label"], "second")


if __name__ == "__main__":
    unittest.main(verbosity=2)
