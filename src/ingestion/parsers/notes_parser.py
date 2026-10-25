"""Researcher notes: knowledge the research team has that no spreadsheet holds.

The Oss gap analysis (Oct 2026) showed that about one statement in six in the
human report comes from the team itself: what the experts saw on the walk
(street names, a dated jeu de boules court), what residents said at the
meeting, the project context (2,000 new homes), and literature. A tool that
writes such statements without a source is hallucinating. This file type
lets the team record them once, with a source, so they become citable
evidence like any spreadsheet cell.

Format: one Excel workbook, sheet "Notities", one note per row:

    Nr | Soort | Thema | Buurt / plek | Notitie | Bron | Datum | Aantal keer genoemd | Foto

Soort decides the perspective a note counts as (see NOTE_TYPES). `write_template`
creates an empty workbook with drop-down lists and an explanation sheet.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

import openpyxl

from ..models import THEMES, SourceRecord

SHEET = "Notities"
COLUMNS = ["Nr", "Soort", "Thema", "Buurt / plek", "Notitie", "Bron", "Datum", "Aantal keer genoemd", "Foto"]
GENERAL = "Algemeen"

# Soort -> how the note is used. 'perspective' is the SourceRecord perspective;
# the evidence layer turns 'reference' into its own 'context' perspective so a
# literature note is never mistaken for the RIVM norm.
NOTE_TYPES: dict[str, dict] = {
    "Observatie expertwandeling": {"key": "observatie", "perspective": "expert",
                                   "label": "observatie tijdens de expertwandeling"},
    "Bewonersbijeenkomst": {"key": "bijeenkomst", "perspective": "bewoners", "label": "bewonersbijeenkomst"},
    "Stakeholdergesprek": {"key": "stakeholder", "perspective": "bewoners", "label": "gesprek met stakeholders",
                           "is_stakeholder": True},
    "Projectcontext": {"key": "context", "perspective": "reference", "label": "projectcontext"},
    "Literatuur": {"key": "literatuur", "perspective": "reference", "label": "literatuur"},
}

EXPLANATION = [
    ("Waarvoor", "Leg hier vast wat het onderzoeksteam weet maar wat in geen enkel bronbestand staat. "
                 "Elke notitie wordt een bewijsstuk (O-…) waar het concept naar kan verwijzen. Wat hier niet staat, "
                 "mag de tool niet schrijven."),
    ("Soort", "Observatie expertwandeling: wat jullie ter plekke zagen (plek, toestand, foto). "
              "Bewonersbijeenkomst: wat bewoners op de bijeenkomst zeiden. Stakeholdergesprek: wat professionals zeiden. "
              "Projectcontext: feiten over het project en de opdracht (voor de Aanleiding). "
              "Literatuur: een bevinding uit een bron, met volledige bronvermelding in 'Bron'."),
    ("Thema", "Eén van de zes thema's of 'Algemeen'. Meerdere thema's scheiden met een puntkomma. "
              "Projectcontext mag leeg blijven."),
    ("Notitie", "Eén feit of waarneming per regel, zo concreet mogelijk. Geen namen of herleidbare gegevens van bewoners."),
    ("Bron", "Wie, wanneer, waar vastgelegd. Bijvoorbeeld: 'Expertwandeling 12-03-2026, foto 14' of "
             "'Bewonersbijeenkomst 15-04-2026, flip-over'. Bij literatuur de volledige verwijzing."),
    ("Aantal keer genoemd", "Optioneel, alleen bij bijeenkomst of gesprek: hoe vaak dit werd genoemd."),
    ("Foto", "Optioneel: bestandsnaam van een foto (jpg/png) in dezelfde map als dit bestand, of een volledig pad. "
             "De foto komt in het rapport bij het thema van de notitie, met de notitie als bijschrift. "
             "De eerste foto bij een notitie van de soort Projectcontext wordt de omslagfoto."),
    ("Voorbeeld 1", "Observatie expertwandeling | Formeel spelen | Staatsliedenbuurt, Van Tienhovenstraat | "
                    "De enige jeu-de-boulesbaan in de buurt is verouderd | Expertwandeling 12-03-2026, foto 31"),
    ("Voorbeeld 2", "Projectcontext | (leeg) | Euterpepark | Het bedrijventerrein wordt een woon-werkgebied met "
                    "circa 2.000 woningen | Projectopdracht gemeente Oss, 2026"),
]


def write_template(path: str | Path, overwrite: bool = False) -> Path:
    """Empty notes workbook with drop-down lists and an explanation sheet.
    Refuses to overwrite an existing file (it may hold filled-in notes)."""
    if Path(path).exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; not overwritten (it may contain notes)")
    from openpyxl.styles import Alignment, Font
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(COLUMNS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for col, width in zip("ABCDEFGHI", (6, 28, 22, 30, 70, 40, 12, 12, 28)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    soort = DataValidation(type="list", formula1='"' + ",".join(NOTE_TYPES) + '"', allow_blank=False)
    thema = DataValidation(type="list", formula1='"' + ",".join((*THEMES, GENERAL)) + '"', allow_blank=True,
                           showErrorMessage=False)   # free text allowed for 'A; B'
    ws.add_data_validation(soort)
    ws.add_data_validation(thema)
    soort.add("B2:B1000")
    thema.add("C2:C1000")
    ex = wb.create_sheet("Uitleg")
    ex.column_dimensions["A"].width = 22
    ex.column_dimensions["B"].width = 120
    for k, v in EXPLANATION:
        ex.append([k, v])
        ex.cell(ex.max_row, 1).font = Font(bold=True)
        ex.cell(ex.max_row, 2).alignment = Alignment(wrap_text=True, vertical="top")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def is_notes_workbook(path: str | Path) -> bool:
    try:
        wb = openpyxl.load_workbook(str(path), read_only=True)
    except Exception:  # noqa: BLE001
        return False
    if SHEET not in wb.sheetnames:
        return False
    header = [str(c.value or "").strip().lower() for c in next(wb[SHEET].iter_rows(min_row=1, max_row=1))]
    return "notitie" in header and "soort" in header


def _note_type(value: str) -> Optional[tuple[str, dict]]:
    v = (value or "").strip().lower()
    for name, spec in NOTE_TYPES.items():
        if v == name.lower() or v == spec["key"] or (len(v) > 4 and name.lower().startswith(v)):
            return name, spec
    return None


def _fmt_date(v) -> Optional[str]:
    if isinstance(v, (datetime, date)):
        return v.strftime("%d-%m-%Y")
    return str(v).strip() if v not in (None, "") else None


def parse_notes(path: str | Path, rel: Optional[str] = None, case_id: Optional[str] = None
                ) -> tuple[list[SourceRecord], list[str]]:
    """Returns (records, problems). A row without 'Notitie' is skipped; a row
    with an unknown Soort/Thema or without 'Bron' is still read but flagged
    (needs_manual_mapping) and named in `problems`."""
    rel = rel or Path(path).name
    wb = openpyxl.load_workbook(str(path), data_only=True)
    if SHEET not in wb.sheetnames:
        return [], [f"{rel}: werkblad '{SHEET}' ontbreekt"]
    ws = wb[SHEET]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], [f"{rel}: werkblad '{SHEET}' is leeg"]
    header = [str(h or "").strip().lower() for h in rows[0]]

    def col(name: str) -> Optional[int]:
        name = name.lower()
        for i, h in enumerate(header):
            if h == name or h.startswith(name.split(" ")[0]):
                return i
        return None

    idx = {c: col(c) for c in COLUMNS}
    if idx["Notitie"] is None or idx["Soort"] is None:
        return [], [f"{rel}: kolommen 'Soort' en 'Notitie' zijn verplicht (gevonden: {rows[0]})"]
    records: list[SourceRecord] = []
    problems: list[str] = []

    def get(row: tuple, name: str):
        i = idx[name]
        return row[i] if i is not None and i < len(row) else None

    for n, row in enumerate(rows[1:], start=2):
        text = str(get(row, "Notitie") or "").strip()
        if not text:
            continue
        flagged = False
        nt = _note_type(str(get(row, "Soort") or ""))
        if nt is None:
            problems.append(f"rij {n}: onbekende soort '{get(row, 'Soort')}' (kies uit: {', '.join(NOTE_TYPES)})")
            name, spec, flagged = "onbekend", {"key": "onbekend", "perspective": "reference", "label": "notitie"}, True
        else:
            name, spec = nt
        themes, unknown = [], []
        for t in str(get(row, "Thema") or "").replace(",", ";").split(";"):
            t = t.strip()
            if not t:
                continue
            match = next((x for x in (*THEMES, GENERAL) if x.lower() == t.lower()), None)
            (themes if match else unknown).append(match or t)
        if unknown:
            problems.append(f"rij {n}: onbekend thema {unknown} (gebruik een van de zes thema's of 'Algemeen')")
            flagged = True
        source = str(get(row, "Bron") or "").strip()
        if not source:
            problems.append(f"rij {n}: geen bron ingevuld - de notitie is niet herleidbaar")
            flagged = True
        count = get(row, "Aantal keer genoemd")
        try:
            count = int(count) if count not in (None, "") else None
        except (TypeError, ValueError):
            problems.append(f"rij {n}: 'Aantal keer genoemd' is geen getal ({count!r})")
            count = None
        photo_path = None
        photo = str(get(row, "Foto") or "").strip()
        if photo:
            cand = Path(photo) if Path(photo).is_absolute() else Path(path).parent / photo
            if cand.is_file():
                photo_path = str(cand.resolve())
            else:
                problems.append(f"rij {n}: foto '{photo}' niet gevonden naast het notitiebestand")
        locator = f"rij {n}"
        theme_list = [t for t in themes if t in THEMES]
        records.append(SourceRecord(
            id=SourceRecord.make_id(spec["perspective"], rel, SHEET, locator), perspective=spec["perspective"],
            source_file=rel, sheet=SHEET, locator=locator, theme=theme_list[0] if theme_list else None,
            subtheme=name, neighbourhood=str(get(row, "Buurt / plek") or "").strip() or None, text=text,
            value=float(count) if count is not None else None,
            extra={"record_kind": "researcher_note", "note_type": spec["key"], "note_type_label": spec["label"],
                   "themes": theme_list, "general": GENERAL in themes or not theme_list,
                   "place": str(get(row, "Buurt / plek") or "").strip() or None, "source": source or None,
                   "date": _fmt_date(get(row, "Datum")), "count": count, "nr": get(row, "Nr"),
                   "is_stakeholder": bool(spec.get("is_stakeholder")), "photo_path": photo_path,
                   "cover": bool(photo_path and spec["key"] == "context" and not any(
                       r_.extra.get("cover") for r_ in records))},
            needs_manual_mapping=flagged, case_id=case_id,
        ))
    return records, problems
