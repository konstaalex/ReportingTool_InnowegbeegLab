"""UC1 for the Wijkscan layout (the real Oss / Euterpepark case, Sept 2026 shape):

    <root>/Expert perspectief/Expertwandeling resultaten Oss <buurt>.xlsx   (score sheet + Score per indicator)
    <root>/Expert perspectief/Totaal resultaten Expertwandeling ....xlsx     (Totaal scores + Opmerkingen)
    <root>/Expert perspectief/Sunburst ....png                               (chart drawn from Totaal scores)
    <root>/Bewonersperspectief/Gecategoriseerde Resultaten Oss.xlsx          (coded open answers + survey totals)
    <root>/Data perspectief/*.png|*.jpeg                                     (QGIS maps, demografie infographics)
    <root>/Rapportage Wijkscan ... .pdf                                      (THE HUMAN-WRITTEN REPORT)

The human report is deliberately NOT ingested: it is the reference the
generated draft is evaluated against, so feeding it in would leak the answer
(brief section 18, research validity). It is registered with status
'excluded_reference_report' so the exclusion is visible, not silent.

Images are registered as records (record_kind 'image') with the metadata from
config/images.toml; reading or measuring them is a separate, explicit step
(imaging/), because it is model-assisted or heuristic and must be confirmed.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import openpyxl

from .buurtscan import IngestionResult, apply_survey_mapping
from .config import load_config
from .models import SourceRecord
from .parsers import expert_parser, reference_parser, resident_parser, survey_tally_parser
from .perspective_detector import detect_perspective_from_path
from .sources import SourceDocument, sha256_file
from .store import SourceStore

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
REPORT_HINTS = ("rapportage", "rapport ")
CODED_SHEET = "Gecategoriseerde data"
MERGED_SHEET = "Samengevoegde data"   # not used for evidence: appears to merge rows of CODED_SHEET (open question)


def is_wijkscan_folder(root: str | Path) -> bool:
    names = {p.name.lower() for p in Path(root).iterdir() if p.is_dir()}
    return any("perspectief" in n for n in names)


def _image_meta(filename: str) -> dict:
    cfg = load_config("images.toml")
    name = filename.lower()
    for img in cfg.get("image", []):
        if img["match"].lower() in name:
            return dict(img)
    return {}


def ingest_wijkscan_folder(root: str | Path, case_id: str, case_label: str, verbose: bool = False) -> IngestionResult:
    root = Path(root)
    store = SourceStore()
    result = IngestionResult(root=str(root), store=store)
    mapping = load_config("survey_mapping.toml")
    seen: dict[str, str] = {}
    rivm_checksum: Optional[str] = None
    perspectives: dict[str, str] = {}

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith(("~$", "."))):
        rel = str(path.relative_to(root))
        lower = path.name.lower()
        checksum = sha256_file(path)
        persp = detect_perspective_from_path(rel)
        doc = SourceDocument(source_id=checksum[:12], filename=rel, source_type="unknown", perspective=persp,
                             checksum=checksum, case_id=case_id, case_label=case_label)
        result.documents.append(doc)
        if checksum in seen:
            doc.status, doc.duplicate_of = "duplicate", seen[checksum]
            continue
        seen[checksum] = rel
        suffix = path.suffix.lower()

        if suffix == ".pdf" and any(h in lower for h in REPORT_HINTS):
            doc.source_type, doc.status = "reference_report", "excluded_reference_report"
            doc.note = "human-written report: evaluation reference only, never a drafting input (prevents answer leakage)"
            log(f"[{rel}] EXCLUDED: reference report")
            continue
        if suffix == ".pdf" and "vuistregels" in lower:
            rivm_checksum = checksum
            doc.source_type, doc.perspective, doc.status = "reference_norm", "reference", "parsed"
            continue
        if suffix == ".psd":
            doc.status, doc.note = "skipped", "Photoshop working file of a chart that is also present as PNG"
            continue

        records: list[SourceRecord] = []
        try:
            if suffix == ".xlsx":
                sheets = openpyxl.load_workbook(str(path), read_only=True).sheetnames
                if persp == "expert" and any(s.lower().startswith(("score ", "scores ")) for s in sheets):
                    buurt = re.sub(r"^Expertwandeling resultaten Oss\s+", "", path.stem, flags=re.I).strip()
                    for s in sheets:
                        if s.lower().startswith(("score ", "scores ")) and s != expert_parser.SCORE_PER_INDICATOR_SHEET:
                            records += expert_parser.parse_score_sheet(str(path), s, buurt, rel, case_id)
                            records += expert_parser.parse_section_remarks(str(path), s, buurt, rel, case_id)
                    doc.source_type = "wijkscan_expert_neighbourhood"
                elif persp == "expert" and expert_parser.TOTAAL_SHEET in sheets:
                    totals = expert_parser.parse_totaal_scores(str(path), rel)
                    for r in totals:
                        r.extra["record_kind"] = "expert_indicator_total"
                    remarks = expert_parser.parse_opmerkingen(str(path), rel) if expert_parser.OPMERKINGEN_SHEET in sheets else []
                    for r in remarks:
                        r.extra["record_kind"] = "expert_remark_sheet"
                    records = totals + remarks
                    doc.source_type = "wijkscan_expert_totals"
                elif persp == "bewoners" and resident_parser.detect(sheets):
                    if CODED_SHEET in sheets:
                        coded = resident_parser.parse_categorized_sheet(str(path), CODED_SHEET, rel)
                        for r in coded:
                            r.extra.update({"record_kind": "coded_response", "group": r.neighbourhood,
                                            "response_type": r.subtheme})
                        records += coded
                    if survey_tally_parser.detect(sheets):
                        tallies, meta = survey_tally_parser.parse_file(str(path), rel, case_id, neighbourhood="het Euterpepark")
                        apply_survey_mapping(tallies, mapping)
                        records += tallies
                        result.survey_meta[case_id] = meta
                    doc.source_type = "wijkscan_residents"
                    if MERGED_SHEET in sheets:
                        doc.note = (f"'{MERGED_SHEET}' not used for evidence: it appears to merge rows of "
                                    f"'{CODED_SHEET}' (would double-count); to confirm with InnoBeweegLab")
                else:
                    doc.status, doc.note = "unparsed", f"no known sheet shape among {sheets}"
                    continue
            elif suffix in IMAGE_SUFFIXES:
                meta = _image_meta(path.name)
                locator = "afbeelding (volledig)"
                records = [SourceRecord(
                    id=SourceRecord.make_id(persp or "data", rel, None, locator),
                    perspective=persp or "data", source_file=rel, sheet=None, locator=locator,
                    theme=None, text=meta.get("title", path.stem),
                    extra={"record_kind": "image", "image_kind": meta.get("kind", "unknown"),
                           "image_id": meta.get("id"), "themes": meta.get("themes", []),
                           "rules": meta.get("rules", []), "checksum": checksum,
                           "absolute_path": str(path.resolve())},
                    needs_manual_mapping=not meta, case_id=case_id,
                )]
                doc.source_type = f"image:{meta.get('kind', 'unknown')}"
            else:
                doc.status, doc.note = "unparsed", f"unsupported file type {suffix!r}"
                continue
        except Exception as exc:  # noqa: BLE001 - report per file
            doc.status, doc.note = "unparsed", f"{type(exc).__name__}: {exc}"
            continue

        for r in records:
            r.case_id = case_id
        store.add_many(records)
        doc.status, doc.records = "parsed", len(records)
        if persp:
            perspectives[persp] = rel
        log(f"[{rel}] {persp} / {doc.source_type} -> {len(records)} records")

    store.add_many(reference_parser.load_rivm_rules(rivm_checksum))
    result.cases[case_id] = {"label": case_label, "perspectives": perspectives, "synthetic": False,
                             "complete": {"expert", "bewoners", "data"} <= set(perspectives),
                             "layout": "wijkscan"}
    return result
