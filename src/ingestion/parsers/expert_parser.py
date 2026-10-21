"""Expertperspectief: per-neighbourhood scores + the cross-neighbourhood totals.

Two workbook shapes:

- `Expertwandeling resultaten Oss <buurt>.xlsx` — sheet "Score <buurt>" has
  one row per numbered statement (evaluator scores A/E/F/L/R, Gemiddelde,
  Definitief, Opmerkingen) but NO theme column; sheet "Score per indicator"
  has the theme/indicator per statement number, with the theme column
  forward-filled (blank = same theme as the row above). We join the two on
  statement number so every scored row ends up with a theme.
- `Totaal resultaten Expertwandeling Oss maart 2026.xlsx` — sheet
  "Totaal scores" (per-indicator averages across the three neighbourhoods,
  theme column forward-filled the same way) and sheet "Opmerkingen" (free
  text, grouped by neighbourhood column and, within it, by a theme-name
  cell acting as a section header rather than a row of its own).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import openpyxl

from ..models import SourceRecord, THEMES
from ..theme_tagger import normalise_theme

SCORE_PER_INDICATOR_SHEET = "Score per indicator"
TOTAAL_SHEET = "Totaal scores"
OPMERKINGEN_SHEET = "Opmerkingen"


def detect(sheet_names: list[str]) -> bool:
    names = set(sheet_names)
    if SCORE_PER_INDICATOR_SHEET in names and any(n.lower().startswith("score ") or n.lower().startswith("scores ") for n in names):
        return True
    return TOTAAL_SHEET in names or OPMERKINGEN_SHEET in names


def _forward_fill(values: list):
    out = []
    current = None
    for v in values:
        if v is not None:
            current = v
        out.append(current)
    return out


def _load_indicator_lookup(path: str) -> dict[int, tuple[Optional[str], Optional[str]]]:
    """statement number -> (theme, indicator), from 'Score per indicator'."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[SCORE_PER_INDICATOR_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {}
    themes_col = _forward_fill([r[0] if len(r) > 0 else None for r in rows])
    indicators_col = _forward_fill([r[1] if len(r) > 1 else None for r in rows])
    lookup: dict[int, tuple[Optional[str], Optional[str]]] = {}
    for i, row in enumerate(rows[1:], start=1):
        stelling = row[2] if len(row) > 2 else None
        if isinstance(stelling, (int, float)):
            lookup[int(stelling)] = (themes_col[i], indicators_col[i])
    return lookup


def parse_score_sheet(path: str, sheet_name: str, neighbourhood: str, source_file_label: Optional[str] = None) -> list[SourceRecord]:
    source_file_label = source_file_label or Path(path).name
    indicator_lookup = _load_indicator_lookup(path)

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(h).strip() if h is not None else "" for h in rows[0]]

    def col(name: str) -> Optional[int]:
        return header.index(name) if name in header else None

    vraag_col = col("Vraag")
    gem_col = col("Gemiddelde")
    def_col = col("Definitief")
    opm_col = col("Opmerkingen")
    eval_cols = [i for i, h in enumerate(header) if h and len(h) <= 2 and i not in (vraag_col,)]

    records: list[SourceRecord] = []
    current_section: Optional[str] = None

    for row_idx, row in enumerate(rows[1:], start=2):
        if vraag_col is None or vraag_col >= len(row):
            continue
        vraag = row[vraag_col]
        if vraag is None:
            continue
        if not isinstance(vraag, (int, float)):
            current_section = str(vraag).strip()
            continue

        stelling_nr = int(vraag)
        theme_raw, indicator = indicator_lookup.get(stelling_nr, (None, None))
        theme = normalise_theme(theme_raw)
        gemiddelde = row[gem_col] if gem_col is not None and gem_col < len(row) else None
        opmerking = row[opm_col] if opm_col is not None and opm_col < len(row) else None
        evaluator_scores = {
            header[c]: row[c] for c in eval_cols
            if c < len(row) and isinstance(row[c], (int, float))
        }

        locator = f"row {row_idx} (statement {stelling_nr})"
        rec_id = SourceRecord.make_id("expert", source_file_label, sheet_name, locator)
        records.append(SourceRecord(
            id=rec_id,
            perspective="expert",
            source_file=source_file_label,
            sheet=sheet_name,
            locator=locator,
            theme=theme,
            subtheme=indicator,
            neighbourhood=neighbourhood,
            text=opmerking if isinstance(opmerking, str) else None,
            value=float(gemiddelde) if isinstance(gemiddelde, (int, float)) else None,
            extra={
                "statement_number": stelling_nr,
                "section": current_section,
                "evaluator_scores": evaluator_scores,
            },
            needs_manual_mapping=theme is None,
        ))
    return records


