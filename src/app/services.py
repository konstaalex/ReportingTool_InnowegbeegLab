"""Application services: the only API the Streamlit UI and CLIs call (brief section 7).

A Workspace is a folder holding everything one researcher works on:

    <workspace>/ingest/        store.json, sources.json, survey_meta.json, ingestion_report.json
    <workspace>/evidence/      <case>.json            (evidence packs)
    <workspace>/drafts/        <case>/<section>/<draft_id>.json
    <workspace>/reviews/       <case>/<section>.json  (active review session)
    <workspace>/runs/          <run_id>.json          (experiment run records)
    <workspace>/exports/       <case>/...

Raw source files are never copied or modified; only derived data lives here.
Keep workspaces with real data out of git (NFR7) - see .gitignore.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from drafting.models import SectionDraft
from drafting.pipelines import PipelineConfig, run_pipeline
from evidence.builder import build_case_evidence, evidence_index
from evidence.models import AANLEIDING, CONCLUSIES, GENERAL, Evidence, EvidencePack
from export.report import ReportInputs, blocking_reasons, export_case
from ingestion.buurtscan import ingest_buurtscan_folder
from ingestion.models import SourceRecord, THEMES
from ingestion.store import SourceStore
from llm.registry import get_provider, list_models, validate_model_config
from review.session import ReviewSession
from verification.checks import verify_section

SECTIONS = list(THEMES) + [GENERAL, AANLEIDING, CONCLUSIES]


@dataclass
class Workspace:
    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._store: Optional[SourceStore] = None
        self._packs: dict[str, dict[str, EvidencePack]] = {}

    # ------------------------------------------------------------ paths
    def p(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def _section_slug(self, section: str) -> str:
        return section.lower().replace(" ", "-")

    # ------------------------------------------------------------ UC1
    def ingest(self, folder: str, cases: Optional[list[str]] = None, case_id: Optional[str] = None,
               case_label: Optional[str] = None, append: bool = True) -> dict:
        """Ingest a Buurtscan folder (flat, one case per neighbourhood) or a
        Wijkscan folder (perspective subfolders, one case). With append=True,
        cases already in the workspace are kept and re-ingested cases replaced."""
        from ingestion.wijkscan import ingest_wijkscan_folder, is_wijkscan_folder
        if is_wijkscan_folder(folder):
            from ingestion.sources import slugify
            case_id = case_id or slugify(Path(folder).name)
            result = ingest_wijkscan_folder(folder, case_id, case_label or Path(folder).name)
        else:
            result = ingest_buurtscan_folder(folder, cases=cases)
        if append and self.has_data():
            result = self._merge(result)
        result.save(self.p("ingest"))
        self._store, self._packs = result.store, {}
        return result.summary()

    def _merge(self, new):
        """Keep earlier cases; replace records/documents of the cases in `new`."""
        from ingestion.buurtscan import IngestionResult
        from ingestion.sources import SourceDocument
        old_store = self.store()
        new_cases = set(new.cases)
        new_files = {d.filename for d in new.documents}
        # researcher notes added separately (ingest_notes) survive a re-ingest of their case
        kept_notes = [r for r in old_store.all() if r.case_id in new_cases
                      and r.extra.get("record_kind") == "researcher_note" and r.source_file not in new_files]
        # map measurements and confirmed readings survive a re-ingest when their image did not change
        old_sums = {d.get("filename"): d.get("checksum") for d in self.documents()}
        new_sums = {d.filename: d.checksum for d in new.documents}
        kept_images = [r for r in old_store.all() if r.case_id in new_cases
                       and r.extra.get("record_kind") in ("image_measurement", "image_reading", "image_observation")
                       and new_sums.get(r.source_file) is not None
                       and new_sums.get(r.source_file) == old_sums.get(r.source_file)]
        merged = SourceStore()
        merged.add_many(r for r in old_store.all() if r.case_id not in new_cases and r.perspective != "reference")
        merged.add_many(new.store.all())
        merged.add_many(kept_notes)
        merged.add_many(kept_images)
        note_files = {r.source_file for r in kept_notes}
        docs = [SourceDocument(**d) for d in self.documents() if d.get("case_id") not in new_cases
                or (d.get("source_type") == "researcher_notes" and d.get("filename") in note_files)]
        known = {(d.filename, d.checksum) for d in docs}
        docs += [d for d in new.documents if (d.filename, d.checksum) not in known]
        report = self.ingestion_report()
        cases = {k: v for k, v in report.get("cases", {}).items() if k not in new_cases}
        cases.update(new.cases)
        meta_path = self.p("ingest", "survey_meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        meta.update(new.survey_meta)
        return IngestionResult(root=new.root, store=merged, documents=docs, validation=new.validation,
                               survey_meta=meta, cases=cases)

    # ------------------------------------------------------------ researcher notes
    def ingest_notes(self, path: str | Path, case_id: str) -> dict:
        """Read a researcher-notes workbook (sheet 'Notities') into an existing
        case. Notes read earlier from a file with the same name are replaced."""
        from ingestion.parsers import notes_parser
        from ingestion.sources import SourceDocument, sha256_file
        path = Path(path)
        if case_id not in self.cases():
            raise ValueError(f"unknown case {case_id!r}; ingest the case first")
        if not notes_parser.is_notes_workbook(path):
            raise ValueError(f"{path.name}: no sheet '{notes_parser.SHEET}' with columns Soort and Notitie "
                             "(make one with: python -m ingestion.cli notes-template <file>)")
        records, problems = notes_parser.parse_notes(path, path.name, case_id)
        store = self.store()
        store.remove_where(lambda r: r.case_id == case_id and r.extra.get("record_kind") == "researcher_note"
                           and r.source_file == path.name)
        store.add_many(records)
        checksum = sha256_file(path)
        docs = [d for d in self.documents() if not (d.get("case_id") == case_id and d.get("filename") == path.name
                                                     and d.get("source_type") == "researcher_notes")]
        docs.append(SourceDocument(source_id=checksum[:12], filename=path.name, source_type="researcher_notes",
                                   perspective=None, checksum=checksum, case_id=case_id,
                                   case_label=self.case_label(case_id), status="parsed", records=len(records),
                                   note="; ".join(problems) or None).to_dict())
        self.p("ingest", "sources.json").write_text(json.dumps(docs, indent=2, ensure_ascii=False), encoding="utf-8")
        report = self.ingestion_report()
        report.setdefault("validation_problems", {})
        report["validation_problems"].pop(path.name, None)
        if problems:
            report["validation_problems"][path.name] = problems
        report["records"] = store.summary()
        self.p("ingest", "ingestion_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                                             encoding="utf-8")
        self.save_store()
        return {"records": len(records), "problems": problems,
                "by_type": {t: sum(1 for r in records if r.extra["note_type"] == t)
                            for t in sorted({r.extra["note_type"] for r in records})}}

    def notes(self, case_id: str) -> list[SourceRecord]:
        return [r for r in self.store().all() if r.case_id == case_id and r.extra.get("record_kind") == "researcher_note"]

    @staticmethod
    def write_notes_template(path: str | Path) -> Path:
        from ingestion.parsers.notes_parser import write_template
        return write_template(path)

    def save_store(self) -> None:
        self.store().save(self.p("ingest", "store.json"))
        self._packs = {}

    # ------------------------------------------------------------ images
    def images_dir(self, case_id: str) -> Path:
        return self.p("images", case_id)

    def analyse_images(self, case_id: str, vision_model_id: Optional[str] = None, samples: int = 3) -> dict:
        from imaging.pipeline import analyse_case_images
        provider = None
        if vision_model_id:
            problems = validate_model_config(vision_model_id)
            if problems:
                raise ValueError("; ".join(problems))
            provider = get_provider(vision_model_id)
            if not provider.model_info.capabilities.vision:
                raise ValueError(f"{vision_model_id} is not a vision model (capabilities.vision = false)")
        summary = analyse_case_images(self.store(), case_id, self.images_dir(case_id), provider, samples)
        self.save_store()
        return summary

    def confirmations(self, case_id: str):
        from imaging.confirm import ConfirmationLog
        return ConfirmationLog(self.images_dir(case_id) / "confirmations.json")

    def image_records(self, case_id: str, kinds: tuple[str, ...] = ("image_measurement", "image_reading",
                                                                    "image_observation")) -> list[SourceRecord]:
        return [r for r in self.store().all() if r.case_id == case_id and r.extra.get("record_kind") in kinds]

    def has_data(self) -> bool:
        return self.p("ingest", "store.json").exists()

    def store(self) -> SourceStore:
        if self._store is None:
            self._store = SourceStore.load(self.p("ingest", "store.json"))
        return self._store

    def ingestion_report(self) -> dict:
        return json.loads(self.p("ingest", "ingestion_report.json").read_text(encoding="utf-8"))

    def documents(self) -> list[dict]:
        return json.loads(self.p("ingest", "sources.json").read_text(encoding="utf-8"))

    def cases(self) -> dict[str, dict]:
        return self.ingestion_report().get("cases", {})

    def case_label(self, case_id: str) -> str:
        return self.cases().get(case_id, {}).get("label", case_id)

    # ------------------------------------------------------------ evidence
    def evidence(self, case_id: str, rebuild: bool = False) -> dict[str, EvidencePack]:
        if case_id in self._packs and not rebuild:
            return self._packs[case_id]
        path = self.p("evidence", f"{case_id}.json")
        inputs = [self.p("ingest", "store.json"), self.images_dir(case_id) / "confirmations.json"]
        stale = path.exists() and any(i.exists() and i.stat().st_mtime > path.stat().st_mtime for i in inputs)
        if path.exists() and not rebuild and not stale:
            data = json.loads(path.read_text(encoding="utf-8"))
            packs = {k: EvidencePack.from_dict(v) for k, v in data.items()}
        else:
            packs = build_case_evidence(self.store(), case_id, confirmations=self.confirmations(case_id))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({k: v.to_dict() for k, v in packs.items()}, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        self._packs[case_id] = packs
        return packs

    def evidence_index(self, case_id: str) -> dict[str, Evidence]:
        return evidence_index(self.evidence(case_id))

    def source_records(self, evidence: Evidence) -> list[SourceRecord]:
        st = self.store()
        return [r for r in (st.get(i) for i in evidence.source_record_ids) if r is not None]

    # ------------------------------------------------------------ UC5
    def models(self) -> dict[str, dict]:
        return list_models()

    def model_problems(self, model_id: str) -> list[str]:
        return validate_model_config(model_id)

    # ------------------------------------------------------------ UC2
    def generate(self, case_id: str, section: str, cfg: PipelineConfig, model_id: Optional[str],
                 reviewer: str, replace_review: bool = True) -> SectionDraft:
        packs = self.evidence(case_id)
        pack = packs[section]
        cfg.case_label = cfg.case_label or self.case_label(case_id)
        provider = None
        if cfg.pipeline_id != "template":
            problems = validate_model_config(model_id or "")
            if problems:
                raise ValueError("; ".join(problems))
            provider = get_provider(model_id)
        draft = run_pipeline(pack, cfg, provider)
        verify_section(draft, pack, evidence_index(packs))
        self.save_draft(draft)
        current = self.p("reviews", case_id, f"{self._section_slug(section)}.json")
        if replace_review or not current.exists():
            if current.exists():
                # never lose a review trail (NFR11): archive the previous session
                archive = self.p("reviews", case_id, "archive",
                                 f"{self._section_slug(section)}-{draft.created_at.replace(':', '')}.json")
                archive.parent.mkdir(parents=True, exist_ok=True)
                current.rename(archive)
            session = ReviewSession.start(draft, reviewer)
            self.save_session(session)
        return draft

    def archived_sessions(self, case_id: str) -> list[Path]:
        return sorted(self.p("reviews", case_id, "archive").glob("*.json"))

    def save_draft(self, draft: SectionDraft) -> Path:
        path = self.p("drafts", draft.case_id, self._section_slug(draft.section), f"{draft.draft_id}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(draft.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    # ------------------------------------------------------------ UC3/UC4
    def session(self, case_id: str, section: str) -> Optional[ReviewSession]:
        path = self.p("reviews", case_id, f"{self._section_slug(section)}.json")
        if not path.exists():
            return None
        return ReviewSession.load(path).attach_index(self.evidence_index(case_id))

    def save_session(self, session: ReviewSession) -> None:
        d = session.draft
        session.save(self.p("reviews", d.case_id, f"{self._section_slug(d.section)}.json"))

    def sessions(self, case_id: str) -> dict[str, ReviewSession]:
        out = {}
        for sec in SECTIONS:
            s = self.session(case_id, sec)
            if s is not None:
                out[sec] = s
        return out

    # ------------------------------------------------------------ UC6
    def report_inputs(self, case_id: str) -> ReportInputs:
        return ReportInputs(case_id=case_id, case_label=self.case_label(case_id), sessions=self.sessions(case_id),
                            evidence=self.evidence_index(case_id), store=self.store(), documents=self.documents(),
                            case_info=self.cases().get(case_id, {}))

    def export_blockers(self, case_id: str, required_sections: Optional[list[str]] = None) -> list[str]:
        return blocking_reasons(self.report_inputs(case_id), required_sections)

    def export(self, case_id: str, preview: bool = False, required_sections: Optional[list[str]] = None,
               pdf: bool = False) -> dict[str, Path]:
        """Markdown + evidence_map.csv + the Word report in the house layout (and PDF if asked and
        LibreOffice is installed). Blocked until reviewed unless preview (FR11)."""
        files = export_case(self.report_inputs(case_id), self.p("exports", case_id), preview=preview,
                           required_sections=required_sections)
        from export.content import from_sessions
        from export.docx_report import render_docx, to_pdf
        content = from_sessions(self, case_id, preview=preview)
        content.provenance["label"] = "beoordeeld rapport" if content.status == "final" else "conceptversie"
        docx_path, _ = render_docx(content, self.p("exports", case_id),
                                   filename=files["markdown"].with_suffix(".docx").name)
        files["docx"] = docx_path
        if pdf:
            pdf_path = to_pdf(docx_path)
            if pdf_path:
                files["pdf"] = pdf_path
        return files

    # ------------------------------------------------------------ full reports per model (SQ3)
    def selected_models(self) -> list[str]:
        from llm.registry import selected_models
        return selected_models("drafting")

    def report_run(self, case_id: str, model_id: str, pipeline: str = "staged", pdf: bool = False,
                   progress=None) -> dict:
        """Draft every section with one model (or 'template') and render the Word report.
        Runs are kept apart from the review sessions (reports/<case>/<run>/)."""
        from reports.runs import generate_report_run
        return generate_report_run(self, case_id, model_id, pipeline=pipeline, pdf=pdf, progress=progress)

    def report_runs(self, case_id: str) -> list[dict]:
        from reports.runs import list_runs
        return list_runs(self, case_id)

    def latest_report_runs(self, case_id: str) -> dict[str, str]:
        from reports.runs import latest_runs
        return latest_runs(self, case_id)

    def report_run_dir(self, case_id: str, run_id: str) -> Path:
        from reports.runs import run_path
        return run_path(self, case_id, run_id)

    def reference_report(self, case_id: str) -> Optional[Path]:
        """The original (human) report of the case, if ingestion found one. Evaluation only."""
        from reports.evaluate import reference_path
        return reference_path(self, case_id)

    def evaluate_report_run(self, case_id: str, run_id: str, reference_pdf: Optional[str] = None,
                            judge_model: Optional[str] = None, progress=None) -> dict:
        from reports.evaluate import evaluate_run
        return evaluate_run(self, case_id, run_id, reference_pdf=reference_pdf, judge_model=judge_model,
                            progress=progress)

    def compare_report_runs(self, case_id: str, run_ids: list[str], reference_pdf: Optional[str] = None,
                            judge_model: Optional[str] = None, progress=None) -> dict:
        from reports.evaluate import compare_runs
        return compare_runs(self, case_id, run_ids, reference_pdf=reference_pdf, judge_model=judge_model,
                            progress=progress)

    def adopt_report_run(self, case_id: str, run_id: str, reviewer: str) -> list[str]:
        """Make a run's drafts the review sessions of the case (earlier sessions are archived)."""
        from reports.runs import adopt_for_review
        return adopt_for_review(self, case_id, run_id, reviewer)
