"""What a rendered report contains, independent of where it came from.

Two sources:
- review sessions  -> the researcher's reviewed report (final, or concept with markers)
- a model run      -> the unreviewed draft of one model (always 'concept'), used to
                      compare models and to evaluate against the original report

Both become a ReportContent: per section an ordered list of Items (claim text,
type, evidence ids, paragraph, review status, verifier status).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from evidence.models import Evidence
from ingestion.store import SourceStore


@dataclass
class Item:
    claim_id: str
    text: str
    claim_type: str
    evidence_ids: list[str]
    paragraph: int = 1
    status: str = "draft"                 # draft | approved | edited | rejected
    verification: Optional[str] = None    # grounded | weak | unsupported


@dataclass
class ReportContent:
    case_id: str
    case_label: str
    case_info: dict
    sections: dict[str, list[Item]]
    evidence: dict[str, Evidence]
    store: SourceStore
    documents: list[dict]
    images_dir: Path
    status: str = "concept"               # concept | final
    provenance: dict = field(default_factory=dict)
    show_ids: bool = True

    @property
    def report_kind(self) -> str:
        return "Wijkscan" if self.case_info.get("layout") == "wijkscan" else "Buurtscan"

    def items(self, section: str) -> list[Item]:
        return [i for i in self.sections.get(section, []) if i.status != "rejected"]


def from_sessions(ws, case_id: str, preview: bool = False) -> ReportContent:
    """The reviewed report. Without preview only approved/edited claims are kept."""
    sections: dict[str, list[Item]] = {}
    for sec, session in ws.sessions(case_id).items():
        out = []
        for st in session.states().values():
            if st.status == "rejected" or (not preview and st.status not in ("approved", "edited")):
                continue
            out.append(Item(st.claim_id, st.text, st.claim_type, list(st.evidence_ids), st.paragraph, st.status,
                            (st.verification or {}).get("status")))
        sections[sec] = out
    pending = any(i.status == "draft" for items in sections.values() for i in items)
    return ReportContent(case_id, ws.case_label(case_id), ws.cases().get(case_id, {}), sections,
                         ws.evidence_index(case_id), ws.store(), ws.documents(), ws.images_dir(case_id),
                         status="concept" if (preview and pending) else "final",
                         provenance={"source": "beoordeelde concepten (review sessions)"})


def from_drafts(ws, case_id: str, drafts: dict, provenance: Optional[dict] = None) -> ReportContent:
    """A model run's unreviewed drafts (SectionDraft per section)."""
    sections = {sec: [Item(c.claim_id, c.text, c.claim_type, list(c.evidence_ids), c.paragraph, "draft",
                           (c.verification or {}).get("status")) for c in d.claims]
                for sec, d in drafts.items()}
    return ReportContent(case_id, ws.case_label(case_id), ws.cases().get(case_id, {}), sections,
                         ws.evidence_index(case_id), ws.store(), ws.documents(), ws.images_dir(case_id),
                         status="concept", provenance=provenance or {})
