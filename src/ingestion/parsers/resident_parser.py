"""Bewonersperspectief: coded qualitative data + raw survey tallies.

Handles the two sheet shapes seen in `Gecategoriseerde Resultaten Oss.xlsx`:

- "Gecategoriseerde data" / "Samengevoegde data": a two-row header. Row 1
  carries theme names, each spanning three (label, Aantal) column pairs
  (Behoefte, Belemmering, Positieve bevinding). Row 2 carries those three
  subtype labels. Column A carries a neighbourhood/group label that only
  appears on the first row of its block (merged-cell style) and must be
  forward-filled down.
- "Resultaten vragenlijst": a flatter respondent-tally sheet. Column labels
  in row 3 (e.g. "Wandel- en fietspaden - Belangrijk") are mapped to a
  theme via theme_tagger.theme_for_survey_column; row 4+ are counts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import openpyxl

from ..models import SourceRecord
from ..theme_tagger import normalise_theme, theme_for_survey_column

CATEGORIZED_SHEET_NAMES = {"Gecategoriseerde data", "Samengevoegde data"}
SURVEY_SHEET_NAME = "Resultaten vragenlijst"


def detect(sheet_names: list[str]) -> bool:
    return bool(CATEGORIZED_SHEET_NAMES & set(sheet_names)) or SURVEY_SHEET_NAME in sheet_names


def parse_categorized_sheet(path: str, sheet_name: str, source_file_label: str) -> list[SourceRecord]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 3:
        return []
    header_theme, header_subtype = rows[0], rows[1]
    max_col = max(len(header_theme), len(header_subtype))

    records: list[SourceRecord] = []
    current_neighbourhood: Optional[str] = None

    for row_idx, row in enumerate(rows[2:], start=3):
        if row and row[0] is not None:
            current_neighbourhood = str(row[0]).strip()
        if not any(row):
            continue

        current_theme: Optional[str] = None
        col = 1
        while col < max_col:
            if col < len(header_theme) and header_theme[col] is not None:
                current_theme = str(header_theme[col]).strip()
            subtype = header_subtype[col] if col < len(header_subtype) else None
            label = row[col] if col < len(row) else None
            count = row[col + 1] if col + 1 < len(row) else None

            if subtype is not None and label is not None:
                theme = normalise_theme(current_theme)
                locator = f"row {row_idx}, cols {col + 1}-{col + 2}"
                rec_id = SourceRecord.make_id("bewoners", source_file_label, sheet_name, locator)
                records.append(SourceRecord(
                    id=rec_id,
                    perspective="bewoners",
                    source_file=source_file_label,
                    sheet=sheet_name,
                    locator=locator,
                    theme=theme,
                    subtheme=str(subtype).strip(),
                    neighbourhood=current_neighbourhood,
                    text=str(label).strip(),
                    value=float(count) if isinstance(count, (int, float)) else None,
                    needs_manual_mapping=theme is None,
                ))
            col += 2
    return records


def parse_survey_sheet(path: str, source_file_label: str, sheet_name: str = SURVEY_SHEET_NAME) -> list[SourceRecord]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 4:
        return []
    # Row index 2 (third row, 0-based) carries the flat column labels in
    # this workbook's layout; row index 3+ carry per-group tallies.
    column_labels = rows[2]
    records: list[SourceRecord] = []

    for row_idx, row in enumerate(rows[3:], start=4):
        if not any(row):
            continue
        for col_idx, value in enumerate(row):
            if value is None:
                continue
            label = column_labels[col_idx] if col_idx < len(column_labels) else None
            if label is None:
                continue
            theme = theme_for_survey_column(str(label))
            locator = f"row {row_idx}, col {col_idx + 1} ({label})"
            rec_id = SourceRecord.make_id("bewoners", source_file_label, sheet_name, locator)
            records.append(SourceRecord(
                id=rec_id,
                perspective="bewoners",
                source_file=source_file_label,
                sheet=sheet_name,
                locator=locator,
                theme=theme,
                subtheme=str(label),
                text=str(label),
                value=float(value) if isinstance(value, (int, float)) else None,
                needs_manual_mapping=theme is None,
            ))
    return records


def parse_file(path: str, source_file_label: Optional[str] = None) -> list[SourceRecord]:
    source_file_label = source_file_label or Path(path).name
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    records: list[SourceRecord] = []
    for name in wb.sheetnames:
        if name in CATEGORIZED_SHEET_NAMES:
            records.extend(parse_categorized_sheet(path, name, source_file_label))
        elif name == SURVEY_SHEET_NAME:
            records.extend(parse_survey_sheet(path, source_file_label, name))
    return records
