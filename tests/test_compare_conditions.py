"""Comparison of counted runs (pre-registered rules, sections 3 and 4). Synthetic records only."""
from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "compare_conditions", Path(__file__).resolve().parents[1] / "scripts" / "compare_conditions.py")
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

CASE, COMMIT = "testwijk", "abc1234"
# section, label, evidence label, numbers in the original, numbers in the draft, numbers differ
LABELS = [["Sporten", "covered", "covered", "3 5", "3", "False"], ["Sporten", "missing", "partial", "7", "8", "True"],
          ["Sporten", "covered", "missing", "", "", "False"], ["conclusies", "partial", "covered", "", "", "False"],
          ["aanbevelingen", "covered", "covered", "9", "9", "False"]]
OK = [["grounded", "unsupported"], ["grounded", "weak"]]


def write_run(ws: Path, exp: str, seed: int, statuses=OK, labels=LABELS, code=COMMIT, tag=None, cpu=True, name="",
              checksums=None, minutes=60):
    """statuses: the claim statuses of the sections Sporten and Algemeen."""
    rid = f"{exp}-{seed}{name}"
    d = ws / "reports" / CASE / rid
    (d / "drafts").mkdir(parents=True)
    (d / "evaluation").mkdir()
    (ws / "runs").mkdir(exist_ok=True)
    for sec, st in zip(("Sporten", "Algemeen"), statuses):
        (d / "drafts" / f"{sec}.json").write_text(json.dumps(
            {"section": sec, "claims": [{"text": "geheim", "verification": {"status": s}} for s in st]}), encoding="utf-8")
    with open(d / "evaluation" / "alignment.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["section", "label", "evidence_label", "numbers_reference", "numbers_generated", "numbers_differ"])
        w.writerows(labels)
    (ws / "runs" / f"{rid}.json").write_text(json.dumps({
        "run_id": rid, "experiment": {"id": exp}, "tag": tag, "code_version": code,
        "runtime": {"cpu_only": cpu, "cold_start": True}, "fingerprints": {"prompts": exp, "models": "m"},
        "dataset_checksums": checksums or {"a": "1"},
        "results": [{"case_id": CASE, "seed": seed, "report_run_id": rid, "failed_sections": [],
                     "totals": {"wall_s": 60 * minutes, "model_peak_mb": 8192}}]}), encoding="utf-8")


class TestLoading(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp())

    def load(self, conditions=("E1",), seeds=(42,)):
        return cc.load_runs(self.ws, CASE, conditions, seeds, COMMIT, None)

    def test_measures_from_a_run(self):
        write_run(self.ws, "E1", 42)
        runs = self.load()["E1"]
        self.assertEqual(cc.rate(cc.pooled_claims(runs)), 0.25)
        self.assertEqual(cc.rate(cc.pooled_claims(runs, {"Sporten"})), 0.5)
        ps = cc.pooled_statements(runs)
        self.assertEqual(cc.s_rate(ps), 0.5)                   # (1 + 0 + 0.5) / 3 supportable; recommendations left out
        self.assertEqual(cc.s_rate(cc.pooled_statements(runs, {"conclusies"})), 0.5)
        self.assertAlmostEqual(cc.found_rate(ps), 1 / 3)       # 3 of {3, 5, 7}
        self.assertEqual(cc.conflicts(ps), 1)

    def test_seeds_are_pooled_not_averaged(self):
        write_run(self.ws, "E1", 42, [["unsupported"], []])
        write_run(self.ws, "E1", 43, [["grounded"] * 3, []], [[r[0], "missing", *r[2:]] for r in LABELS])
        runs = self.load(seeds=(42, 43))["E1"]
        self.assertEqual(cc.rate(cc.pooled_claims(runs)), 0.25)               # not (100% + 0%) / 2
        self.assertEqual(cc.s_rate(cc.pooled_statements(runs)), 0.25)

    def test_runs_that_do_not_count(self):
        write_run(self.ws, "E1", 42, tag="pilot")
        write_run(self.ws, "E1", 42, code="0000000", name="-old")
        with self.assertRaisesRegex(cc.NotComparable, "0 runs"):
            self.load()
        write_run(self.ws, "E1", 42, name="-ok")
        self.assertEqual(self.load()["E1"][42]["run_id"], "E1-42-ok")
        write_run(self.ws, "E1", 42, name="-again")                           # never picked silently
        with self.assertRaisesRegex(cc.NotComparable, "2 runs"):
            self.load()

    def test_dirty_or_gpu_runs_are_refused(self):
        write_run(self.ws, "E1", 42, code=COMMIT + "-dirty")
        with self.assertRaisesRegex(cc.NotComparable, "dirty"):
            self.load()
        write_run(self.ws, "E2", 42, cpu=False)
        with self.assertRaisesRegex(cc.NotComparable, "CPU-only"):
            self.load(("E2",))

    def test_other_inputs_or_ceiling_are_refused(self):
        write_run(self.ws, "E1", 42)
        write_run(self.ws, "E2", 42, checksums={"a": "2"})
        with self.assertRaisesRegex(cc.NotComparable, "source files"):
            self.load(("E1", "E2"))
        write_run(self.ws, "E4", 42, labels=LABELS[1:])
        with self.assertRaisesRegex(cc.NotComparable, "ceiling"):
            self.load(("E1", "E4"))

    def test_report_is_repeatable_and_has_no_text(self):
        for exp in cc.CONDITIONS:
            for seed in cc.SEEDS:
                write_run(self.ws, exp, seed)
        data = self.load(cc.CONDITIONS, cc.SEEDS)
        md = cc.render(data, "t")
        self.assertEqual(md, cc.render(data, "t"))
        for part in ("six themes", "**winner: E1**", "not the final verdict", "| E4 − E2 (H3) | all | U | 25.0 | 25.0 |"):
            self.assertIn(part, md)
        self.assertNotIn("geheim", md)


class TestRules(unittest.TestCase):
    def test_verdicts(self):
        v = cc.verdict
        self.assertEqual(v(-0.08, (-0.12, -0.04), True), "better")
        self.assertEqual(v(0.08, (0.04, 0.12), True), "worse")
        self.assertEqual(v(0.08, (0.04, 0.12), False), "better")
        self.assertEqual(v(0.05, (0.01, 0.09), False), "better")            # exactly the MMD
        self.assertEqual(v(0.04, (0.01, 0.07), False), "inconclusive")      # excludes 0, smaller than the MMD
        self.assertEqual(v(0.02, (0.01, 0.04), False), "no meaningful difference")
        self.assertEqual(v(0.08, (-0.01, 0.17), False), "inconclusive")

    def test_bootstrap_is_fixed_and_close_to_theory(self):
        x, y = {"a": [1] * 10 + [0] * 90, "b": [1] * 5 + [0] * 45}, {"a": [1] * 40 + [0] * 60, "b": [1] * 20 + [0] * 30}
        ci = cc.bootstrap_u(x, y)
        self.assertEqual(ci, cc.bootstrap_u(x, y))
        se = (0.1 * 0.9 / 150 + 0.4 * 0.6 / 150) ** 0.5                     # normal approximation
        self.assertAlmostEqual(ci[0], -0.3 - 1.96 * se, delta=0.02)
        self.assertAlmostEqual(ci[1], -0.3 + 1.96 * se, delta=0.02)

    def test_statements_are_paired(self):
        x = {"a": [(1.0, 1, 0, 0, 0), (0.0, 1, 0, 0, 0)] * 10}
        self.assertEqual(cc.bootstrap_statements(x, x, cc.s_rate), (0.0, 0.0))

    def test_winner(self):
        def win(**k):
            return cc.stage1_winner({c: dict(zip(("U", "S", "minutes"), v)) for c, v in k.items()})["winner"]
        self.assertEqual(win(E1=(0.20, 0.40, 70), E2=(0.10, 0.25, 90), E4=(0.06, 0.24, 130)), "E2")  # E1 out on U
        self.assertEqual(win(E1=(0.10, 0.20, 70), E2=(0.10, 0.30, 90), E4=(0.06, 0.31, 130)), "E2")  # tie: the faster
        self.assertEqual(win(E1=(0.10, 0.20, 70), E2=(0.12, 0.30, 90), E4=(0.06, 0.31, 130)), "E4")  # E2 out on U
        self.assertEqual(win(E1=(0.10, 0.28, 70), E2=(0.10, 0.30, 90), E4=(0.09, 0.31, 130)), "E1")


if __name__ == "__main__":
    unittest.main()
