"""Bewonersperspectief, Wijkscan Oss: 'Resultaten vragenlijst' = the SAME
questionnaire as the Buurtscan survey, but stored as totals per respondent
group instead of one row per respondent:

    row g     <group title>                      (e.g. '65+ en toekomstige bewoners', 'Jongvolwassenen')
    row g+1   <question, merged across its options> ...
    row g+2   <answer option per column> ...
    row g+3   <count per option>
    (blank rows, next group)

One SourceRecord per group x question block (locator = the counts row and its
column range), carrying {option: count}. Question -> theme comes from the same
config/survey_mapping.toml as the respondent-level survey, applied by
ingestion.buurtscan.apply_survey_mapping. No free text exists in this sheet;
open answers live in the coded sheets ('Gecategoriseerde data').
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl.utils import get_column_letter

from ..models import SourceRecord
from .buurtscan_survey_parser import build_blocks

SHEET = "Resultaten vragenlijst"


def detect(sheet_names: list[str]) -> bool:
    return SHEET in sheet_names


def _group_rows(ws) -> list[tuple[int, str]]:
    """(row, group title) for rows whose only filled cell is column A and
    whose next row starts a question block."""
    out = []
    for r in range(1, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        if not isinstance(a, str) or not a.strip():
            continue
        others = [ws.cell(row=r, column=c).value for c in range(2, min(ws.max_column, 12) + 1)]
        nxt = ws.cell(row=r + 1, column=1).value if r + 1 <= ws.max_row else None
        if all(v is None for v in others) and isinstance(nxt, str) and "?" in nxt:
            out.append((r, " ".join(a.split())))
    return out


def parse_file(path: str, source_file_label: Optional[str] = None, case_id: Optional[str] = None,
               neighbourhood: Optional[str] = None) -> tuple[list[SourceRecord], dict]:
    source_file_label = source_file_label or Path(path).name
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[SHEET]
    records: list[SourceRecord] = []
    meta: dict = {"sheet": SHEET, "groups": {}}
    for g_row, group in _group_rows(ws):
        qrow, orow, crow = g_row + 1, g_row + 2, g_row + 3
        blocks = build_blocks(ws, neighbourhood, question_row=qrow, option_row=orow, first_col=1)
        group_n = None
        for b in blocks:
            counts: dict[str, int] = {}
            for c in range(b.first_col, b.last_col + 1):
                v = ws.cell(row=crow, column=c).value
                label = b.options[c - b.first_col]
                if isinstance(v, (int, float)) and label is not None:
                    counts[str(label)] = int(v)
            if not counts:
                continue
            if group_n is None and "in welke buurt woont u" in b.question.lower():
                group_n = sum(counts.values())
            a, z = get_column_letter(b.first_col), get_column_letter(b.last_col)
            locator = f"rij {crow}, {a}{crow}" + (f":{z}{crow}" if a != z else "") + f" ({group}, {b.block_id})"
            records.append(SourceRecord(
                id=SourceRecord.make_id("bewoners", source_file_label, SHEET, locator),
                perspective="bewoners", source_file=source_file_label, sheet=SHEET, locator=locator,
                subtheme=b.item, neighbourhood=neighbourhood, text=b.question,
                extra={
                    "record_kind": "survey_tally",
                    "group": group,
                    "block_id": b.block_id,
                    "question_key": b.question_key,
                    "question_kind": b.kind,
                    "counts": counts,
                    "open_options": [str(b.options[c - b.first_col]) for c in b.open_cols],
                },
                needs_manual_mapping=True,   # until the config mapping is applied
                case_id=case_id,
            ))
        meta["groups"][group] = {"row": g_row, "n_from_woonbuurt": group_n, "blocks": len(blocks)}
    for r in records:
        r.extra["group_n"] = meta["groups"].get(r.extra["group"], {}).get("n_from_woonbuurt")
    return records, meta
