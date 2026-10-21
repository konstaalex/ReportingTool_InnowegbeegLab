import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ingestion.parsers import expert_parser


def test_score_sheet_joins_theme_from_score_per_indicator(expert_neighbourhood_workbook):
    records = expert_parser.parse_score_sheet(
        str(expert_neighbourhood_workbook), "Score Testbuurt", "Testbuurt"
    )
    assert len(records) == 2  # statements 4 and 5; the "Sportplekken" section row is skipped

    stmt4 = next(r for r in records if r.extra["statement_number"] == 4)
    assert stmt4.theme == "Sporten"
    assert stmt4.subtheme == "Variatie"
    assert stmt4.neighbourhood == "Testbuurt"
    assert stmt4.value == 3
    assert stmt4.text == "Twee velden aanwezig"
    assert stmt4.extra["evaluator_scores"] == {"A": 3, "E": 3, "F": 3, "L": 3, "R": 3}
    assert stmt4.extra["section"] == "Sportplekken"


def test_totaal_scores_forward_fills_theme_per_neighbourhood(expert_totaal_workbook):
    records = expert_parser.parse_totaal_scores(str(expert_totaal_workbook))
    # 2 indicator rows x 3 neighbourhood columns = 6 records
    assert len(records) == 6
    assert all(r.theme == "Sporten" for r in records)
    neighbourhoods = {r.neighbourhood for r in records}
    assert neighbourhoods == {"Testbuurt A", "Testbuurt B", "Testbuurt C"}


def test_opmerkingen_uses_theme_cell_as_section_header_not_a_record(expert_totaal_workbook):
    records = expert_parser.parse_opmerkingen(str(expert_totaal_workbook))
    assert len(records) == 2
    a = next(r for r in records if r.neighbourhood == "Testbuurt A")
    assert a.theme == "Sporten"
    assert a.text == "Twee velden, weinig licht"
    b = next(r for r in records if r.neighbourhood == "Testbuurt B")
    assert b.theme == "Sporten"
    assert b.text == "Verouderd materiaal"
    # the literal word "Sporten" itself must never show up as a record's text
    assert all(r.text != "Sporten" for r in records)
