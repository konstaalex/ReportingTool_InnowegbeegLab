import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ingestion.parsers import resident_parser


def test_categorized_sheet_splits_theme_subtype_and_neighbourhood(categorized_workbook):
    records = resident_parser.parse_categorized_sheet(
        str(categorized_workbook), "Gecategoriseerde data", "Gecategoriseerde Resultaten Test.xlsx"
    )
    assert len(records) == 5

    jeu_de_boules = next(r for r in records if r.text == "Jeu de boules")
    assert jeu_de_boules.theme == "Sporten"
    assert jeu_de_boules.subtheme == "Behoefte"
    assert jeu_de_boules.neighbourhood == "Testbuurt"
    assert jeu_de_boules.value == 3
    assert jeu_de_boules.needs_manual_mapping is False

    sportveldjes = next(r for r in records if r.text == "Sportveldjes")
    # forward-filled neighbourhood from the row above (merged-cell style)
    assert sportveldjes.neighbourhood == "Testbuurt"
    assert sportveldjes.theme == "Sporten"

    samen_tuinieren = next(r for r in records if r.text == "Samen tuinieren")
    assert samen_tuinieren.theme == "Ontmoeten"
    assert samen_tuinieren.subtheme == "Behoefte"


def test_categorized_sheet_every_record_has_a_precise_locator(categorized_workbook):
    records = resident_parser.parse_categorized_sheet(
        str(categorized_workbook), "Gecategoriseerde data", "Gecategoriseerde Resultaten Test.xlsx"
    )
    for r in records:
        assert r.sheet == "Gecategoriseerde data"
        assert r.locator.startswith("row ")
        assert r.source_file == "Gecategoriseerde Resultaten Test.xlsx"


def test_survey_sheet_maps_known_column_flags_unknown(survey_workbook):
    records = resident_parser.parse_survey_sheet(str(survey_workbook), "Survey Test.xlsx")
    known = next(r for r in records if r.subtheme == "Wandel- en fietspaden - Belangrijk")
    assert known.theme == "Actieve mobiliteit"
    assert known.needs_manual_mapping is False

    unknown = next(r for r in records if r.subtheme == "Onbekende kolom - Belangrijk")
    assert unknown.theme is None
    assert unknown.needs_manual_mapping is True
