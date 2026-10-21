"""UC1 for the Buurtscan shapes: parsers, footer cross-check, dedup, mapping.

Run: PYTHONPATH=src python -m unittest discover -s tests -p "test_b*.py"   (or pytest)
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from buurtscan_fixtures import has_docx, write_case_folder, write_demografie, write_expert, write_survey  # noqa: E402
from ingestion.buurtscan import ingest_buurtscan_folder  # noqa: E402
from ingestion.parsers import buurtscan_expert_parser as ep  # noqa: E402
from ingestion.parsers import buurtscan_survey_parser as sp  # noqa: E402
from ingestion.parsers.demografie_parser import parse_dutch_number  # noqa: E402
from ingestion.sources import classify_filename  # noqa: E402


class TestFilenames(unittest.TestCase):
    def test_classify_known_kinds(self):
        p, t, case, label, synth = classify_filename("Expertwandeling - De Gilden Dronten (SYNTHETISCH)[24].xlsx")
        self.assertEqual((p, t, case, label, synth), ("expert", "buurtscan_expert", "de-gilden-dronten", "De Gilden Dronten", True))
        self.assertEqual(classify_filename("Vragenlijst buurtbewoners - Moesel Weert.xlsx")[:3],
                         ("bewoners", "buurtscan_survey", "moesel-weert"))

    def test_unknown_file_is_not_guessed(self):
        self.assertEqual(classify_filename("Notities - Moesel Weert.xlsx")[0], None)


class TestExpertParser(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = write_expert(self.tmp / "Expertwandeling - Testwijk Teststad.xlsx")

    def test_forward_fills_merged_theme_and_indicator(self):
        recs = ep.parse_file(str(self.path), case_id="c")
        self.assertEqual(len(recs), 4)
        self.assertEqual([r.theme for r in recs], ["Sporten", "Sporten", "Sporten", "Ontmoeten"])
        self.assertEqual(recs[2].subtheme, "Onderhoud")          # merged B6:B7
        self.assertEqual(recs[3].extra["statement_number"], 1)    # numbering restarts per theme
        self.assertEqual(recs[1].extra["remark"], "Kapotte basketbalpaal")
        self.assertEqual(recs[1].extra["cells"], {"score": "E6", "remark": "G6"})
        self.assertEqual(recs[1].value, 2.0)
        self.assertEqual(len({r.id for r in recs}), 4)

    def test_detect_rejects_oss_shape(self):
        self.assertTrue(ep.detect(["Score per indicator"]))
        self.assertFalse(ep.detect(["Score per indicator", "Score Testbuurt"]))


class TestSurveyParser(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_blocks_and_kinds(self):
        import openpyxl
        path = write_survey(self.tmp / "s.xlsx")
        ws = openpyxl.load_workbook(path).worksheets[0]
        blocks = sp.build_blocks(ws, "Testwijk")
        self.assertEqual([b.kind for b in blocks], ["choice", "likert", "multi", "rating", "multi"])
        self.assertEqual(blocks[1].item, "Openbare sport- en beweegvoorzieningen")
        self.assertIn("{buurt}", blocks[4].question_key)
        self.assertEqual(len(blocks[3].open_cols), 1)  # 'Omdat:'

    def test_parser_agrees_with_footer(self):
        path = write_survey(self.tmp / "s.xlsx")
        recs, meta = sp.parse_file(str(path), neighbourhood="Testwijk")
        self.assertEqual(meta["respondents"], 4)
        self.assertEqual(meta["footer_n"], 4)
        self.assertEqual(sp.validate_against_footer(recs, meta), [])

    def test_footer_mismatch_is_reported(self):
        path = write_survey(self.tmp / "s.xlsx", bad_footer=True)
        recs, meta = sp.parse_file(str(path), neighbourhood="Testwijk")
        problems = sp.validate_against_footer(recs, meta)
        self.assertEqual(len(problems), 1)
        self.assertIn("Fietspaden", problems[0])

    def test_rating_and_open_text(self):
        path = write_survey(self.tmp / "s.xlsx")
        recs, _ = sp.parse_file(str(path), neighbourhood="Testwijk")
        r1 = [r for r in recs if r.extra.get("respondent") == 1 and r.extra.get("question_kind") == "rating"][0]
        self.assertEqual(r1.value, 4.0)
        self.assertEqual(r1.extra["open_text"], ["Er is een trapveld"])
        self.assertTrue(r1.extra["contains_free_text"])
        self.assertTrue(r1.needs_manual_mapping)   # until the config mapping is applied


class TestFolderIngestion(unittest.TestCase):
    def test_end_to_end_folder(self):
        root = write_case_folder(Path(tempfile.mkdtemp()) / "data", with_docx=has_docx())
        res = ingest_buurtscan_folder(root)
        s = res.summary()
        self.assertEqual(len(s["duplicates_skipped"]), 1)                 # the [3] copy
        self.assertEqual(len(s["unparsed"]), 1)                           # Onbekend bestand.txt
        self.assertEqual(s["validation_problems"], {})
        self.assertEqual(s["unmapped_survey_questions"], [])
        self.assertIn("testwijk-teststad", res.cases)
        sport = [r for r in res.store.all() if r.extra.get("question_id") == "sport_cijfer"]
        self.assertTrue(sport and all(r.theme == "Sporten" for r in sport))
        mist = [r for r in res.store.all() if r.extra.get("question_id") == "mist" and r.extra.get("record_kind") == "survey_answer"]
        self.assertEqual(mist[0].extra["option_themes"]["Fietspaden"], "Actieve mobiliteit")
        self.assertEqual(sum(1 for r in res.store.all() if r.perspective == "reference"), 6)  # RIVM rules
        if has_docx():
            self.assertTrue(res.cases["testwijk-teststad"]["complete"])

    def test_unmapped_question_is_flagged(self):
        import openpyxl
        root = Path(tempfile.mkdtemp()) / "data"
        write_case_folder(root, with_duplicate=False, with_docx=False)
        p = root / "Vragenlijst buurtbewoners - Testwijk Teststad (SYNTHETISCH).xlsx"
        wb = openpyxl.load_workbook(p)
        wb.active.cell(row=1, column=18).value = "Een nieuwe vraag die nergens in de mapping staat?"
        wb.save(p)
        s = ingest_buurtscan_folder(root).summary()
        self.assertTrue(any("nieuwe vraag" in q for q in s["unmapped_survey_questions"]))


@unittest.skipUnless(has_docx(), "python-docx not installed")
class TestDemografie(unittest.TestCase):
    def test_tables_and_numbers(self):
        from ingestion.parsers import demografie_parser as dp
        path = write_demografie(Path(tempfile.mkdtemp()) / "Data voor Demografie - Testwijk Teststad.docx")
        recs = dp.parse_file(str(path))
        inh = [r for r in recs if r.extra["record_kind"] == "demografie_inhabitants"][0]
        self.assertEqual(inh.value, 5010.0)
        sporters = [r for r in recs if r.subtheme == "Sporters"][0]
        self.assertEqual(sporters.extra["values"]["Nederland"], 54.9)
        self.assertEqual(sporters.locator, "tabel 1, rij 2")
        self.assertEqual(sporters.extra["unit"], "%")

    def test_dutch_numbers(self):
        self.assertEqual(parse_dutch_number("57,8%"), 57.8)
        self.assertEqual(parse_dutch_number("44.685"), 44685.0)
        self.assertEqual(parse_dutch_number("€ 31.000"), 31000.0)
        self.assertIsNone(parse_dutch_number("n.v.t."))


if __name__ == "__main__":
    unittest.main()
