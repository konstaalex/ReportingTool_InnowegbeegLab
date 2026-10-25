"""Researcher notes, distance-zone coverage (RIVM R2/R4), cross-theme
conclusions, the case-aware method text and the new report chapters.
Synthetic fixtures only."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import openpyxl
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from buurtscan_fixtures import has_docx, write_case_folder  # noqa: E402
from app.services import Workspace  # noqa: E402
from drafting.models import DraftClaim  # noqa: E402
from drafting.pipelines import PipelineConfig, run_pipeline  # noqa: E402
from evidence.builder import _Ids, cross_theme_evidence, image_evidence, note_evidence, recurring_terms  # noqa: E402
from evidence.models import AANLEIDING, CONCLUSIES, Evidence  # noqa: E402
from export.report import ReportInputs, methode_text, render_markdown  # noqa: E402
from imaging import maps  # noqa: E402
from ingestion.models import SourceRecord  # noqa: E402
from ingestion.parsers import notes_parser  # noqa: E402
from ingestion.store import SourceStore  # noqa: E402
from verification.checks import verify_claim  # noqa: E402

CASE = "testwijk-teststad"


def write_notes(path: Path, rows: list[list]) -> Path:
    notes_parser.write_template(path)
    wb = openpyxl.load_workbook(path)
    ws = wb[notes_parser.SHEET]
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


NOTE_ROWS = [
    [1, "Observatie expertwandeling", "Formeel spelen", "Teststraat", "De speeltuin aan de Teststraat is verouderd",
     "Expertwandeling 12-03-2026, foto 4", None, None],
    [2, "Bewonersbijeenkomst", "Ontmoeten", None, "Meer bankjes bij het speelveld", "Bewonersbijeenkomst 15-04-2026",
     None, 5],
    [3, "Projectcontext", None, "Testpark", "Het bedrijventerrein wordt een woonwijk met 2.000 woningen",
     "Projectopdracht gemeente Teststad, 2026", None, None],
    [4, "Literatuur", "Actieve mobiliteit", None, "Voorzieningen dichtbij dragen bij aan meer wandelen en fietsen",
     "Mulier Instituut (2026). Testrapport.", None, None],
    [5, "Gerucht", "Sporten", None, "Iets zonder geldige soort", None, None, None],
    [6, "Observatie expertwandeling", "Sportten", None, "Thema met tikfout", "Wandeling", None, None],
]


class TestNotes(unittest.TestCase):
    def setUp(self):
        self.path = write_notes(Path(tempfile.mkdtemp()) / "Onderzoekersnotities.xlsx", NOTE_ROWS)

    def test_template_never_overwrites_filled_notes(self):
        with self.assertRaises(FileExistsError):
            notes_parser.write_template(self.path)
        self.assertEqual(len(notes_parser.parse_notes(self.path)[0]), 6)

    def test_template_is_recognised_and_parsed(self):
        self.assertTrue(notes_parser.is_notes_workbook(self.path))
        records, problems = notes_parser.parse_notes(self.path, case_id="x")
        self.assertEqual(len(records), 6)
        by_nr = {r.extra["nr"]: r for r in records}
        self.assertEqual(by_nr[1].perspective, "expert")
        self.assertEqual(by_nr[1].theme, "Formeel spelen")
        self.assertEqual(by_nr[1].locator, "rij 2")
        self.assertEqual(by_nr[2].extra["count"], 5)
        self.assertTrue(by_nr[5].needs_manual_mapping)       # unknown soort + no source
        self.assertTrue(by_nr[6].needs_manual_mapping)       # unknown theme
        self.assertTrue(any("onbekende soort" in p for p in problems))
        self.assertTrue(any("geen bron" in p for p in problems))
        self.assertTrue(any("onbekend thema" in p for p in problems))

    def test_note_evidence_perspectives_and_sections(self):
        records, _ = notes_parser.parse_notes(self.path, case_id="x")
        ev = note_evidence(records, "x", _Ids())
        obs = ev["Formeel spelen"][0]
        self.assertTrue(obs.evidence_id.startswith("O-FS-"))
        self.assertEqual(obs.perspective, "expert")
        self.assertIn("Expertwandeling 12-03-2026", obs.statement)
        meeting = ev["Ontmoeten"][0]
        self.assertEqual(meeting.perspective, "bewoners")
        self.assertTrue(meeting.contains_free_text)          # may hold personal details -> local only
        self.assertIn("5× genoemd", meeting.statement)
        ctx = ev[AANLEIDING][0]
        self.assertEqual(ctx.perspective, "context")
        lit = ev["Actieve mobiliteit"][0]
        self.assertTrue(lit.statement.startswith("Literatuur:"))

    def test_meeting_note_is_not_the_residents_view(self):
        records, _ = notes_parser.parse_notes(self.path, case_id="x")
        meeting = note_evidence(records, "x", _Ids())["Ontmoeten"][0]
        claim = DraftClaim("ON-01", "De meeste bewoners willen meer bankjes.", "bevinding", [meeting.evidence_id])
        flags = [f["code"] for f in verify_claim(claim, "Ontmoeten", {meeting.evidence_id: meeting})["flags"]]
        self.assertIn("overgeneralisation", flags)


class TestNotesInWorkspace(unittest.TestCase):
    def test_ingest_notes_replace_and_survive_reingest(self):
        root = write_case_folder(Path(tempfile.mkdtemp()) / "data", with_docx=has_docx())
        ws = Workspace(Path(tempfile.mkdtemp()) / "ws")
        ws.ingest(str(root))
        path = write_notes(Path(tempfile.mkdtemp()) / "Onderzoekersnotities.xlsx", NOTE_ROWS[:4])
        res = ws.ingest_notes(path, CASE)
        self.assertEqual(res["records"], 4)
        self.assertEqual(res["problems"], [])
        packs = ws.evidence(CASE, rebuild=True)
        self.assertEqual(len(packs[AANLEIDING].items), 1)
        self.assertTrue(any(e.kind == "note" for e in packs["Formeel spelen"].items))
        # same file again: replaced, not duplicated
        ws.ingest_notes(path, CASE)
        self.assertEqual(len(ws.notes(CASE)), 4)
        self.assertEqual(sum(1 for d in ws.documents() if d.get("source_type") == "researcher_notes"), 1)
        # re-ingesting the case keeps notes that were added separately
        ws.ingest(str(root))
        self.assertEqual(len(ws.notes(CASE)), 4)
        # Aanleiding drafts from the notes; an empty pack says what to add
        d = ws.generate(CASE, AANLEIDING, PipelineConfig(pipeline_id="template"), None, "tester")
        self.assertEqual(d.claims[0].text, "Het bedrijventerrein wordt een woonwijk met 2.000 woningen")
        md = render_markdown(ws.report_inputs(CASE), preview=True)
        self.assertIn("## Aanleiding", md)
        self.assertIn("Mulier Instituut (2026). Testrapport.", md)   # literature in the Bronnenlijst


# ------------------------------------------------------------------ zone coverage

ZONE = (100, 200, 240)
RED, GREEN, ORANGE = (230, 30, 40), (20, 200, 60), (250, 120, 0)


def _basemap(w: int = 640, h: int = 520, shift=(0, 0)) -> Image.Image:
    """Grey blocks and white streets: the shared base map both maps are drawn on."""
    rng = np.random.default_rng(7)
    im = Image.new("RGB", (w, h), (232, 232, 228))
    d = ImageDraw.Draw(im)
    dx, dy = shift
    for _ in range(140):
        x, y = int(rng.integers(-40, w)), int(rng.integers(-40, h))
        bw, bh = int(rng.integers(12, 40)), int(rng.integers(12, 40))
        g = int(rng.integers(185, 215))
        d.rectangle([x + dx, y + dy, x + bw + dx, y + bh + dy], fill=(g, g, g))
    for k in range(0, w, 70):
        d.line([k + dx, 0, k + dx, h], fill=(255, 255, 255), width=5)
    return im


HOUSES = [(70, 80), (70, 240), (150, 160), (280, 80), (280, 240), (360, 160)]   # 3 West, 3 Oost


def _zone_maps(tmp: Path, shift=(12, 8)) -> tuple[Path, Path]:
    ref = _basemap()
    d = ImageDraw.Draw(ref)
    for i, (x, y) in enumerate(HOUSES):
        d.rectangle([x, y, x + 40, y + 40], fill=RED if i % 2 else GREEN)
    d.rectangle([40, 40, 440, 400], outline=(0, 0, 0), width=7)
    d.line([240, 40, 240, 400], fill=(0, 0, 0), width=7)
    d.rectangle([500, 430, 520, 450], fill=RED)
    d.rectangle([500, 470, 520, 490], fill=GREEN)
    ref_p = tmp / "Basisbehoeftekaart Test.png"
    ref.save(ref_p)

    dx, dy = shift
    tgt = _basemap(shift=shift)
    d = ImageDraw.Draw(tgt)
    for x, y in HOUSES:
        d.rectangle([x + dx, y + dy, x + 40 + dx, y + 40 + dy], fill=(200, 200, 200))
    arr = np.asarray(tgt).astype(np.float32)
    zone = np.zeros(arr.shape[:2], bool)
    zone[40 + dy:400 + dy, 40 + dx:240 + dx] = True               # the West neighbourhood lies inside the zone
    arr[zone] = arr[zone] * 0.5 + np.array(ZONE) * 0.5
    tgt = Image.fromarray(arr.astype(np.uint8))
    d = ImageDraw.Draw(tgt)
    d.rectangle([40 + dx, 40 + dy, 440 + dx, 400 + dy], outline=(0, 0, 0), width=7)
    d.line([240 + dx, 40 + dy, 240 + dx, 400 + dy], fill=(0, 0, 0), width=7)
    d.rectangle([500, 430, 520, 450], fill=ORANGE)
    swatch = tuple(int(c * 0.5 + 255 * 0.5) for c in ZONE)
    d.rectangle([500, 470, 520, 490], fill=swatch)
    tgt_p = tmp / "Sport- en speelkaart Test tot en met 12 jaar.png"
    tgt.save(tgt_p)
    return ref_p, tgt_p


class TestZoneCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.ref, cls.tgt = _zone_maps(cls.tmp)
        cls.template = maps.RegionTemplate.from_map("ref", cls.ref, ["0", "1"], {"West": (0.25, 0.5), "Oost": (0.75, 0.5)})

    def test_registration_on_shared_base_map(self):
        info = maps.align_basemap(self.template.img, maps.load_rgb(self.tgt), scale=2)
        self.assertEqual(info["zoom"], 1.0)
        self.assertEqual(info["offset_xy"], [12, 8])
        self.assertGreaterEqual(maps.border_agreement(self.template.img, info, maps.load_rgb(self.tgt)), maps.BORDER_OK)

    def test_tint_score_separates_zone_from_base_map(self):
        swatch = tuple(int(c * 0.5 + 255 * 0.5) for c in ZONE)
        grey = np.array([[[200, 200, 200]]], np.uint8)
        tinted = (grey * 0.5 + np.array(ZONE) * 0.5).astype(np.uint8)
        self.assertLess(float(maps.tint_score(grey, swatch)[0, 0]), 0.1)
        self.assertGreater(float(maps.tint_score(tinted, swatch)[0, 0]), 0.9)

    def test_homes_inside_the_zone_per_neighbourhood(self):
        m = maps.measure_zone_coverage(self.tgt, ["Buurtplek", "Tot en met 12 jaar"], "Tot en met 12 jaar", self.template,
                                       overlay_to=self.tmp / "ov.png")
        self.assertEqual(m.warnings, [])
        self.assertEqual(maps.coverage_pct(m.per_region["West"])[0], 100)
        self.assertEqual(maps.coverage_pct(m.per_region["Oost"])[0], 0)
        self.assertEqual(maps.coverage_pct(m.total)[0], 50)
        self.assertTrue(Path(m.overlay_path).exists())

    def test_wrong_reference_is_refused_not_guessed(self):
        other = self.tmp / "other.png"
        Image.new("RGB", (640, 520), (232, 232, 228)).save(other)
        im = Image.open(self.tgt)
        im2 = Image.new("RGB", im.size, (240, 240, 240))
        d = ImageDraw.Draw(im2)
        d.rectangle([500, 430, 520, 450], fill=ORANGE)
        d.rectangle([500, 470, 520, 490], fill=tuple(int(c * 0.5 + 255 * 0.5) for c in ZONE))
        blank = self.tmp / "blank.png"
        im2.save(blank)
        m = maps.measure_zone_coverage(blank, ["Buurtplek", "Zone"], "Zone", self.template)
        self.assertEqual(m.total, {})
        self.assertTrue(any("could not be matched" in w for w in m.warnings))

    def test_coverage_evidence_tests_the_rule(self):
        rec = SourceRecord(id="m1", perspective="data", source_file="kaart.png", sheet=None, locator="x",
                           extra={"record_kind": "image_measurement", "measure_kind": "coverage", "area": "West",
                                  "title": "Sport- en speelkaart tot en met 12 jaar",
                                  "measure": "speelplek binnen 200 meter, kinderen t/m 12 jaar",
                                  "coverage": {"homes_pct": 38, "area_pct": 43, "zone": "Tot en met 12 jaar"},
                                  "reference_map": "basisbehoefte", "themes": ["Formeel spelen"], "rules": ["R2"],
                                  "method": "m", "warnings": []})
        ev, _ = image_evidence([rec], "x", _Ids())
        e = ev["Formeel spelen"][0]
        self.assertTrue(e.data["tests_rule"])
        self.assertIn("38% van de woningen binnen de zone en 62% erbuiten", e.statement)
        self.assertIn("Toetst RIVM-vuistregel R2", e.statement)
        claim = DraftClaim("FS-01", "In West ligt 62% van de woningen niet binnen 200 meter van een speelplek.",
                           "bevinding", [e.evidence_id])
        self.assertEqual(verify_claim(claim, "Formeel spelen", {e.evidence_id: e})["status"], "grounded")


# ------------------------------------------------------------------ cross-theme conclusions

def _ev(eid, kind, theme, statement="", perspective="bewoners", **data):
    return Evidence(eid, "x", [theme], perspective, kind, statement, data, [f"r-{eid}"])


class TestConclusions(unittest.TestCase):
    def setUp(self):
        self.items = {
            "Sporten": [_ev("X-SP-01", "theme_average", "Sporten", "", "expert", mean=3.2, n_statements=9),
                        _ev("B-SP-01", "coded", "Sporten", 'Behoefte genoemd door \'65+\' (gecodeerd open antwoord): "Betere verlichting" (4×).', count=4),
                        _ev("B-SP-02", "rating", "Sporten", "", mean=3.5, n=20)],
            "Ontmoeten": [_ev("X-ON-01", "theme_average", "Ontmoeten", "", "expert", mean=3.4, n_statements=9),
                          _ev("X-ON-02", "expert_statement", "Ontmoeten", "", "expert", remark="Weinig verlichting bij de bankjes"),
                          _ev("B-ON-01", "rating", "Ontmoeten", "", mean=3.0, n=20)],
            "Uitstraling": [_ev("X-UI-01", "theme_average", "Uitstraling", "", "expert", mean=3.9, n_statements=9),
                            _ev("B-UI-01", "coded", "Uitstraling", 'Positieve bevinding genoemd door \'65+\' (gecodeerd open antwoord): "Veel bomen" (2×).', count=2),
                            _ev("B-UI-02", "rating", "Uitstraling", "", mean=3.8, n=20)],
        }

    def test_recurring_terms_need_two_themes(self):
        terms = recurring_terms(self.items, "x", _Ids())
        labels = {e.data["term"] for e in terms}
        self.assertIn("verlichting", labels)          # Sporten (coded) + Ontmoeten (expert remark)
        self.assertNotIn("groen", labels)             # only Uitstraling
        v = next(e for e in terms if e.data["term"] == "verlichting")
        self.assertEqual(v.data["themes"], ["Sporten", "Ontmoeten"])
        self.assertIn("4× genoemd", v.statement)
        self.assertIn("1 opmerking of stelling van de experts", v.statement)

    def test_group_names_do_not_count_as_terms(self):
        # '65+' is in the group name of every coded item; only the answer text counts
        terms = recurring_terms(self.items, "x", _Ids())
        self.assertNotIn("ouderen", {e.data["term"] for e in terms})

    def test_cross_theme_pack_and_template(self):
        items = cross_theme_evidence(self.items, "x", _Ids())
        rank = next(e for e in items if e.kind == "cross_theme" and e.perspective == "expert")
        self.assertIn("uitstraling 3,9; ontmoeten 3,4; sporten 3,2", rank.statement)
        from evidence.models import EvidencePack
        pack = EvidencePack("x", CONCLUSIES, items)
        d = run_pipeline(pack, PipelineConfig(pipeline_id="template"))
        self.assertTrue(d.claims and all(c.evidence_ids for c in d.claims))
        idx = pack.by_id()
        claim = DraftClaim("CO-01", "Verlichting komt terug bij sporten en ontmoeten.", "conclusie",
                           [e.evidence_id for e in items if e.data.get("term") == "verlichting"] + ["X-SP-01"])
        flags = [f["code"] for f in verify_claim(claim, CONCLUSIES, idx)["flags"]]
        self.assertNotIn("theme_mismatch", flags)     # conclusions cite across themes by design

    def test_conclusions_need_prompt_v2(self):
        from evidence.models import EvidencePack
        from llm.base import Capabilities, ModelInfo
        from llm.providers import FakeProvider
        pack = EvidencePack("x", CONCLUSIES, cross_theme_evidence(self.items, "x", _Ids()))
        fake = FakeProvider(ModelInfo("fake", "fake", "fake", "local", Capabilities(structured_output=True)))
        d = run_pipeline(pack, PipelineConfig(pipeline_id="naive", prompt_version="v1"), fake)
        self.assertTrue(any("v2" in e for e in d.errors))
        d = run_pipeline(pack, PipelineConfig(pipeline_id="staged", prompt_version="v2"), fake)
        self.assertEqual(d.stages[0].stage, "conclusions")
        self.assertTrue(d.claims)


# ------------------------------------------------------------------ method text

class TestMethodText(unittest.TestCase):
    def _inputs(self, evidence, layout="wijkscan"):
        return ReportInputs("x", "Test", {}, {e.evidence_id: e for e in evidence}, SourceStore(), [],
                            {"layout": layout})

    def test_counts_statements_once_and_names_tested_rules(self):
        ev = []
        for b in ("Noord", "Zuid"):
            for i in range(3):
                ev.append(Evidence(f"X-SP-{b}{i}", "x", ["Sporten"], "expert", "expert_statement", "",
                                   {"neighbourhood": b, "indicator": f"ind{i}", "evaluator_scores": {"a": 3, "b": 4}}))
        ev.append(Evidence("X-SP-90", "x", ["Sporten"], "expert", "theme_average", "", {"n_statements": 6}))
        ev.append(Evidence("X-SP-91", "x", ["Sporten"], "expert", "theme_average", "", {"n_statements": 3}))
        ev.append(Evidence("B-AL-01", "x", [], "bewoners", "respondents", "", {"group": "65+", "n_respondents": 37}))
        ev.append(Evidence("B-AL-02", "x", [], "bewoners", "respondents", "", {"group": "Jongvolwassenen", "n_respondents": 41}))
        ev.append(Evidence("D-FS-01", "x", ["Formeel spelen"], "data", "map_measurement",
                           "Sport- en speelkaart tot en met 12 jaar (x): ...",
                           {"area": "onderzoeksgebied", "rules": ["R2"], "tests_rule": True}))
        txt = methode_text(self._inputs(ev))
        self.assertIn("Deze Wijkscan", txt)
        self.assertIn("in 2 buurten (Noord en Zuid). Per buurt zijn 3 stellingen", txt)
        self.assertIn("door 2 beoordelaars", txt)
        self.assertIn("65+ (37 respondenten) en Jongvolwassenen (41 respondenten)", txt)
        self.assertIn("Getoetst met kaartgegevens: R2", txt)
        self.assertIn("Niet getoetst", txt)
        self.assertNotIn("geen afstandsmetingen", txt)

    def test_buurtscan_without_maps_says_rules_untested(self):
        ev = [Evidence("B-AL-01", "x", [], "bewoners", "respondents", "", {"n_respondents": 45})]
        txt = methode_text(self._inputs(ev, layout=None))
        self.assertIn("Deze Buurtscan", txt)
        self.assertIn("bewoners (45 respondenten)", txt)
        self.assertIn("niet getoetst", txt)


if __name__ == "__main__":
    unittest.main()
