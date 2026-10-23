"""Images (deterministic map measurement, model reading safeguards, confirmation),
the Oss/Wijkscan shapes (expert section numbering, survey totals) and the
evidence/verifier rules that depend on them. Synthetic fixtures only."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import openpyxl
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from drafting.models import DraftClaim  # noqa: E402
from evidence.models import Evidence  # noqa: E402
from imaging import maps  # noqa: E402
from imaging.confirm import ConfirmationLog  # noqa: E402
from imaging.evaluate import score_readings  # noqa: E402
from imaging.vision import ensemble, read_infographic, sanity_checks  # noqa: E402
from ingestion.models import SourceRecord  # noqa: E402
from ingestion.parsers import expert_parser, survey_tally_parser  # noqa: E402
from llm.base import Capabilities, ModelInfo  # noqa: E402
from llm.providers import ReplayProvider  # noqa: E402
from verification.checks import verify_claim  # noqa: E402

RED, GREEN = (230, 30, 40), (20, 200, 60)


def synthetic_map(path: Path, shift: tuple[int, int] = (0, 0)) -> Path:
    """Two neighbourhoods (left/right) inside a thick black border; buildings
    coloured red/green; a two-entry legend bottom-right."""
    dx, dy = shift
    im = Image.new("RGB", (640, 520), (235, 235, 230))
    d = ImageDraw.Draw(im)
    d.rectangle([40 + dx, 40 + dy, 440 + dx, 400 + dy], outline=(0, 0, 0), width=7)
    d.line([240 + dx, 40 + dy, 240 + dx, 400 + dy], fill=(0, 0, 0), width=7)
    for x0, y0, c in [(70, 80, RED), (70, 240, RED), (280, 80, RED), (280, 240, GREEN), (360, 80, GREEN), (360, 240, GREEN)]:
        d.rectangle([x0 + dx, y0 + dy, x0 + 45 + dx, y0 + 45 + dy], fill=c)
    d.rectangle([500, 430, 520, 450], fill=RED)
    d.rectangle([500, 470, 520, 490], fill=GREEN)
    im.save(path)
    return path


SEEDS = {"West": (0.25, 0.5), "Oost": (0.75, 0.5)}


class TestMaps(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = synthetic_map(self.tmp / "Beweeggroenkaart Test.png")

    def test_legend_swatches_found_top_to_bottom(self):
        sw = maps.detect_swatches(maps.load_rgb(self.path))
        self.assertEqual([s.rgb for s in sw], [RED, GREEN])

    def test_identical_legend_colours_are_flagged(self):
        sw = [maps.Swatch(RED, (0, 0, 1, 1), 1), maps.Swatch(GREEN, (0, 2, 1, 3), 1), maps.Swatch(GREEN, (0, 4, 1, 5), 1)]
        _, warnings = maps.label_swatches(sw, ["0", "1", "2"])
        self.assertTrue(any("identical colour" in w for w in warnings))

    def test_regions_and_shares(self):
        m = maps.measure_classes(self.path, ["Geen groen", "Wel groen"], SEEDS)
        self.assertEqual(m.warnings, [])
        self.assertEqual(set(m.per_region), {"West", "Oost"})
        west = m.shares(m.per_region["West"])
        oost = m.shares(m.per_region["Oost"])
        self.assertAlmostEqual(west["Geen groen"], 1.0, places=2)
        self.assertAlmostEqual(oost["Wel groen"], 0.75, places=2)
        self.assertAlmostEqual(m.shares(m.total)["Geen groen"], 0.5, places=2)

    def test_count_objects_per_region(self):
        m = maps.count_objects(self.path, ["Geen groen", "Wel groen"], seeds=SEEDS)
        self.assertEqual(m.per_region["West"], {"Geen groen": 2, "Wel groen": 0})
        self.assertEqual(m.per_region["Oost"], {"Geen groen": 1, "Wel groen": 3})

    def test_region_template_alignment(self):
        ref = maps.load_rgb(self.path)
        ref_reg, regions, _ = maps.detect_regions(ref, SEEDS)
        shifted = maps.load_rgb(synthetic_map(self.tmp / "shifted.png", shift=(24, 16)))
        out, info = maps.align_regions(ref, ref_reg, shifted, scale=2)
        self.assertEqual(info["offset_xy"], [24, 16])
        self.assertGreaterEqual(info["border_overlap"], 0.9)
        west_id = next(r.region_id for r in regions if r.name == "West")
        self.assertEqual(out[220, 140 + 24], west_id)


def _replay(outputs):
    return ReplayProvider(ModelInfo("r", "replay", "replay-vlm", "local", Capabilities(structured_output=True, vision=True)),
                          outputs)


class TestVisionSafeguards(unittest.TestCase):
    def setUp(self):
        self.img = Path(tempfile.mkdtemp()) / "info.png"
        Image.new("RGB", (200, 100), "white").save(self.img)

    def _sample(self, v):
        return json.dumps({"values": [
            {"chart": "Vitaliteit", "area": "Dichtersbuurt", "label": "Overgewicht", "value": v, "unit": "%", "as_printed": f"{v}%"},
            {"chart": "Leeftijdsgroepen", "area": "Nederland", "label": "0-15 jaar", "value": 15, "unit": "%", "as_printed": "15%"},
            {"chart": "Leeftijdsgroepen", "area": "Nederland", "label": "15-25 jaar", "value": 12, "unit": "%", "as_printed": "12%"},
            {"chart": "Leeftijdsgroepen", "area": "Nederland", "label": "65 jaar of ouder", "value": 31, "unit": "%", "as_printed": "31%"},
        ]})

    def test_self_ensemble_median_and_agreement(self):
        run = read_infographic(self.img, _replay([self._sample(62), self._sample(62), self._sample(52)]), "t", samples=3)
        over = next(r for r in run.readings if r.label == "Overgewicht")
        self.assertEqual(over.value, 62)
        self.assertAlmostEqual(over.agreement, 2 / 3, places=2)

    def test_sum_check_flags_impossible_donut(self):
        rs = ensemble([[{"chart": "Leeftijdsgroepen", "area": "NL", "label": l, "value": v, "unit": "%"}
                        for l, v in [("a", 40), ("b", 40), ("c", 40)]]], [0], 1)
        sanity_checks(rs)
        self.assertTrue(all(r.checks for r in rs))

    def test_tiles_count_once_per_sample(self):
        one = [{"chart": "c", "area": "a", "label": "l", "value": 5, "unit": ""}]
        rs = ensemble([one, one, one, one], [0, 1, 0, 1], 2)   # 2 samples x 2 overlapping tiles
        self.assertEqual(rs[0].samples_seen, 2)
        self.assertEqual(rs[0].agreement, 1.0)

    def test_evaluation_against_gold(self):
        recs = [SourceRecord(id="r1", perspective="data", source_file="x", sheet=None, locator="x", value=62,
                             extra={"image_id": "img", "area": "Dichtersbuurt", "label": "Overgewicht", "chart": "Vitaliteit"}),
                SourceRecord(id="r2", perspective="data", source_file="x", sheet=None, locator="x", value=31,
                             extra={"image_id": "img", "area": "NL", "label": "65 jaar of ouder", "chart": "Leeftijd",
                                    "checks": ["sum"]}),
                SourceRecord(id="r3", perspective="data", source_file="x", sheet=None, locator="x", value=7,
                             extra={"image_id": "img", "area": "Kortfoort", "label": "Verzonnen", "chart": "x"})]
        gold = {"value_tolerance": 0.5, "value": [
            {"image": "img", "chart": "Vitaliteit", "area": "Dichtersbuurt", "label": "Overgewicht", "value": 62, "unit": "%"},
            {"image": "img", "chart": "Leeftijd", "area": "Nederland", "label": "65 jaar of ouder", "value": 21, "unit": "%"},
            {"image": "img", "chart": "Inkomen", "area": "Kortfoort", "label": "Gemiddeld inkomen", "value": 35100, "unit": "EUR"}]}
        s = score_readings(recs, gold)["summary"]
        self.assertEqual((s["recall"], s["value_accuracy"]), (round(2 / 3, 3), round(1 / 3, 3)))
        self.assertEqual((s["wrong_values"], s["flagged_wrong"]), (1, 1))
        self.assertEqual(s["unmatched_rate"], round(1 / 3, 3))


class TestConfirmations(unittest.TestCase):
    def test_status_and_chain(self):
        log = ConfirmationLog(Path(tempfile.mkdtemp()) / "c.json")
        self.assertEqual(log.status("a")[0], "unconfirmed")
        log.confirm("a", "mika")
        log.correct("b", 21.0, "mika", "was 31")
        log.reject("c", "mika")
        self.assertEqual(log.status("a")[0], "confirmed")
        self.assertEqual(log.status("b")[:2], ("corrected", 21.0))
        self.assertEqual(log.status("c")[0], "rejected")
        again = ConfirmationLog(log.path)
        self.assertEqual(again.verify_chain(), [])
        again.events[1].value = 99.0
        self.assertTrue(again.verify_chain())
        with self.assertRaises(ValueError):
            log.confirm("d", "")


class TestOssShapes(unittest.TestCase):
    def test_expert_statement_numbers_restart_per_section(self):
        """Regression: the Sept parser matched on statement number alone, so every
        real Oss statement got the LAST theme's label."""
        p = Path(tempfile.mkdtemp()) / "Expertwandeling resultaten Oss Testbuurt.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Score Testbuurt"
        ws.append(["Vraag", "A", "E", None, "Gemiddelde", "Definitief", "Opmerkingen"])
        ws.append(["Sportplekken", None, None, None, None, None, "Geen sportplekken aanwezig"])
        ws.append([1, 2, 3, None, 2.5, None, "Veld kapot"])
        ws.append([2, 4, 4, None, 4, None, None])
        ws.append(["Ontmoeten", None, None, None, None, None, None])
        ws.append([1, 5, 5, None, 5, None, "Veel bankjes"])
        ws.append([2, None, None, None, "#DIV/0!", None, None])
        spi = wb.create_sheet("Score per indicator")
        spi.append(["Thema's", "Indicatoren", "Stelling nummer", "Stelling", "Gemiddelde score stelling"])
        spi.append(["Sporten", "Onderhoud", 1, "Sportplekken onderhouden", 2.5])
        spi.append([None, "Variatie", 2, "Variatie", 4])
        spi.append(["Ontmoeten", "Zitgelegenheid", 1, "Genoeg bankjes", 4.0])   # deliberate mismatch with 5
        spi.append([None, None, 2, "Schaduw", None])
        wb.save(p)
        recs = expert_parser.parse_score_sheet(str(p), "Score Testbuurt", "Testbuurt")
        self.assertEqual([r.theme for r in recs], ["Sporten", "Sporten", "Ontmoeten", "Ontmoeten"])
        self.assertEqual(recs[0].extra["statement"], "Sportplekken onderhouden")
        self.assertTrue(recs[0].extra["crosscheck_ok"])
        self.assertFalse(recs[2].extra["crosscheck_ok"])
        self.assertTrue(recs[2].needs_manual_mapping)           # mismatch flagged, not trusted
        self.assertTrue(recs[3].extra["not_scored"])
        remarks = expert_parser.parse_section_remarks(str(p), "Score Testbuurt", "Testbuurt")
        self.assertEqual([(r.theme, r.text) for r in remarks], [("Sporten", "Geen sportplekken aanwezig")])

    def test_survey_totals_per_group(self):
        from ingestion.buurtscan import apply_survey_mapping
        from ingestion.config import load_config
        p = Path(tempfile.mkdtemp()) / "res.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Resultaten vragenlijst"
        ws.append(["65+ en toekomstige bewoners"])
        ws.append(["In welke buurt woont u?", None, "Hoe belangrijk zijn de volgende onderdelen in de buurt voor u?",
                   None, None, None, "Welk cijfer geeft u het thema sporten in de openbare ruimte in uw buurt?",
                   None, None, None, None])
        ws.append(["Testbuurt", "Ik woon niet in deze buurten", "Wandel- en fietspaden - Neutraal",
                   "Wandel- en fietspaden - Belangrijk", "Speelgelegenheid - Neutraal", "Speelgelegenheid - Belangrijk",
                   "- 1", "- 2", "- 3", "- 4", "- 5"])
        ws.append([8, 2, 3, 7, 6, 4, 0, 1, 4, 4, 1])
        ws.merge_cells("A2:B2")
        ws.merge_cells("C2:F2")
        ws.merge_cells("G2:K2")
        wb.save(p)
        recs, meta = survey_tally_parser.parse_file(str(p))
        apply_survey_mapping(recs, load_config("survey_mapping.toml"))
        by_q = {r.extra.get("question_id"): r for r in recs}
        self.assertEqual(meta["groups"]["65+ en toekomstige bewoners"]["n_from_woonbuurt"], 10)
        self.assertEqual(by_q["belang_wandelfiets"].theme, "Actieve mobiliteit")   # matrix split per item
        self.assertEqual(by_q["belang_spelen"].theme, "Formeel spelen")
        self.assertEqual(by_q["sport_cijfer"].extra["question_kind"], "rating")    # '- 1'..'- 5' labels
        self.assertFalse(any(r.needs_manual_mapping for r in recs))


