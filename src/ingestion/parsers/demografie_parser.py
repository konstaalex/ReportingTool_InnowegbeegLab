"""Dataperspectief: 'Data voor Demografie - <buurt> <stad>.docx'.

Content per file (all synthetic in the Oct 2026 set):
- paragraphs 'Aantal inwoners Buurt X (in 2025): 5.010' (buurt and gemeente);
- table 1: Indicator | Buurt | Gemeente | Nederland  (overgewicht, sporters,
  beweegrichtlijnen, beweegvriendelijke omgeving index);
- table 2: Indicator | Buurt | Wijk  (sub-scores of the beweegvriendelijke
  omgeving index, 0-100);
- tables 3+: two charts side by side (buurt vs gemeente) as embedded images
  with a caption. The numbers in those charts exist only as pixels, so each
  chart becomes an image record flagged needs_manual_mapping (FR3) — never
  read by guesswork. Open question 5 in docs/design.

Locators use 'tabel N, rij R' (1-based, header = rij 1) or 'alinea N'.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ..models import SourceRecord

_INHABITANTS_RE = re.compile(r"Aantal inwoners\s+(?P<who>.+?)\s*\(in\s*(?P<year>\d{4})\)\s*:\s*(?P<n>[\d.]+)", re.I)
_AREA_RE = re.compile(r"Buurt\s+(?P<buurt>.+?)\s*\(wijk\s+(?P<wijk>.+?)\),\s*Gemeente\s+(?P<gemeente>.+)", re.I)


def parse_dutch_number(text) -> Optional[float]:
    """'57,8%' -> 57.8 ; '5.010' -> 5010 ; '53' -> 53 ; '€ 31.000' -> 31000."""
    if text is None:
        return None
    t = str(text).strip().replace("€", "").replace("%", "").replace(" ", " ").strip()
    if not t:
        return None
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", t):
        t = t.replace(".", "")
    t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def detect(path: str) -> bool:
    return Path(path).suffix.lower() == ".docx" and "demografie" in Path(path).name.lower()


def parse_file(path: str, source_file_label: Optional[str] = None, case_id: Optional[str] = None) -> list[SourceRecord]:
    import docx  # python-docx; imported lazily so the rest of UC1 works without it

    source_file_label = source_file_label or Path(path).name
    doc = docx.Document(path)
    records: list[SourceRecord] = []
    area: dict = {}

    for i, p in enumerate(doc.paragraphs, start=1):
        text = " ".join(p.text.split())
        if not text:
            continue
        m = _AREA_RE.search(text)
        if m:
            area = {k: v.strip() for k, v in m.groupdict().items()}
        m = _INHABITANTS_RE.search(text)
        if m:
            locator = f"alinea {i}"
            records.append(SourceRecord(
                id=SourceRecord.make_id("data", source_file_label, None, locator),
                perspective="data", source_file=source_file_label, sheet=None, locator=locator,
                subtheme="Aantal inwoners", neighbourhood=area.get("buurt"),
                text=text, value=parse_dutch_number(m.group("n")),
                extra={"record_kind": "demografie_inhabitants", "area": m.group("who").strip(),
                       "year": int(m.group("year"))},
                case_id=case_id,
            ))

    for t_idx, table in enumerate(doc.tables, start=1):
        rows = [[" ".join(c.text.split()) for c in r.cells] for r in table.rows]
        if not rows:
            continue
        header = rows[0]
        has_images = bool(table._tbl.xpath(".//*[local-name()='blip']"))
        if has_images:
            title = header[0] if header else f"tabel {t_idx}"
            charts = []  # (area label from the row above, caption in the image cell)
            for r_i, r in enumerate(table.rows):
                for c_i, cell in enumerate(r.cells):
                    if cell._tc.xpath(".//*[local-name()='blip']"):
                        label = rows[r_i - 1][c_i] if r_i > 0 and c_i < len(rows[r_i - 1]) else ""
                        charts.append((label, " ".join(cell.text.split())))
            for c_idx, (label, caption) in enumerate(charts, start=1):
                caption = f"{label}: {caption}" if label and label != title else caption
                locator = f"tabel {t_idx}, grafiek {c_idx}"
                records.append(SourceRecord(
                    id=SourceRecord.make_id("data", source_file_label, None, locator),
                    perspective="data", source_file=source_file_label, sheet=None, locator=locator,
                    subtheme=title, neighbourhood=area.get("buurt"), text=caption,
                    extra={"record_kind": "demografie_chart_image", "chart_title": title,
                           "reason_flagged": "values only present as image pixels"},
                    needs_manual_mapping=True, case_id=case_id,
                ))
            continue
        # numeric comparison table: first column = indicator, others = areas
        areas = header[1:]
        for r_idx, row in enumerate(rows[1:], start=2):
            if not row or not row[0]:
                continue
            values = {areas[j]: parse_dutch_number(v) for j, v in enumerate(row[1:]) if j < len(areas)}
            raw = {areas[j]: v for j, v in enumerate(row[1:]) if j < len(areas)}
            locator = f"tabel {t_idx}, rij {r_idx}"
            unit = "%" if any("%" in v for v in raw.values()) else "index 0-100"
            records.append(SourceRecord(
                id=SourceRecord.make_id("data", source_file_label, None, locator),
                perspective="data", source_file=source_file_label, sheet=None, locator=locator,
                subtheme=row[0], neighbourhood=area.get("buurt"),
                text=f"{row[0]}: " + "; ".join(f"{a} {raw[a]}" for a in areas if a in raw),
                value=values.get(areas[0]) if areas else None,
                extra={"record_kind": "demografie_indicator", "table": t_idx, "areas": areas,
                       "values": values, "raw": raw, "unit": unit, "area_names": area},
                needs_manual_mapping=any(v is None for v in values.values()),
                case_id=case_id,
            ))
    return records
