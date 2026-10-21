"""Dependency-free test runner (unittest + openpyxl only) for environments
without network access to install pytest. Mirrors the pytest suite in the
other test_*.py files — keep both in sync when the parsers change; the
pytest files are the ones to run once a machine has real internet access.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import openpyxl
from ingestion.models import SourceRecord
from ingestion.store import SourceStore
from ingestion.parsers import resident_parser, expert_parser, map_parser


def build_categorized_workbook(tmp_path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gecategoriseerde data"
    ws.append([None, "Sporten", None, None, None, None, None, "Ontmoeten", None, None, None, None, None])
    ws.append([None, "Behoefte", "Aantal", "Belemmering", "Aantal", "Positieve bevinding", "Aantal",
               "Behoefte", "Aantal", "Belemmering", "Aantal", "Positieve bevinding", "Aantal"])
    ws.append(["Testbuurt", "Jeu de boules", 3, None, None, "Urban Ox park leuk", 1,
               "Samen tuinieren", 1, "Te druk", 1, None, None])
    ws.append([None, "Sportveldjes", 2, None, None, None, None, None, None, None, None, None, None])
    path = tmp_path / "Gecategoriseerde Resultaten Test.xlsx"
    wb.save(path)
    return path


def build_survey_workbook(tmp_path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Resultaten vragenlijst"
    ws.append(["65+ en toekomstige bewoners"])
    ws.append(["In welke buurt woont u?", None, "Hoe belangrijk?"])
    ws.append(["Testbuurt", "Andere buurt", "Wandel- en fietspaden - Belangrijk", "Onbekende kolom - Belangrijk"])
    ws.append([4, 2, 14, 5])
    path = tmp_path / "Survey Test.xlsx"
    wb.save(path)
    return path


def build_expert_neighbourhood_workbook(tmp_path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Score Testbuurt"
    ws.append(["Vraag", "A", "E", "F", "L", "R", None, "Gemiddelde", "Definitief", "Opmerkingen"])
    ws.append(["Sportplekken", None, None, None, None, None, None, None, None, "Geen sportplekken"])
    ws.append([4, 3, 3, 3, 3, 3, None, 3, None, "Twee velden aanwezig"])
    ws.append([5, 3, 4, 3, 3, 4, None, 3.4, None, "Prullenbak bij bankjes"])

    ws2 = wb.create_sheet("Score per indicator")
    ws2.append(["Thema's", "Indicatoren", "Stelling nummer", "Stelling", "Gemiddelde score stelling", "Gemiddelde score indicator"])
    ws2.append(["Sporten", "Variatie", 4, "test stelling 4", 3, 3])
    ws2.append([None, "Doelgroep", 5, "test stelling 5", 3.4, 3.4])

    path = tmp_path / "Expertwandeling resultaten Test Testbuurt.xlsx"
    wb.save(path)
    return path


def build_expert_totaal_workbook(tmp_path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Totaal scores"
    ws.append([None, "Indicator", "Stelling nummer", "Stelling", "Testbuurt A", "Testbuurt B", "Testbuurt C", None, "Gemiddelde score 3 buurten", None, "Score per indicator"])
    ws.append(["Sporten", "Variatie", 4, "test stelling", 3, 3, 4.6, None, 3.53, None, 3.53])
    ws.append([None, "Doelgroep", 5, "test stelling 2", 3.4, 2.6, 4.8, None, 3.6, None, 3.6])

    ws2 = wb.create_sheet("Opmerkingen")
    ws2.append(["Testbuurt A", None, "Testbuurt B"])
    ws2.append(["Sporten", None, "Sporten"])
    ws2.append(["Twee velden, weinig licht", None, "Verouderd materiaal"])

    path = tmp_path / "Totaal resultaten Expertwandeling Test.xlsx"
    wb.save(path)
    return path


class ResidentParserTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_categorized_sheet_splits_theme_subtype_and_neighbourhood(self):
        wb = build_categorized_workbook(self.tmp_path)
        records = resident_parser.parse_categorized_sheet(str(wb), "Gecategoriseerde data", "Gecategoriseerde Resultaten Test.xlsx")
        self.assertEqual(len(records), 5)

        jeu = next(r for r in records if r.text == "Jeu de boules")
        self.assertEqual(jeu.theme, "Sporten")
        self.assertEqual(jeu.subtheme, "Behoefte")
        self.assertEqual(jeu.neighbourhood, "Testbuurt")
        self.assertEqual(jeu.value, 3)
        self.assertFalse(jeu.needs_manual_mapping)

        sportveldjes = next(r for r in records if r.text == "Sportveldjes")
        self.assertEqual(sportveldjes.neighbourhood, "Testbuurt")  # forward-filled
        self.assertEqual(sportveldjes.theme, "Sporten")

        samen = next(r for r in records if r.text == "Samen tuinieren")
        self.assertEqual(samen.theme, "Ontmoeten")
        self.assertEqual(samen.subtheme, "Behoefte")

    def test_categorized_sheet_every_record_has_a_precise_locator(self):
        wb = build_categorized_workbook(self.tmp_path)
        records = resident_parser.parse_categorized_sheet(str(wb), "Gecategoriseerde data", "Gecategoriseerde Resultaten Test.xlsx")
        for r in records:
            self.assertEqual(r.sheet, "Gecategoriseerde data")
            self.assertTrue(r.locator.startswith("row "))
            self.assertEqual(r.source_file, "Gecategoriseerde Resultaten Test.xlsx")

    def test_survey_sheet_maps_known_column_flags_unknown(self):
        wb = build_survey_workbook(self.tmp_path)
        records = resident_parser.parse_survey_sheet(str(wb), "Survey Test.xlsx")
        known = next(r for r in records if r.subtheme == "Wandel- en fietspaden - Belangrijk")
        self.assertEqual(known.theme, "Actieve mobiliteit")
        self.assertFalse(known.needs_manual_mapping)

        unknown = next(r for r in records if r.subtheme == "Onbekende kolom - Belangrijk")
        self.assertIsNone(unknown.theme)
        self.assertTrue(unknown.needs_manual_mapping)


class ExpertParserTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_score_sheet_joins_theme_from_score_per_indicator(self):
        wb = build_expert_neighbourhood_workbook(self.tmp_path)
        records = expert_parser.parse_score_sheet(str(wb), "Score Testbuurt", "Testbuurt")
        self.assertEqual(len(records), 2)

        stmt4 = next(r for r in records if r.extra["statement_number"] == 4)
        self.assertEqual(stmt4.theme, "Sporten")
        self.assertEqual(stmt4.subtheme, "Variatie")
        self.assertEqual(stmt4.neighbourhood, "Testbuurt")
        self.assertEqual(stmt4.value, 3)
        self.assertEqual(stmt4.text, "Twee velden aanwezig")
        self.assertEqual(stmt4.extra["evaluator_scores"], {"A": 3, "E": 3, "F": 3, "L": 3, "R": 3})
        self.assertEqual(stmt4.extra["section"], "Sportplekken")

    def test_totaal_scores_forward_fills_theme_per_neighbourhood(self):
        wb = build_expert_totaal_workbook(self.tmp_path)
        records = expert_parser.parse_totaal_scores(str(wb))
        self.assertEqual(len(records), 6)
        self.assertTrue(all(r.theme == "Sporten" for r in records))
        self.assertEqual({r.neighbourhood for r in records}, {"Testbuurt A", "Testbuurt B", "Testbuurt C"})

    def test_opmerkingen_uses_theme_cell_as_section_header_not_a_record(self):
        wb = build_expert_totaal_workbook(self.tmp_path)
        records = expert_parser.parse_opmerkingen(str(wb))
        self.assertEqual(len(records), 2)
        a = next(r for r in records if r.neighbourhood == "Testbuurt A")
        self.assertEqual(a.theme, "Sporten")
        self.assertEqual(a.text, "Twee velden, weinig licht")
        b = next(r for r in records if r.neighbourhood == "Testbuurt B")
        self.assertEqual(b.theme, "Sporten")
        self.assertEqual(b.text, "Verouderd materiaal")
        self.assertTrue(all(r.text != "Sporten" for r in records))


class StoreAndMapTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_store_summary_counts_by_perspective_and_theme(self):
        store = SourceStore()
        store.add(SourceRecord(id="a", perspective="bewoners", source_file="f.xlsx", sheet="s", locator="row 1", theme="Sporten"))
        store.add(SourceRecord(id="b", perspective="expert", source_file="f.xlsx", sheet="s", locator="row 2", theme=None, needs_manual_mapping=True))
        summary = store.summary()
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_perspective"], {"bewoners": 1, "expert": 1})
        self.assertEqual(summary["by_theme"], {"Sporten": 1, "(unmapped)": 1})
        self.assertEqual(summary["flagged_for_manual_mapping"], 1)

    def test_store_round_trips_through_json(self):
        store = SourceStore()
        store.add(SourceRecord(id="a", perspective="data", source_file="map.png", sheet=None, locator="image", theme="Sporten"))
        out = self.tmp_path / "store.json"
        store.save(out)
        reloaded = SourceStore.load(out)
        self.assertEqual(len(reloaded), 1)
        self.assertEqual(reloaded.get("a").theme, "Sporten")

    def test_map_filename_theme_lookup_unambiguous_vs_flagged(self):
        clear = self.tmp_path / "Basisbehoeftekaart Oss 13052026.png"
        clear.write_bytes(b"fake")
        records = map_parser.parse_file(str(clear))
        self.assertEqual(records[0].theme, "Actieve mobiliteit")
        self.assertFalse(records[0].needs_manual_mapping)

        ambiguous = self.tmp_path / "Sport- en speelkaart Oss 06052026.png"
        ambiguous.write_bytes(b"fake")
        records2 = map_parser.parse_file(str(ambiguous))
        self.assertIsNone(records2[0].theme)
        self.assertTrue(records2[0].needs_manual_mapping)


if __name__ == "__main__":
    unittest.main(verbosity=2)