def _ev(eid, kind, perspective="data", **data):
    return Evidence(eid, "c", ["Informeel spelen"], perspective, kind, "x", data, ["r"])


class TestImageAwareVerifier(unittest.TestCase):
    def setUp(self):
        self.idx = {
            "N-1": _ev("N-1", "norm", "reference", threshold=300),
            "D-1": _ev("D-1", "map_measurement", tests_rule=True, shares_pct={"Geen groen": 57}),
            "D-2": _ev("D-2", "map_measurement", tests_rule=False, counts={"Buurtplek": 7}),
            "D-3": _ev("D-3", "image_reading", value=62, confirmed=False),
            "D-4": _ev("D-4", "image_reading", value=62, confirmed=True),
            "B-1": _ev("B-1", "coded", "bewoners", is_stakeholder=True, count=1),
        }

    def codes(self, text, ids):
        r = verify_claim(DraftClaim("c", text, "bevinding", ids), "Informeel spelen", self.idx)
        return {f["code"] for f in r["flags"] if f["severity"] != "style"}

    def test_compliance_needs_a_measurement_that_tests_the_rule(self):
        self.assertIn("norm_not_measured", self.codes("Centrum-West voldoet niet aan vuistregel R3.", ["N-1"]))
        self.assertIn("norm_not_measured", self.codes("Centrum-West voldoet niet aan vuistregel R3.", ["N-1", "D-2"]))
        self.assertNotIn("norm_not_measured", self.codes("57% voldoet niet aan vuistregel R3.", ["N-1", "D-1"]))

    def test_rule_testing_measurement_may_name_rivm(self):
        self.idx["D-1"].data["rules"] = ["R3"]
        self.assertNotIn("attribution_mismatch", self.codes("Volgens de RIVM-vuistregel R3 heeft 57% geen groen.", ["D-1"]))
        self.assertIn("attribution_mismatch", self.codes("Volgens de RIVM-vuistregel heeft 62% overgewicht.", ["D-4"]))

    def test_unconfirmed_image_reading_is_weak(self):
        self.assertIn("image_unconfirmed", self.codes("Overgewicht is 62%.", ["D-3"]))
        self.assertNotIn("image_unconfirmed", self.codes("Overgewicht is 62%.", ["D-4"]))

    def test_stakeholders_are_not_residents(self):
        self.assertIn("attribution_mismatch", self.codes("Bewoners missen een speeltuin.", ["B-1"]))


if __name__ == "__main__":
    unittest.main()
