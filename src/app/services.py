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
from evidence.builder import GENERAL, build_case_evidence, evidence_index
from evidence.models import Evidence, EvidencePack
from export.report import ReportInputs, blocking_reasons, export_case
from ingestion.buurtscan import ingest_buurtscan_folder
from ingestion.models import SourceRecord, THEMES
from ingestion.store import SourceStore
from llm.registry import get_provider, list_models, validate_model_config
from review.session import ReviewSession
from verification.checks import verify_section

SECTIONS = list(THEMES) + [GENERAL]


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
    def ingest(self, folder: str, cases: Optional[list[str]] = None) -> dict:
        result = ingest_buurtscan_folder(folder, cases=cases)
        result.save(self.p("ingest"))
        self._store, self._packs = result.store, {}
        return result.summary()

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
        if path.exists() and not rebuild:
            data = json.loads(path.read_text(encoding="utf-8"))
            packs = {k: EvidencePack.from_dict(v) for k, v in data.items()}
        else:
            packs = build_case_evidence(self.store(), case_id)
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
                            evidence=self.evidence_index(case_id), store=self.store(), documents=self.documents())

    def export_blockers(self, case_id: str, required_sections: Optional[list[str]] = None) -> list[str]:
        return blocking_reasons(self.report_inputs(case_id), required_sections)

    def export(self, case_id: str, preview: bool = False, required_sections: Optional[list[str]] = None) -> dict[str, Path]:
        return export_case(self.report_inputs(case_id), self.p("exports", case_id), preview=preview,
                           required_sections=required_sections)
