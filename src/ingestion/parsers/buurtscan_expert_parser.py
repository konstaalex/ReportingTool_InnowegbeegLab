"""Expertperspectief, Buurtscan format (Oct 2026 data set).

One sheet ``Score per indicator``:

    row 1     Stad | <stad> | Buurt | <buurt>
    rows 2-9  scale legend (1 = heel slecht ... 5 = uitstekend)
    header    Thema's | Indicatoren | Stelling nummer | Stelling | Score | Gemiddelde score | Opmerkingen
    rows      one per statement; theme and indicator are merged cells
              (only the first row carries the value -> forward-fill)

Differs from the Oss shape (expert_parser.py): one consensus score per
statement instead of five evaluator columns, statement numbers restart per
theme, and remarks sit on the statement row. One SourceRecord per statement;
the score cell and remark cell are both kept in ``extra['cells']`` so a
claim quoting the remark can be shown the exact cell (FR7).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl.utils import get_column_letter

from ..models import SourceRecord
from ..theme_tagger import normalise_theme

SHEET = "Score per indicator"
HEADER_FIRST_CELL = "Thema's"
SCALE = {1: "heel slecht (of niet aanwezig)", 2: "slecht", 3: "neutraal", 4: "goed", 5: "uitstekend"}


def detect(sheet_names: list[str], path: Optional[str] = None) -> bool:
    """Buurtscan expert files have exactly the 'Score per indicator' sheet and
    no 'Score <buurt>' companion sheet (that combination is the Oss shape)."""
    names = list(sheet_names)
    if SHEET not in names:
        return False
    return not any(n.lower().startswith(("score ", "scores ")) and n != SHEET for n in names)


def _find_header(rows: list[tuple]) -> Optional[int]:
    for i, r in enumerate(rows):
        if r and isinstance(r[0], str) and r[0].strip() == HEADER_FIRST_CELL:
            return i
    return None


def parse_file(path: str, source_file_label: Optional[str] = None, case_id: Optional[str] = None) -> list[SourceRecord]:
    source_file_label = source_file_label or Path(path).name
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[SHEET]
    rows = [tuple(c.value for c in row) for row in ws.iter_rows()]
    header_idx = _find_header(rows)
    if header_idx is None:
        return []

    first = rows[0]
    stad = first[1] if len(first) > 1 else None
    buurt = first[3] if len(first) > 3 else None
    neighbourhood = str(buurt).strip() if buurt else None

    header = [str(h).strip() if h is not None else "" for h in rows[header_idx]]

    def col(name: str, default: int) -> int:
        return header.index(name) if name in header else default

    c_theme, c_ind, c_nr, c_stel = col("Thema's", 0), col("Indicatoren", 1), col("Stelling nummer", 2), col("Stelling", 3)
    c_score, c_opm = col("Score", 4), col("Opmerkingen", 6)

    records: list[SourceRecord] = []
    cur_theme_raw: Optional[str] = None
    cur_indicator: Optional[str] = None
    for idx in range(header_idx + 1, len(rows)):
        row = rows[idx]
        excel_row = idx + 1
        if not row or all(v is None for v in row):
            continue
        if row[c_theme] is not None:
            cur_theme_raw = str(row[c_theme]).strip()
            cur_indicator = None  # a new theme block always restates its indicator
        if row[c_ind] is not None:
            cur_indicator = str(row[c_ind]).strip()
        nr = row[c_nr] if c_nr < len(row) else None
        if not isinstance(nr, (int, float)):
            continue
        statement = row[c_stel] if c_stel < len(row) else None
        score = row[c_score] if c_score < len(row) else None
        remark = row[c_opm] if c_opm < len(row) else None
        theme = normalise_theme(cur_theme_raw)
        score_cell = f"{get_column_letter(c_score + 1)}{excel_row}"
        remark_cell = f"{get_column_letter(c_opm + 1)}{excel_row}"
        locator = f"rij {excel_row} ({get_column_letter(c_theme + 1)}{excel_row}:{remark_cell})"
        records.append(SourceRecord(
            id=SourceRecord.make_id("expert", source_file_label, SHEET, locator),
            perspective="expert",
            source_file=source_file_label,
            sheet=SHEET,
            locator=locator,
            theme=theme,
            subtheme=cur_indicator,
            neighbourhood=neighbourhood,
            text=str(statement).strip() if statement else None,
            value=float(score) if isinstance(score, (int, float)) else None,
            extra={
                "record_kind": "expert_statement",
                "statement_number": int(nr),
                "remark": str(remark).strip() if isinstance(remark, str) and remark.strip() else None,
                "cells": {"score": score_cell, "remark": remark_cell},
                "scale": "1-5",
                "stad": str(stad).strip() if stad else None,
                "theme_raw": cur_theme_raw,
            },
            needs_manual_mapping=theme is None or not isinstance(score, (int, float)),
            case_id=case_id,
        ))
    return records
