"""Evidence layer arithmetic + every verifier check (TC5-TC9 territory)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from buurtscan_fixtures import has_docx, write_case_folder  # noqa: E402
from drafting.models import DraftClaim, SectionDraft  # noqa: E402
from evidence.builder import build_case_evidence, evidence_index  # noqa: E402
from evidence.models import Evidence  # noqa: E402
from ingestion.buurtscan import ingest_buurtscan_folder  # noqa: E402
from verification.checks import parse_numbers, style_flags, verify_claim, verify_section  # noqa: E402

CASE = "testwijk-teststad"


def _packs():
    root = write_case_folder(Path(tempfile.mkdtemp()) / "data", with_docx=has_docx())
    res = ingest_buurtscan_folder(root)
    return build_case_evidence(res.store, CASE), res


class TestEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packs, cls.res = _packs()
        cls.idx = evidence_index(cls.packs)

    def test_expert_theme_average(self):
        avg = [e for e in self.packs["Sporten"].items if e.kind == "theme_average"][0]
        self.assertAlmostEqual(avg.data["mean"], round((4 + 2 + 1) / 3, 2))
        self.assertEqual(avg.data["n_low"], 2)
        self.assertEqual(avg.evidence_id, "X-SP-01")
        self.assertIn("2,3", avg.statement)

    def test_resident_rating_and_percentages(self):
        rating = [e for e in self.packs["Sporten"].items if e.kind == "rating"][0]
        self.assertEqual(rating.data["n"], 4)
        self.assertAlmostEqual(rating.data["mean"], (4 + 2 + 2 + 5) / 4)
        self.assertEqual(rating.data["pct_low"], 50)
        likert = [e for e in self.packs["Sporten"].items if e.kind == "likert"][0]
        self.assertEqual((likert.data["k_positive"], likert.data["n"], likert.data["pct_positive"]), (2, 4, 50))

    def test_option_share_routed_to_theme(self):
        fiets = [e for e in self.packs["Actieve mobiliteit"].items if e.kind == "option_share"][0]
        self.assertEqual((fiets.data["k"], fiets.data["n"], fiets.data["pct"]), (3, 4, 75))

    def test_quotes_grouped_and_privacy_flagged(self):
        q = [e for e in self.packs["Sporten"].items if e.kind == "quote" and "Niets te doen" in e.statement][0]
        self.assertEqual(q.data["count"], 2)
        self.assertTrue(q.contains_free_text)

    def test_norms_say_not_measured(self):
        norms = [e for e in self.packs["Sporten"].items if e.kind == "norm"]
        self.assertEqual({e.data["rule_id"] for e in norms}, {"R1", "R4", "R6"})
        self.assertTrue(all(e.data["measured_in_dataset"] is False for e in norms))

    def test_comparison_cites_both_perspectives(self):
        comp = [e for e in self.packs["Sporten"].items if e.kind == "comparison"][0]
        persp = {self.res.store.get(r).perspective for r in comp.source_record_ids}
        self.assertEqual(persp, {"expert", "bewoners"})

    def test_ids_are_stable(self):
        again, _ = _packs()
        self.assertEqual(self.packs["Sporten"].ids(), again["Sporten"].ids())

    def test_every_evidence_resolves_to_records(self):
        for e in self.idx.values():
            self.assertTrue(e.source_record_ids, e.evidence_id)
            for rid in e.source_record_ids:
                self.assertIsNotNone(self.res.store.get(rid), f"{e.evidence_id} -> {rid}")


def _ev(eid, perspective="bewoners", kind="likert", themes=("Sporten",), statement="", data=None):
    return Evidence(eid, CASE, list(themes), perspective, kind, statement, data or {}, ["r1"])


class TestVerifier(unittest.TestCase):
    def setUp(self):
        self.idx = {
            "B-SP-01": _ev("B-SP-01", statement="38% van de bewoners (17 van 45) vindt sport belangrijk.",
                           data={"pct_positive": 38, "k_positive": 17, "n": 45}),
            "B-SP-02": _ev("B-SP-02", kind="quote", statement="Open antwoord (1 bewoner): \"Te weinig sport\".",
                           data={"count": 1}),
            "X-SP-01": _ev("X-SP-01", perspective="expert", kind="theme_average",
                           statement="De experts geven gemiddeld 3,0.", data={"mean": 3.0}),
            "N-SP-01": _ev("N-SP-01", perspective="reference", kind="norm",
                           statement="Binnen 400 meter een sportplek.", data={"threshold": 400.0}),
            "B-ON-01": _ev("B-ON-01", themes=("Ontmoeten",), statement="60% wil meer bankjes.", data={"pct": 60}),
        }

    def v(self, text, ids, ctype="bevinding"):
        return verify_claim(DraftClaim("c1", text, ctype, ids), "Sporten", self.idx)

    def codes(self, res):
        return {f["code"] for f in res["flags"] if f["severity"] != "style"}

    def test_grounded(self):
        r = self.v("38% van de bewoners vindt sportvoorzieningen belangrijk.", ["B-SP-01"])
        self.assertEqual(r["status"], "grounded")

    def test_no_citation(self):
        r = self.v("Sporten is populair.", [])
        self.assertEqual((r["status"], self.codes(r)), ("unsupported", {"no_citation"}))

    def test_invalid_citation(self):
        self.assertIn("invalid_citation", self.codes(self.v("Iets.", ["B-SP-99"])))

    def test_unsupported_number(self):
        r = self.v("45% van de bewoners vindt sport belangrijk.", ["B-SP-01"])   # 45 = n, not a %; but 45 IS in data
        self.assertEqual(r["status"], "grounded")
        r = self.v("52% van de bewoners vindt sport belangrijk.", ["B-SP-01"])
        self.assertEqual(r["status"], "unsupported")
        self.assertEqual(r["numbers"]["unsupported"], ["52"])

    def test_decimal_numbers(self):
        self.assertEqual(parse_numbers("gemiddeld 3,0 en 1.500 meter, 57,8%"), [3.0, 1500.0, 57.8])
        self.assertEqual(self.v("Experts geven gemiddeld een 3,0.", ["X-SP-01"])["status"], "grounded")
        self.assertEqual(self.v("Experts geven gemiddeld een 3,4.", ["X-SP-01"])["status"], "unsupported")

    def test_attribution_mismatch(self):
        self.assertIn("attribution_mismatch", self.codes(self.v("Bewoners geven gemiddeld een 3,0.", ["X-SP-01"])))

    def test_majority_overgeneralisation(self):
        self.assertIn("overgeneralisation", self.codes(self.v("De meeste bewoners vinden sport belangrijk.", ["B-SP-01"])))
        self.assertIn("overgeneralisation", self.codes(self.v("De meeste bewoners vinden er te weinig sport.", ["B-SP-02"])))

    def test_generic_group_claim_on_single_quote(self):
        """Scenario B in the RE doc: one remark presented as 'the residents'."""
        r = self.v("Bewoners vinden dat er te weinig sportplekken zijn.", ["B-SP-02"])
        self.assertIn("overgeneralisation", self.codes(r))
        r = self.v("Eén bewoner vindt dat er te weinig sport is.", ["B-SP-02"])
        self.assertNotIn("overgeneralisation", self.codes(r))

    def test_norm_compliance_not_measured(self):
        r = self.v("De buurt voldoet niet aan de RIVM-vuistregel van 400 meter.", ["N-SP-01"])
        self.assertIn("norm_not_measured", self.codes(r))

    def test_theme_mismatch(self):
        self.assertIn("theme_mismatch", self.codes(self.v("60% wil meer bankjes.", ["B-ON-01"])))

    def test_style_rules(self):
        long = " ".join(["woord"] * 25) + "."
        self.assertIn("style:long_sentence", {f.code for f in style_flags(long)})
        self.assertIn("style:passive", {f.code for f in style_flags("Het veld wordt slecht onderhouden door de gemeente.")})
        self.assertIn("style:phrase", {f.code for f in style_flags("Met betrekking tot sport is er weinig.")})
        # quoted resident words are not linted
        self.assertEqual(style_flags("Een bewoner zegt: \"met betrekking tot sport\"."), [])

    def test_section_metrics_and_coverage(self):
        draft = SectionDraft("d1", CASE, "Sporten", "naive", claims=[
            DraftClaim("c1", "38% van de bewoners vindt sport belangrijk.", "bevinding", ["B-SP-01"]),
            DraftClaim("c2", "Iets zonder bron.", "interpretatie", []),
        ])
        from evidence.models import EvidencePack
        pack = EvidencePack(CASE, "Sporten", list(self.idx.values()))
        checks = verify_section(draft, pack, self.idx)
        self.assertEqual(checks["metrics"]["unsupported_rate"], 0.5)
        self.assertEqual(checks["metrics"]["citation_validity"], 1.0)
        self.assertIn("X-SP-01", checks["coverage_gaps"])   # theme average not cited


if __name__ == "__main__":
    unittest.main()
