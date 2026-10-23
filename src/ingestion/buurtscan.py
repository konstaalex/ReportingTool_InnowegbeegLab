"""UC1 for the Buurtscan data set: one folder -> cases -> SourceRecords.

    result = ingest_buurtscan_folder("…/newDataSender")
    result.store                # every record, citable by id
    result.documents            # one SourceDocument per file (incl. skipped duplicates)
    result.validation           # parser-vs-spreadsheet-footer mismatches (should be empty)
    result.cases                # case_id -> which perspectives are present

Never silent: duplicates, unknown files, unmapped survey questions and chart
images are all listed in the result and printed by the CLI (FR2, FR3).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import openpyxl

from .config import load_config
from .models import SourceRecord
from .parsers import buurtscan_expert_parser, buurtscan_survey_parser, demografie_parser, reference_parser
from .sources import SourceDocument, classify_filename, sha256_file
from .store import SourceStore

RIVM_PDF_HINT = "vuistregels bewegen"
REFERENCE_DOC_HINTS = ("ten textual rules", "schrijfwijzer")


@dataclass
class IngestionResult:
    root: str
    store: SourceStore
    documents: list[SourceDocument] = field(default_factory=list)
    validation: dict[str, list[str]] = field(default_factory=dict)  # file -> problems
    survey_meta: dict[str, dict] = field(default_factory=dict)      # case_id -> survey meta
    cases: dict[str, dict] = field(default_factory=dict)

    def summary(self) -> dict:
        by_status: dict[str, int] = {}
        for d in self.documents:
            by_status[d.status] = by_status.get(d.status, 0) + 1
        return {
            "files": len(self.documents),
            "files_by_status": by_status,
            "duplicates_skipped": [f"{d.filename} = {d.duplicate_of}" for d in self.documents if d.status == "duplicate"],
            "unparsed": [f"{d.filename}: {d.note}" for d in self.documents if d.status == "unparsed"],
            "cases": self.cases,
            "records": self.store.summary(),
            "validation_problems": {k: v for k, v in self.validation.items() if v},
            "unmapped_survey_questions": sorted({
                r.extra.get("question_key", "")[:90] for r in self.store.all()
                if r.extra.get("record_kind") == "survey_answer" and r.needs_manual_mapping
            }),
        }

    def save(self, out_dir: str | Path) -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        self.store.save(out / "store.json")
        (out / "sources.json").write_text(json.dumps([d.to_dict() for d in self.documents], indent=2, ensure_ascii=False), encoding="utf-8")
        (out / "survey_meta.json").write_text(json.dumps(self.survey_meta, indent=2, ensure_ascii=False), encoding="utf-8")
        (out / "ingestion_report.json").write_text(json.dumps(self.summary(), indent=2, ensure_ascii=False), encoding="utf-8")
        return out


# --------------------------------------------------------------------------- mapping

def _match_question(question_key: str, item: Optional[str], mapping: dict) -> Optional[dict]:
    qk = question_key.lower()
    best = None
    for q in mapping["question"]:
        if q["match"].lower() not in qk:
            continue
        if "item" in q:
            if item and q["item"].lower() == item.lower():
                return q
            continue
        best = best or q
    return best


def option_theme(option: str, mapping: dict) -> Optional[str]:
    for prefix, theme in mapping.get("option_themes", {}).items():
        if str(option).lower().startswith(prefix.lower()):
            return theme or None
    return None


def apply_survey_mapping(records: list[SourceRecord], mapping: dict) -> None:
    """Sets theme / question_id on survey records in place. Unmatched
    questions keep needs_manual_mapping=True (FR3)."""
    for r in records:
        if r.perspective != "bewoners" or r.extra.get("record_kind") not in ("survey_answer", "survey_column_total",
                                                                             "survey_tally"):
            continue
        q = _match_question(r.extra.get("question_key", ""), r.subtheme, mapping)
        if q is None:
            r.needs_manual_mapping = True
            continue
        r.extra["question_id"] = q["id"]
        r.extra["question_label"] = q["label"]
        r.needs_manual_mapping = False
        if q["theme"] == "*per_option":
            r.theme = None
            if r.extra.get("record_kind") == "survey_answer":
                r.extra["option_themes"] = {str(o): option_theme(str(o), mapping) for o in r.extra.get("selected", [])}
            elif r.extra.get("record_kind") == "survey_tally":
                r.extra["option_themes"] = {o: option_theme(o, mapping) for o in r.extra.get("counts", {})}
        else:
            r.theme = q["theme"] or None


def apply_demografie_mapping(records: list[SourceRecord], mapping: dict) -> None:
    table = mapping.get("indicator", {})
    for r in records:
        if r.extra.get("record_kind") != "demografie_indicator":
            continue
        cfg = table.get(r.subtheme or "")
        if cfg is None:
            r.needs_manual_mapping = True
            continue
        r.extra["themes"] = list(cfg.get("themes", []))
        r.extra["rules"] = list(cfg.get("rules", []))
        r.theme = r.extra["themes"][0] if len(r.extra["themes"]) == 1 else None


# --------------------------------------------------------------------------- main

def ingest_buurtscan_folder(root: str | Path, cases: Optional[list[str]] = None, verbose: bool = False) -> IngestionResult:
    """Ingest every recognised file under `root`. `cases` optionally limits
    to some case_ids (e.g. ['passewaaij-tiel'] for a fast slice)."""
    root = Path(root)
    store = SourceStore()
    result = IngestionResult(root=str(root), store=store)
    survey_mapping = load_config("survey_mapping.toml")
    demo_mapping = load_config("demografie_mapping.toml")
    seen: dict[str, str] = {}  # checksum -> first filename
    rivm_checksum: Optional[str] = None

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith(("~$", "."))):
        rel = str(path.relative_to(root))
        checksum = sha256_file(path)
        perspective, source_type, case_id, case_label, synthetic = classify_filename(path.name)
        doc = SourceDocument(source_id=checksum[:12], filename=rel, source_type=source_type or "unknown",
                             perspective=perspective, checksum=checksum, case_id=case_id,
                             case_label=case_label, synthetic=synthetic)
        result.documents.append(doc)

        if checksum in seen:
            doc.status, doc.duplicate_of = "duplicate", seen[checksum]
            log(f"[{rel}] duplicate of {seen[checksum]} -> skipped")
            continue
        seen[checksum] = rel

        lower = path.name.lower()
        if RIVM_PDF_HINT in lower and path.suffix.lower() == ".pdf":
            rivm_checksum = checksum
            doc.source_type, doc.perspective, doc.status = "reference_norm", "reference", "parsed"
            doc.note = "rules loaded from config/reference/rivm_vuistregels.toml (transcribed)"
            continue
        if any(h in lower for h in REFERENCE_DOC_HINTS):
            doc.source_type, doc.perspective, doc.status = "reference_style", "reference", "parsed"
            doc.note = "rules loaded from config/reference/writing_rules.toml (transcribed)"
            continue
        if source_type is None:
            doc.status, doc.note = "unparsed", "filename matches no known Buurtscan file kind"
            continue
        if cases and case_id not in cases:
            doc.status, doc.note = "skipped", "case not selected"
            continue

        records: list[SourceRecord] = []
        try:
            if source_type == "buurtscan_expert":
                wb = openpyxl.load_workbook(str(path), read_only=True)
                if not buurtscan_expert_parser.detect(wb.sheetnames):
                    doc.status, doc.note = "unparsed", f"sheet shape not recognised: {wb.sheetnames}"
                    continue
                records = buurtscan_expert_parser.parse_file(str(path), rel, case_id)
            elif source_type == "buurtscan_survey":
                if not buurtscan_survey_parser.detect([], str(path)):
                    doc.status, doc.note = "unparsed", "row 1 is not 'Respondent | Vraag'"
                    continue
                neighbourhood = _neighbourhood_for(case_label, result, case_id)
                records, meta = buurtscan_survey_parser.parse_file(str(path), rel, case_id, neighbourhood)
                apply_survey_mapping(records, survey_mapping)
                result.validation[rel] = buurtscan_survey_parser.validate_against_footer(records, meta)
                meta["source_file"] = rel
                result.survey_meta[case_id] = meta
            elif source_type == "buurtscan_demografie":
                records = demografie_parser.parse_file(str(path), rel, case_id)
                apply_demografie_mapping(records, demo_mapping)
        except Exception as exc:  # noqa: BLE001 - report per file, never crash the batch
            doc.status, doc.note = "unparsed", f"{type(exc).__name__}: {exc}"
            continue

        store.add_many(records)
        doc.status, doc.records = "parsed", len(records)
        case = result.cases.setdefault(case_id, {"label": case_label, "perspectives": {}, "synthetic": synthetic})
        case["perspectives"][perspective] = rel
        log(f"[{rel}] {perspective} / {source_type} / case={case_id} -> {len(records)} records")

    store.add_many(reference_parser.load_rivm_rules(rivm_checksum))
    for case in result.cases.values():
        case["complete"] = set(case["perspectives"]) == {"expert", "bewoners", "data"}
    return result


def _neighbourhood_for(case_label: Optional[str], result: IngestionResult, case_id: Optional[str]) -> Optional[str]:
    """The survey replaces the neighbourhood name in its questions; use the
    expert file's 'Buurt' cell when already ingested, else the case label
    minus its last word (the city)."""
    for r in result.store.all():
        if r.case_id == case_id and r.perspective == "expert" and r.neighbourhood:
            return r.neighbourhood
    if case_label and " " in case_label:
        return case_label.rsplit(" ", 1)[0]
    return case_label
