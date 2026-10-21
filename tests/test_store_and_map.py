import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ingestion.models import SourceRecord
from ingestion.store import SourceStore
from ingestion.parsers import map_parser


def test_store_summary_counts_by_perspective_and_theme():
    store = SourceStore()
    store.add(SourceRecord(id="a", perspective="bewoners", source_file="f.xlsx", sheet="s", locator="row 1", theme="Sporten"))
    store.add(SourceRecord(id="b", perspective="expert", source_file="f.xlsx", sheet="s", locator="row 2", theme=None, needs_manual_mapping=True))
    summary = store.summary()
    assert summary["total"] == 2
    assert summary["by_perspective"] == {"bewoners": 1, "expert": 1}
    assert summary["by_theme"] == {"Sporten": 1, "(unmapped)": 1}
    assert summary["flagged_for_manual_mapping"] == 1


def test_store_round_trips_through_json(tmp_path):
    store = SourceStore()
    store.add(SourceRecord(id="a", perspective="data", source_file="map.png", sheet=None, locator="image", theme="Sporten"))
    out = tmp_path / "store.json"
    store.save(out)
    reloaded = SourceStore.load(out)
    assert len(reloaded) == 1
    assert reloaded.get("a").theme == "Sporten"


def test_map_filename_theme_lookup_unambiguous_vs_flagged(tmp_path):
    clear = tmp_path / "Basisbehoeftekaart Oss 13052026.png"
    clear.write_bytes(b"fake")
    records = map_parser.parse_file(str(clear))
    assert records[0].theme == "Actieve mobiliteit"
    assert records[0].needs_manual_mapping is False

    ambiguous = tmp_path / "Sport- en speelkaart Oss 06052026.png"
    ambiguous.write_bytes(b"fake")
    records2 = map_parser.parse_file(str(ambiguous))
    assert records2[0].theme is None
    assert records2[0].needs_manual_mapping is True
