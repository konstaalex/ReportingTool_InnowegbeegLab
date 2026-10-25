"""Full report per model (reports.runs), the Word renderer and charts, and the comparison
with an original report (reports.evaluate, reports.judge). Synthetic data only."""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from buurtscan_fixtures import has_docx, write_case_folder  # noqa: E402
from app.services import Workspace  # noqa: E402
from evidence.models import Evidence  # noqa: E402
from llm.base import Capabilities, ModelInfo, PrivacyViolation  # noqa: E402
from llm.providers import ReplayProvider  # noqa: E402
from llm.registry import selected_models  # noqa: E402

CASE = "testwijk-teststad"


def _ws() -> Workspace:
    root = write_case_folder(Path(tempfile.mkdtemp()) / "data", with_docx=has_docx())
    ws = Workspace(Path(tempfile.mkdtemp()) / "ws")
    ws.ingest(str(root))
    return ws


def _ev(eid, group, option, k, n, q="q_sport"):
    return Evidence(eid, CASE, ["Sporten"], "bewoners", "option_share", f"{group}: {option} {k} van {n}",
                    data={"question_id": q, "group": group, "option": option, "k": k, "n": n})


def _has_matplotlib() -> bool:
    try:
        import matplotlib  # noqa: F401
        return True
    except ImportError:
        return False


class TestSelection(unittest.TestCase):
    def test_three_drafting_models_selected(self):
        sel = selected_models("drafting")
        self.assertEqual(len(sel), 3)
        self.assertNotIn("fake", sel)


class TestCharts(unittest.TestCase):
    SPEC = {"id": "t", "question": "q_sport", "title": "Genoeg sportplekken in {focus}?", "measure": "count",
            "options": [["Ja", "Ja"], ["Nee", "Nee"], ["Weet ik niet", "Weet niet"]]}

    def test_values_come_from_evidence(self):
        from export.charts import chart_data
        ev = [_ev("B-SP-01", "A", "Ja", 3, 10), _ev("B-SP-02", "A", "Nee", 7, 10), _ev("B-SP-03", "B", "1 - Ja", 2, 4),
              _ev("B-SP-09", "A", "Ja", 99, 99, q="other_question")]
        d = chart_data(self.SPEC, ev, {"B": "Groep B"}, {"focus": "de wijk"})
        self.assertEqual(d.title, "Genoeg sportplekken in de wijk?")
        self.assertEqual(d.categories, ["Ja", "Nee", "Weet niet"])
        self.assertEqual(d.series[0], ("Groep B", [2, 0, 0]))          # configured groups first, label mapped
        self.assertEqual(d.series[1], ("A", [3, 7, 0]))
        self.assertEqual(d.missing_options, ["Weet ik niet"])
        self.assertEqual(sorted(d.evidence_ids), ["B-SP-01", "B-SP-02", "B-SP-03"])
        pct = chart_data({**self.SPEC, "measure": "pct"}, ev)
        self.assertEqual(pct.series[0][1][:2], [30, 70])

    @unittest.skipUnless(_has_matplotlib(), "matplotlib not installed")
    def test_render_png(self):
        from export.charts import chart_data, render_chart
        d = chart_data(self.SPEC, [_ev("B-SP-01", "A", "Ja", 3, 10), _ev("B-SP-02", "A", "Nee", 7, 10)])
        out = render_chart(d, Path(tempfile.mkdtemp()) / "c.png")
        self.assertTrue(out.is_file())
        self.assertEqual(out.read_bytes()[:4], b"\x89PNG")


