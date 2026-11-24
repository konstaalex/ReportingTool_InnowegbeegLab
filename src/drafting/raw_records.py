"""Raw source records for the E1R baseline (docs/experiments/e4-e1r-design-decisions.md, R1-R3).

E1R is E1 without the evidence layer: the model gets the source records behind the section's
evidence items (the union of their `source_record_ids`), as one Markdown pipe table per source
sheet. Every row is one SourceRecord, with its record id as the first column and the sheet's own
column headers where the record keeps them.

Not shown, because they come from this tool and not from the source: parser cross-checks, mapping
flags, file checksums and image-processing parameters.

The model cites record ids. `raw_pack` gives the verifier one Evidence-like item per record, so
the same checks run. Its number check accepts only numbers that stand in the cited row: a correctly
computed share is flagged, a known bias against E1R (R5), which is why U is secondary for E1R.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Optional

from evidence.models import Evidence, EvidencePack
from ingestion.models import SourceRecord
from ingestion.store import SourceStore

ID_COLUMN = "record-id"


def _fmt(v) -> str:
    """Cell text: Dutch decimal comma (as Excel shows it here), no line breaks, pipes escaped."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "ja" if v else "nee"
    if isinstance(v, float):
        v = f"{v:.4f}".rstrip("0").rstrip(".").replace(".", ",")
    elif isinstance(v, (list, tuple)):
        v = "; ".join(_fmt(x) for x in v)
    return " ".join(str(v).split()).replace("|", "\\|")


def _tally_header(question: Optional[str], item: Optional[str], option) -> str:
    """Question text stays in the header (R2); the item is added when the option does not name it."""
    q, opt = " ".join(str(question or "").split()), str(option)
    if item and str(item).lower() not in opt.lower():
        q = f"{q} {item}".strip()
    return f"{q} › {opt}" if q else opt


def record_row(r: SourceRecord, include_free_text: bool = True) -> "OrderedDict[str, str]":
    """Column header -> cell text for one record (without the id column)."""
    x, kind = r.extra, r.extra.get("record_kind")
    row: "OrderedDict[str, object]" = OrderedDict()
    if kind == "expert_statement":
        row["Vraag"] = x.get("statement_number")
        row["Stelling"] = x.get("statement") if "statement" in x else r.text
        row["Thema"], row["Indicator"], row["Buurt"] = r.theme, r.subtheme, r.neighbourhood
        for name, score in (x.get("evaluator_scores") or {}).items():
            row[str(name)] = score
        row["Gemiddelde"], row["Opmerkingen"] = r.value, x.get("remark")
    elif kind == "expert_section_remark":
        row["Stelling"], row["Thema"], row["Buurt"] = x.get("section"), r.theme, r.neighbourhood
        row["Opmerkingen"] = r.text
    elif kind == "expert_remark_sheet":
        row["Thema"], row["Buurt"], row["Opmerking"] = r.theme, r.neighbourhood, r.text
    elif kind == "survey_tally":
        row["Groep"] = x.get("group")
        for option, n in (x.get("counts") or {}).items():
            row[_tally_header(r.text, r.subtheme, option)] = n
    elif kind == "survey_answer":
        row["Respondent"] = x.get("respondent")
        head = " ".join(str(x.get("question_key") or "").split())
        if r.subtheme and str(r.subtheme).lower() not in head.lower():
            head = f"{head} {r.subtheme}".strip()
        parts = [_fmt(s) for s in x.get("selected") or []]
        if include_free_text:
            parts += [_fmt(t) for t in x.get("open_text") or []]
        row[head or "Antwoord"] = "; ".join(parts)
    elif kind == "survey_column_total":
        row["Respondent"] = "totaalrij"
        for option, n in (x.get("totals") or {}).items():
            row[_tally_header(r.text, r.subtheme, option)] = n
    elif kind == "coded_response":
        label = str(x.get("response_type") or r.subtheme)
        row["Groep"], row["Thema"] = x.get("group"), r.theme
        row[label], row[f"{label}: Aantal"] = r.text, r.value
    elif kind == "demografie_indicator":
        row["Indicator"] = r.subtheme
        for area, v in (x.get("raw") or {}).items():
            row[str(area)] = v
    elif kind == "image_measurement":
        tests = x.get("measure_kind") in ("shares", "coverage")
        row["Kaart"], row["Meting"], row["Gebied"] = x.get("title"), x.get("measure"), x.get("area")
        for k, v in (x.get("counts") or {}).items() if x.get("measure_kind") == "counts" else ():
            row[f"Aantal: {k}"] = v
        for k, v in (x.get("shares") or {}).items():
            row[f"Aandeel: {k}"] = v
        cov = x.get("coverage") or {}
        if cov:
            row["Woningen binnen de zone (%)"], row["Oppervlak binnen de zone (%)"] = cov.get("homes_pct"), cov.get("area_pct")
        # the same link to the rule that E1's statement gives (builder.image_evidence)
        row["Toetst RIVM-vuistregel" if tests else "Hoort bij RIVM-vuistregel"] = x.get("rules") or None
        row["Alleen onderzoeksgebied"] = x.get("study_area_only")
        row["Waarschuwing"] = x.get("warnings") or None
    elif kind in ("image_reading", "image_observation"):
        row["Afbeelding"], row["Grafiek"], row["Gebied"] = x.get("title"), x.get("chart"), x.get("area")
        row["Label"], row["Waarde"] = x.get("label"), x.get("as_printed")
        if kind == "image_observation":
            row["Beschrijving"] = r.text
    elif kind == "image":
        row["Kaart"] = r.text
    elif kind == "norm_rule":
        row["Vuistregel"], row["Titel"], row["Tekst"] = x.get("rule_id"), x.get("title"), r.text
        row["Drempel"], row["Eenheid"], row["Doelgroep"] = r.value, x.get("unit"), x.get("target_group")
    elif kind == "researcher_note":
        row["Nr"], row["Soort"], row["Thema"] = x.get("nr"), x.get("note_type_label"), x.get("themes")
        row["Buurt / plek"], row["Notitie"], row["Bron"] = x.get("place"), r.text, x.get("source")
        row["Datum"], row["Aantal keer genoemd"] = x.get("date"), x.get("count")
    else:
        row["Thema"], row["Onderdeel"], row["Buurt"] = r.theme, r.subtheme, r.neighbourhood
        row["Tekst"], row["Waarde"] = r.text, r.value
    return OrderedDict((_fmt(k), _fmt(v)) for k, v in row.items() if _fmt(v) != "")