def parse_totaal_scores(path: str, source_file_label: Optional[str] = None) -> list[SourceRecord]:
    source_file_label = source_file_label or Path(path).name
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[TOTAAL_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        return []
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    themes_col = _forward_fill([r[0] if len(r) > 0 else None for r in rows])
    indicators_col = _forward_fill([r[1] if len(r) > 1 else None for r in rows])

    neighbourhood_cols = [i for i, h in enumerate(header) if h and h not in ("Indicator", "Stelling nummer", "Stelling") and "gemiddelde" not in h.lower() and "score per indicator" not in h.lower()]

    records: list[SourceRecord] = []
    for row_idx, row in enumerate(rows[1:], start=2):
        theme = normalise_theme(themes_col[row_idx - 1])
        indicator = indicators_col[row_idx - 1]
        for c in neighbourhood_cols:
            if c >= len(row) or row[c] is None:
                continue
            neighbourhood = header[c]
            locator = f"row {row_idx}, col {c + 1} ({neighbourhood})"
            rec_id = SourceRecord.make_id("expert", source_file_label, TOTAAL_SHEET, locator)
            records.append(SourceRecord(
                id=rec_id,
                perspective="expert",
                source_file=source_file_label,
                sheet=TOTAAL_SHEET,
                locator=locator,
                theme=theme,
                subtheme=indicator,
                neighbourhood=neighbourhood,
                value=float(row[c]) if isinstance(row[c], (int, float)) else None,
                needs_manual_mapping=theme is None,
            ))
    return records


def parse_opmerkingen(path: str, source_file_label: Optional[str] = None) -> list[SourceRecord]:
    """Free-text remarks, grouped by neighbourhood column and a theme-name
    cell acting as that column's section header (not its own data row)."""
    source_file_label = source_file_label or Path(path).name
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[OPMERKINGEN_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    neighbourhood_header = rows[0]
    themes_set = set(THEMES)

    current_theme_by_col: dict[int, Optional[str]] = {}
    records: list[SourceRecord] = []

    for row_idx, row in enumerate(rows[1:], start=2):
        for c, value in enumerate(row):
            if value is None:
                continue
            text = str(value).strip()
            if text in themes_set:
                current_theme_by_col[c] = text
                continue
            neighbourhood = neighbourhood_header[c] if c < len(neighbourhood_header) else None
            theme = current_theme_by_col.get(c)
            locator = f"row {row_idx}, col {c + 1}"
            rec_id = SourceRecord.make_id("expert", source_file_label, OPMERKINGEN_SHEET, locator)
            records.append(SourceRecord(
                id=rec_id,
                perspective="expert",
                source_file=source_file_label,
                sheet=OPMERKINGEN_SHEET,
                locator=locator,
                theme=theme,
                neighbourhood=str(neighbourhood) if neighbourhood else None,
                text=text,
                needs_manual_mapping=theme is None,
            ))
    return records


def parse_file(path: str, neighbourhood: Optional[str] = None, source_file_label: Optional[str] = None) -> list[SourceRecord]:
    source_file_label = source_file_label or Path(path).name
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    names = wb.sheetnames
    records: list[SourceRecord] = []

    for name in names:
        if name.lower().startswith("score ") or name.lower().startswith("scores "):
            nb = neighbourhood or name.split(" ", 1)[1].strip()
            records.extend(parse_score_sheet(path, name, nb, source_file_label))
    if TOTAAL_SHEET in names:
        records.extend(parse_totaal_scores(path, source_file_label))
    if OPMERKINGEN_SHEET in names:
        records.extend(parse_opmerkingen(path, source_file_label))
    return records