class TestRunsAndRenderer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from reports.runs import generate_report_run
        cls.ws = _ws()
        cls.tpl = generate_report_run(cls.ws, CASE, "template")
        cls.fake = generate_report_run(cls.ws, CASE, "fake", pipeline="naive")

    def test_template_run_renders_word_report(self):
        from docx import Document
        run = self.tpl
        self.assertEqual(run["pipeline"], "template")
        self.assertIsNone(run["model_id"])
        self.assertGreater(run["totals"]["n_claims"], 0)
        self.assertEqual(run["totals"]["unsupported_rate"], 0.0)
        path = self.ws.report_run_dir(CASE, run["run_id"]) / run["files"]["docx"]
        self.assertTrue(path.is_file())
        heads = [p.text for p in Document(str(path)).paragraphs if p.style.name.startswith("Heading")]
        for h in ("Samenvatting", "Methode", "Resultaten", "Conclusies", "Aanbevelingen"):
            self.assertIn(h, heads)
        self.assertIn("Thema 1 - Sporten", heads)
        self.assertTrue(all("chapter" in f for f in run["figures"]))

    def test_model_run_is_kept_apart_from_review(self):
        from reports.runs import latest_runs, list_runs, load_run
        self.assertEqual(self.fake["model_id"], "fake")
        self.assertGreater(self.fake["totals"]["model_calls"], 0)
        self.assertEqual(self.ws.sessions(CASE), {})            # runs never touch the review sessions
        latest = latest_runs(self.ws, CASE)
        self.assertEqual(set(latest), {"template", "fake"})
        self.assertEqual(len(list_runs(self.ws, CASE)), 2)
        run, drafts = load_run(self.ws, CASE, self.fake["run_id"])
        self.assertTrue(drafts)
        self.assertTrue(all((d.model or {}).get("model_id") == "fake" for d in drafts.values()))

    def test_adopt_for_review(self):
        from reports.runs import adopt_for_review
        ws = _ws()
        from reports.runs import generate_report_run
        run = generate_report_run(ws, CASE, "template", render=False)
        secs = adopt_for_review(ws, CASE, run["run_id"], reviewer="tester")
        self.assertIn("Sporten", secs)
        self.assertEqual(set(ws.sessions(CASE)), set(secs))
        adopt_for_review(ws, CASE, run["run_id"], reviewer="tester")     # again: earlier sessions archived
        self.assertTrue(list(ws.p("reviews", CASE, "archive").glob("*.json")))

    def test_unreachable_local_model_fails_before_the_run(self):
        from llm.base import ProviderError
        from reports.runs import generate_report_run, list_runs
        before = len(list_runs(self.ws, CASE))
        import llm.providers as prov
        orig = prov.ollama_pulled_models

        def down(url="http://localhost:11434"):
            raise ProviderError("Ollama is not reachable")
        prov.ollama_pulled_models = down
        try:
            with self.assertRaises(ProviderError):
                generate_report_run(self.ws, CASE, "local_qwen7b")
        finally:
            prov.ollama_pulled_models = orig
        self.assertEqual(len(list_runs(self.ws, CASE)), before)


REFERENCE = {
    "path": "original.pdf", "pages": 3,
    "sections": {
        "samenvatting": "De wijk scoort laag op sporten volgens experts en bewoners samen.",
        "Sporten": ("De experts geven het thema sporten gemiddeld een 2,3 op een schaal van 1 tot 5. "
                    "Bewoners noemen de jeu de boulesbaan aan de Lindelaan als verouderd en onveilig. "
                    "Er zijn 12 sportplekken in de buurt volgens de kaart van de gemeente."),
        "aanbevelingen": "Plaats een nieuwe jeu de boulesbaan met bankjes en verlichting bij het park.",
    },
    "figures": [{"kind": "Afbeelding", "number": 1, "title": "Sport- en speelkaart onderzoeksgebied", "chapter": "Sporten"},
                {"kind": "Figuur", "number": 1, "title": "Zijn er genoeg sportplekken?", "chapter": "Sporten"},
                {"kind": "Afbeelding", "number": 2, "title": "Speeltuin Lindelaan", "chapter": "Sporten"},
                {"kind": "Afbeelding", "number": 3, "title": "Inspiratie buitenfitness", "chapter": "aanbevelingen"}],
}
GENERATED = {
    "Sporten": ["De experts geven sporten gemiddeld een 2,3 op een schaal van 1 tot 5 (12 stellingen).",
                "Er staan 15 sportplekken op de kaart van de gemeente in de buurt.",
                "Bewoners waarderen de groene parken met veel bomen."],
}
EVIDENCE = ["De experts beoordelen het thema sporten gemiddeld met een 2,3 op een schaal van 1 tot 5.",
            "Sport- en speelkaart: in de buurt staan 15 sportplekken op de kaart van de gemeente.",
            "Positieve bevinding van bewoners: groene parken met veel bomen."]