def records_behind(pack: EvidencePack, store: SourceStore) -> list[SourceRecord]:
    """The source records behind the pack's items, each once, in the order of the store (sheet order)."""
    wanted = {i for e in pack.items for i in e.source_record_ids}
    missing = sorted(i for i in wanted if store.get(i) is None)
    if missing:
        raise ValueError(f"{len(missing)} source records behind '{pack.section}' are not in the store")
    return [r for r in store.all() if r.id in wanted]


def markdown_tables(records: list[SourceRecord], include_free_text: bool = True) -> str:
    """One pipe table per source sheet: file and sheet as heading, record id as first column (R2)."""
    sheets: "OrderedDict[tuple, list[SourceRecord]]" = OrderedDict()
    for r in records:
        sheets.setdefault((r.source_file, r.sheet), []).append(r)
    blocks = []
    for (file, sheet), rs in sheets.items():
        rows = [(r.id, record_row(r, include_free_text)) for r in rs]
        cols = list(OrderedDict.fromkeys(c for _, row in rows for c in row))
        head = _fmt(file) + (f" – {_fmt(sheet)}" if sheet else "")
        lines = [f"### {head}", "| " + " | ".join([ID_COLUMN, *cols]) + " |",
                 "|" + "---|" * (len(cols) + 1)]
        lines += ["| " + " | ".join([rid, *(row.get(c, "") for c in cols)]) + " |" for rid, row in rows]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "(geen)"


def raw_pack(pack: EvidencePack, store: SourceStore, include_free_text: bool = True) -> EvidencePack:
    """The pack E1R works with: one item per source record, its record id as evidence id."""
    free = {i for e in pack.items if e.contains_free_text for i in e.source_record_ids}
    items = []
    for r in records_behind(pack, store):
        row = record_row(r, include_free_text)
        has_open = r.extra.get("record_kind") == "survey_answer" and bool(r.extra.get("open_text")) and include_free_text
        group = str(r.extra.get("group") or "")
        items.append(Evidence(
            evidence_id=r.id, case_id=pack.case_id, themes=[], perspective=r.perspective, kind="raw_record",
            statement="; ".join(f"{k}: {v}" for k, v in row.items()),
            data={"is_stakeholder": bool(r.extra.get("is_stakeholder")) or "stakeholder" in group.lower()},
            source_record_ids=[r.id], contains_free_text=r.id in free or has_open, method="raw source record"))
    return EvidencePack(pack.case_id, pack.section, items, list(pack.unavailable), dict(pack.built_with))
