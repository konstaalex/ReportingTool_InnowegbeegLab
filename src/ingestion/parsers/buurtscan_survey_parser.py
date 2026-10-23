"""Bewonersperspectief, Buurtscan resident questionnaire (respondent level).

Shape of ``Vragenlijst buurtbewoners - <buurt> <stad>.xlsx`` (sheet ``Blad1``):

    row 1   Respondent | Vraag | <question text, merged across its answer columns> ...
    row 2   (blank)    | Antwoord | <answer option per column> ...
    row 3+  one row per respondent: a cell is filled when that option was chosen
            (Likert/choice: the option label; rating: the number; open
            fields: free text)
    footer  'Totaal aantal antwoorden per kolom (n = N) ...' then one row
            of per-column totals

A *question block* is one merged span in row 1 (a single unmerged cell is a
one-column block). Several blocks can share the same question text (the
"Hoe belangrijk ..." matrix is seven blocks, one per item) - the item name
is the common prefix of the option labels ("Wandel- en fietspaden - ...").

Records produced (all perspective="bewoners"):
- one ``survey_answer`` record per respondent per block they answered;
- one ``survey_column_total`` record per block, holding the footer totals,
  used by validate_against_footer() to prove the parser counts what the
  spreadsheet itself counts.

The question->theme mapping is NOT decided here: it lives in
config/survey_mapping.toml and is applied by apply_theme_mapping(), so the
interpretation stays visible and editable by researchers (FR3, brief rule 10).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl.utils import get_column_letter

from ..models import SourceRecord

OPEN_LABELS = {
    "anders, namelijk:", "omdat:", "toelichting:", "eventuele toelichting:", "open-ended response",
}
FOOTER_PREFIX = "totaal aantal antwoorden"
_RATING_LABEL = re.compile(r"^[\s-]*(\d+)\s*$")


def rating_value(option) -> Optional[int]:
    """1, 3.0, '- 3', ' 4 ' -> int; anything else -> None (rating scales are labelled differently per export)."""
    if isinstance(option, (int, float)) and not isinstance(option, bool):
        return int(option)
    m = _RATING_LABEL.match(str(option)) if option is not None else None
    return int(m.group(1)) if m else None
_N_RE = re.compile(r"n\s*=\s*(\d+)")


@dataclass
class QuestionBlock:
    block_id: str                 # Q01, Q02 ... in sheet order (stable for one file shape)
    question: str                 # row-1 text as written
    question_key: str             # neighbourhood name replaced by {buurt}
    item: Optional[str]           # matrix item ("Wandel- en fietspaden") if any
    first_col: int                # 1-based
    last_col: int
    options: list[Optional[str]] = field(default_factory=list)   # row-2 label per column
    open_cols: list[int] = field(default_factory=list)           # 1-based columns holding free text
    kind: str = "choice"          # rating | likert | choice | multi | open

    @property
    def col_range(self) -> str:
        a, b = get_column_letter(self.first_col), get_column_letter(self.last_col)
        return a if a == b else f"{a}:{b}"

    def to_dict(self) -> dict:
        return asdict(self)


def detect(sheet_names: list[str], path: Optional[str] = None) -> bool:
    if path is None:
        return False
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
    return len(first) > 1 and str(first[0]).strip() == "Respondent" and str(first[1]).strip() == "Vraag"


def _norm_question(text: str, neighbourhood: Optional[str]) -> str:
    t = " ".join(str(text).split())
    if neighbourhood:
        t = t.replace(neighbourhood, "{buurt}")
    return t


def _common_item(options: list[Optional[str]]) -> Optional[str]:
    prefixes = {o.split(" - ", 1)[0].strip() for o in options if isinstance(o, str) and " - " in o}
    return prefixes.pop() if len(prefixes) == 1 else None


def build_blocks(ws, neighbourhood: Optional[str], question_row: int = 1, option_row: int = 2,
                 first_col: int = 3, max_col: Optional[int] = None) -> list[QuestionBlock]:
    max_col = max_col or ws.max_column
    spans: dict[int, int] = {}
    for rng in ws.merged_cells.ranges:
        if rng.min_row == question_row and rng.max_row == question_row:
            spans[rng.min_col] = rng.max_col
    blocks: list[QuestionBlock] = []
    col = first_col  # respondent-level export: A = Respondent, B = Vraag/Antwoord
    n = 0
    while col <= max_col:
        q = ws.cell(row=question_row, column=col).value
        end = spans.get(col, col)
        if q is None:
            col = end + 1
            continue
        options = [ws.cell(row=option_row, column=c).value for c in range(col, end + 1)]
        options = [str(o).strip() if isinstance(o, str) else o for o in options]
        open_cols = [col + i for i, o in enumerate(options)
                     if o is None or (isinstance(o, str) and o.strip().lower() in OPEN_LABELS)]
        closed = [o for i, o in enumerate(options) if col + i not in open_cols]
        if closed and all(rating_value(o) is not None for o in closed):
            kind = "rating"
        elif closed and all(isinstance(o, str) and " - " in o for o in closed):
            kind = "likert"
        elif not closed:
            kind = "open"
        else:
            kind = "multi" if "meerdere antwoorden" in str(q).lower() else "choice"
        # one merged header over several matrix items ('Wandel- en fietspaden - ...',
        # 'Natuur/ groen - ...'): split into one block per item, like the per-item exports
        segments = [(col, end)]
        if kind == "likert" and _common_item(closed) is None:
            segments, start = [], col
            for c in range(col + 1, end + 2):
                prev = str(options[c - 1 - col]).split(" - ", 1)[0]
                cur = str(options[c - col]).split(" - ", 1)[0] if c <= end else None
                if cur != prev:
                    segments.append((start, c - 1))
                    start = c
        for a, z in segments:
            seg_opts = options[a - col:z - col + 1]
            seg_open = [c for c in open_cols if a <= c <= z]
            seg_closed = [o for i, o in enumerate(seg_opts) if a + i not in seg_open]
            n += 1
            blocks.append(QuestionBlock(
                block_id=f"Q{n:02d}",
                question=" ".join(str(q).split()),
                question_key=_norm_question(q, neighbourhood),
                item=_common_item(seg_closed) if kind == "likert" else None,
                first_col=a, last_col=z, options=seg_opts, open_cols=seg_open, kind=kind,
            ))
        col = end + 1
    return blocks


def _respondent_rows(ws) -> tuple[list[int], Optional[int], Optional[int]]:
    """(respondent excel rows, footer label row, footer totals row)."""
    rows, footer = [], None
    for r in range(3, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        if isinstance(a, str) and a.strip().lower().startswith(FOOTER_PREFIX):
            footer = r
            break
        if isinstance(a, (int, float)):
            rows.append(r)
    totals = footer + 1 if footer and footer + 1 <= ws.max_row else None
    return rows, footer, totals


def parse_file(path: str, source_file_label: Optional[str] = None, case_id: Optional[str] = None,
               neighbourhood: Optional[str] = None) -> tuple[list[SourceRecord], dict]:
    """Returns (records, meta). meta carries the question blocks, respondent
    count and footer n, which the evidence layer and validation need."""
    source_file_label = source_file_label or Path(path).name
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    sheet = ws.title
    blocks = build_blocks(ws, neighbourhood)
    resp_rows, footer_row, totals_row = _respondent_rows(ws)
    footer_n = None
    if footer_row:
        m = _N_RE.search(str(ws.cell(row=footer_row, column=1).value))
        footer_n = int(m.group(1)) if m else None

    records: list[SourceRecord] = []
    for r in resp_rows:
        respondent = int(ws.cell(row=r, column=1).value)
        for b in blocks:
            selected: list = []
            open_text: list[str] = []
            open_labels: list[str] = []
            mismatches: list[str] = []
            for c in range(b.first_col, b.last_col + 1):
                v = ws.cell(row=r, column=c).value
                if v is None or (isinstance(v, str) and not v.strip()):
                    continue
                if c in b.open_cols:
                    open_text.append(str(v).strip())
                    open_labels.append(str(b.options[c - b.first_col]))
                else:
                    opt = b.options[c - b.first_col]
                    # the COLUMN is the answer; a differing cell value is recorded, not trusted
                    if b.kind == "rating" and rating_value(v) != rating_value(opt):
                        mismatches.append(f"{get_column_letter(c)}{r}: value {v!r} in column for {opt!r}")
                    selected.append(rating_value(opt) if b.kind == "rating" else opt)
            if not selected and not open_text:
                continue
            a, z = get_column_letter(b.first_col), get_column_letter(b.last_col)
            locator = f"rij {r}, {a}{r}" + (f":{z}{r}" if a != z else "") + f" ({b.block_id})"
            value = float(selected[0]) if b.kind == "rating" and selected and isinstance(selected[0], (int, float)) else None
            text_parts = [str(s) for s in selected] + open_text
            records.append(SourceRecord(
                id=SourceRecord.make_id("bewoners", source_file_label, sheet, locator),
                perspective="bewoners",
                source_file=source_file_label,
                sheet=sheet,
                locator=locator,
                theme=None,                      # applied later from config (FR3)
                subtheme=b.item,
                neighbourhood=neighbourhood,
                text=" | ".join(text_parts),
                value=value,
                extra={
                    "record_kind": "survey_answer",
                    "respondent": respondent,
                    "block_id": b.block_id,
                    "question_key": b.question_key,
                    "question_kind": b.kind,
                    "selected": selected,
                    "open_text": open_text,
                    "open_labels": open_labels,
                    "contains_free_text": bool(open_text),
                    **({"cell_value_mismatch": mismatches} if mismatches else {}),
                },
                needs_manual_mapping=True,       # until a theme mapping is applied
                case_id=case_id,
            ))

    if totals_row:
        for b in blocks:
            totals = {}
            for c in range(b.first_col, b.last_col + 1):
                v = ws.cell(row=totals_row, column=c).value
                if isinstance(v, (int, float)):
                    label = b.options[c - b.first_col]
                    totals[str(label)] = int(v)
            locator = f"rij {totals_row}, {b.col_range} ({b.block_id}, totaalrij)"
            records.append(SourceRecord(
                id=SourceRecord.make_id("bewoners", source_file_label, sheet, locator),
                perspective="bewoners", source_file=source_file_label, sheet=sheet, locator=locator,
                subtheme=b.item, neighbourhood=neighbourhood, text=b.question,
                extra={"record_kind": "survey_column_total", "block_id": b.block_id,
                       "question_key": b.question_key, "totals": totals, "footer_n": footer_n},
                needs_manual_mapping=True, case_id=case_id,
            ))

    meta = {
        "sheet": sheet,
        "blocks": [b.to_dict() for b in blocks],
        "respondents": len(resp_rows),
        "respondent_rows": [resp_rows[0], resp_rows[-1]] if resp_rows else [],
        "footer_n": footer_n,
        "footer_row": footer_row,
    }
    return records, meta


def validate_against_footer(records: list[SourceRecord], meta: dict) -> list[str]:
    """Recount every closed option and open column from the respondent
    records and compare with the spreadsheet's own footer totals. Returns a
    list of human-readable mismatches (empty list = parser agrees with the
    sheet). This is the deterministic sanity check the brief asks for
    before any statistic reaches a report."""
    problems: list[str] = []
    if meta.get("footer_n") is not None and meta["footer_n"] != meta["respondents"]:
        problems.append(f"footer says n = {meta['footer_n']}, parser found {meta['respondents']} respondent rows")
    counts: dict[tuple[str, str], int] = {}
    blocks = {b["block_id"]: b for b in meta["blocks"]}
    for r in records:
        if r.extra.get("record_kind") != "survey_answer":
            continue
        b = blocks[r.extra["block_id"]]
        for s in r.extra["selected"]:
            counts[(b["block_id"], str(s))] = counts.get((b["block_id"], str(s)), 0) + 1
        for label in r.extra.get("open_labels", []):
            counts[(b["block_id"], label)] = counts.get((b["block_id"], label), 0) + 1
    for r in records:
        if r.extra.get("record_kind") != "survey_column_total":
            continue
        bid = r.extra["block_id"]
        b = blocks[bid]
        for label, expected in r.extra["totals"].items():
            got = counts.get((bid, label), 0)
            if got != expected:
                problems.append(f"{bid} '{b['question'][:50]}' option '{label}': footer {expected}, parser {got}")
    return problems