class TestEvaluation(unittest.TestCase):
    def test_alignment_ceiling_numbers_additions(self):
        from reports.evaluate import evaluate
        res = evaluate(REFERENCE, GENERATED, EVIDENCE, run_figures=[
            {"kind": "Afbeelding", "number": 1, "caption": "Sport- en speelkaart onderzoeksgebied", "chapter": "Resultaten"},
            {"kind": "Figuur", "number": 1, "caption": "Zijn er genoeg sportplekken?", "chapter": "Resultaten"}])
        rows = {r["reference"][:20]: r for r in res["rows"] if r["section"] == "Sporten"}
        self.assertEqual(rows["De experts geven het"]["label"], "covered")
        self.assertEqual(rows["Bewoners noemen de j"]["label"], "missing")           # field knowledge: not in the draft
        self.assertEqual(rows["Bewoners noemen de j"]["evidence_label"], "missing")  # ... nor in the data
        self.assertTrue(rows["Er zijn 12 sportplek"]["numbers_differ"])             # 12 in the original, 15 generated
        c = res["summary"]["content"]
        self.assertEqual(c["reference_statements"], 4)                              # recommendations excluded
        self.assertEqual(res["summary"]["recommendations"]["reference_statements"], 1)
        self.assertGreaterEqual(c["possible_number_conflicts"], 1)
        self.assertEqual(res["per_section"]["Sporten"]["additions"], 1)              # the parks sentence
        self.assertIn("samenvatting", res["summary"]["structure"]["missing"])
        self.assertEqual(res["summary"]["structure"]["not_generated_by_design"], ["aanbevelingen"])
        f = res["summary"]["figures"]
        self.assertEqual(f["chart"]["matched"], 1)
        self.assertEqual(f["photo"], {"reference": 1, "generated": 0, "matched": 0})
        self.assertEqual(f["recommendations_chapter"]["reference"], 1)

    def test_deterministic_and_ceiling_independent_of_the_draft(self):
        from reports.evaluate import evaluate
        a = evaluate(REFERENCE, GENERATED, EVIDENCE)["summary"]["content"]
        b = evaluate(REFERENCE, GENERATED, EVIDENCE)["summary"]["content"]
        self.assertEqual(a, b)
        other = evaluate(REFERENCE, {"Sporten": ["Iets heel anders over voetbal en tennis in de zomer."]},
                         EVIDENCE)["summary"]["content"]
        self.assertEqual(a["ceiling"], other["ceiling"])
        self.assertLess(other["coverage"], a["coverage"])

    def test_judge_decides_labels_and_is_cached(self):
        from reports.evaluate import evaluate
        from reports.judge import Judge
        tmp = Path(tempfile.mkdtemp())
        info = ModelInfo("replay", "replay", "replay", "local", Capabilities(structured_output=True))
        verdict = json.dumps({"label": "partial", "zinnen": [1], "conflict": True, "reden": "ander getal"})
        judge = Judge(ReplayProvider(info, [verdict] * 50), tmp / "judge.json")
        res = evaluate(REFERENCE, GENERATED, EVIDENCE, judge=judge)
        self.assertTrue(res["summary"]["method"].startswith("llm-judge:"))
        judged = [r for r in res["rows"] if r["judge_reason"] == "ander getal"]       # asked (not skipped)
        self.assertTrue(judged and all(r["label"] == "partial" for r in judged))
        self.assertTrue(all(r["numbers_differ"] for r in judged))                    # conflict flag counts
        calls = judge.calls
        again = Judge(ReplayProvider(info, []), tmp / "judge.json")                   # no outputs left: cache only
        evaluate(REFERENCE, GENERATED, EVIDENCE, judge=again)
        self.assertEqual(again.calls, 0)
        self.assertGreater(calls, 0)

    def test_hosted_judge_refused(self):
        from reports.judge import Judge
        info = ModelInfo("h", "openai_compatible", "m", "hosted", Capabilities(), data_residency="EU")
        with self.assertRaises(PrivacyViolation):
            Judge(ReplayProvider(info, []), Path(tempfile.mkdtemp()) / "j.json")
        Judge(ReplayProvider(info, []), Path(tempfile.mkdtemp()) / "j.json", allow_hosted=True)

    def test_calibrate(self):
        from reports.evaluate import calibrate, evaluate, write_outputs
        res = evaluate(REFERENCE, GENERATED, EVIDENCE)
        out = write_outputs(res, Path(tempfile.mkdtemp()), "test")
        with open(out["csv"], encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            r["human_label"] = r["auto_label"]                                       # perfect agreement
        with open(out["csv"], "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        cal = calibrate(out["csv"])
        self.assertEqual(cal["report"]["word_overlap"]["agreement"], 1.0)
        self.assertIsNone(cal["evidence"]["word_overlap"])
        self.assertIn("Vergelijking met het originele rapport", out["markdown"].read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("soffice"), "LibreOffice not installed")
class TestRoundTrip(unittest.TestCase):
    """A rendered report compared with its own PDF must score close to 100%."""

    def test_report_against_itself(self):
        from reports.evaluate import evaluate_run, load_reference
        from reports.runs import generate_report_run
        ws = _ws()
        run = generate_report_run(ws, CASE, "template")
        d = ws.report_run_dir(CASE, run["run_id"])
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(d), str(d / run["files"]["docx"])],
                       capture_output=True, timeout=240)
        pdf = next(d.glob("*.pdf"))
        ref = load_reference(pdf)
        self.assertIn("Sporten", ref["sections"])
        res = evaluate_run(ws, CASE, run["run_id"], reference=ref)
        c = res["summary"]["content"]
        self.assertGreaterEqual(c["coverage_of_supportable"], 0.9)
        self.assertGreater(c["coverage"], 0.75)                  # the rest is fixed renderer text (method intro)


if __name__ == "__main__":
    unittest.main()
