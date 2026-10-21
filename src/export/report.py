"""UC6: assemble reviewed sections into the Buurtscan report + evidence map.

Export is blocked until every claim in every included section is approved,
edited or rejected (FR11, NFR2); the error names the sections holding it up.
`preview=True` renders anyway, with a CONCEPT banner and every unresolved
claim visibly marked - for internal reading only.

Sections the AI does not write:
- Methode: generated deterministically from the ingested sources (counts,
  files), labelled as such.
- Aanbevelingen: left to the researcher on purpose (brief section 5: no
  autonomous recommendations); approved 'aanbeveling' claims are included
  if a researcher added them.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from evidence.models import Evidence
from ingestion.config import load_config
from ingestion.models import THEMES
from ingestion.store import SourceStore
from review.session import RESOLVED, ClaimState, ReviewSession


class ExportBlocked(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__("Export geblokkeerd: " + "; ".join(reasons))
        self.reasons = reasons


@dataclass
class ReportInputs:
    case_id: str
    case_label: str
    sessions: dict[str, ReviewSession]          # section -> session
    evidence: dict[str, Evidence]               # evidence index for the case
    store: SourceStore
    documents: list[dict]                       # SourceDocument dicts (sources.json)


def blocking_reasons(inp: ReportInputs, required_sections: Optional[list[str]] = None) -> list[str]:
    reasons = []
    required = required_sections if required_sections is not None else list(THEMES)
    for sec in required:
        if sec not in inp.sessions:
            reasons.append(f"sectie '{sec}' heeft nog geen concept")
    for sec, s in inp.sessions.items():
        pend = s.pending()
        if pend:
            reasons.append(f"sectie '{sec}': {len(pend)} bewering(en) nog niet beoordeeld ({', '.join(pend[:5])}{'…' if len(pend) > 5 else ''})")
        chain = s.verify_chain()
        if chain:
            reasons.append(f"sectie '{sec}': audit-log is gewijzigd ({chain[0]})")
    return reasons


def _cite(ids: list[str]) -> str:
    return f" [{', '.join(ids)}]" if ids else ""


def _claims_md(states: list[ClaimState], preview: bool, with_ids: bool) -> str:
    paras: dict[int, list[str]] = {}
    for st in states:
        if st.status == "rejected":
            continue
        if st.status not in RESOLVED and not preview:
            continue
        text = st.text.strip()
        if with_ids:
            text += _cite(st.evidence_ids)
        if st.status not in RESOLVED:
            text = f"**[NIET BEOORDEELD]** {text}"
        paras.setdefault(st.paragraph, []).append(text)
    return "\n\n".join(" ".join(p) for _, p in sorted(paras.items()))


def methode_text(inp: ReportInputs) -> str:
    ev = inp.evidence
    resp = next((e for e in ev.values() if e.kind == "respondents"), None)
    n_stat = sum(e.data.get("n_statements", 0) for e in ev.values() if e.kind == "theme_average")
    lines = [
        "_Automatisch samengesteld uit de ingelezen bronbestanden._",
        "",
        "Deze Buurtscan combineert drie perspectieven per thema:",
        f"- **Expertperspectief**: een expertwandeling waarin {n_stat} stellingen over zes thema's zijn gescoord op een schaal van 1 (heel slecht) tot 5 (uitstekend), met opmerkingen.",
    ]
    if resp:
        lines.append(f"- **Bewonersperspectief**: een vragenlijst onder bewoners ({resp.data['n_respondents']} respondenten).")
    lines += [
        "- **Dataperspectief**: demografische kerncijfers en de index beweegvriendelijke omgeving voor buurt, wijk/gemeente en Nederland.",
        "",
        "De RIVM-vuistregels Bewegen (2025) dienen als kader. Deze Buurtscan bevat geen afstandsmetingen; de vuistregels zijn daarom niet getoetst.",
    ]
    return "\n".join(lines)


def render_markdown(inp: ReportInputs, preview: bool = False, with_ids: bool = True,
                    required_sections: Optional[list[str]] = None) -> str:
    reasons = blocking_reasons(inp, required_sections)
    if reasons and not preview:
        raise ExportBlocked(reasons)
    out = [f"# Buurtscan {inp.case_label}", ""]
    if preview and reasons:
        out += ["> **CONCEPT — NIET VOOR VERSPREIDING.** Nog niet volledig beoordeeld:", ""]
        out += [f"> - {r}" for r in reasons] + [""]
    states = {sec: list(s.states().values()) for sec, s in inp.sessions.items()}

    out += ["## Samenvatting", ""]
    concl = [(sec, st) for sec in THEMES for st in states.get(sec, []) if st.claim_type == "conclusie"
             and (st.status in ("approved", "edited") or preview) and st.status != "rejected"]
    out += [f"- **{sec}**: {st.text}{_cite(st.evidence_ids) if with_ids else ''}" for sec, st in concl] or ["_(nog geen goedgekeurde conclusies)_"]
    out += ["", "## Methode", "", methode_text(inp), "", "## Resultaten", ""]
    if "Algemeen" in states:
        out += ["### Demografie en respondenten", "", _claims_md(states["Algemeen"], preview, with_ids), ""]
    for i, theme in enumerate(THEMES, start=1):
        if theme not in states:
            continue
        body = _claims_md([s for s in states[theme] if s.claim_type != "aanbeveling"], preview, with_ids)
        out += [f"### Thema {i}: {theme}", "", body or "_(geen goedgekeurde beweringen)_", ""]
    out += ["## Conclusies", ""]
    out += [f"**{sec}.** {st.text}" for sec, st in concl] or ["_(nog geen goedgekeurde conclusies)_"]
    out += ["", "## Aanbevelingen", ""]
    recs = [st for sec in states for st in states[sec] if st.claim_type == "aanbeveling" and st.status in ("approved", "edited")]
    out += [f"- {st.text}" for st in recs] or ["_Aanbevelingen worden door de onderzoeker opgesteld op basis van de goedgekeurde bevindingen en conclusies._"]
    out += ["", "## Bronnenlijst", ""]
    rivm = load_config("reference/rivm_vuistregels.toml")
    out.append(f"- {rivm['citation']}")
    for d in inp.documents:
        if d.get("status") == "parsed" and (d.get("case_id") in (inp.case_id, None)):
            out.append(f"- {d['filename']} (sha256 {d['checksum'][:12]}…)")
    out += ["", "## Bijlage: bewijsoverzicht", "",
            f"Elke bewering verwijst naar bewijsstukken; zie `evidence_map.csv` voor de exacte cellen ({len(evidence_map_rows(inp))} koppelingen).", ""]
    return "\n".join(out).rstrip() + "\n"


def evidence_map_rows(inp: ReportInputs) -> list[dict]:
    rows = []
    for sec, s in inp.sessions.items():
        for st in s.states().values():
            for eid in st.evidence_ids or [""]:
                e = inp.evidence.get(eid)
                recs = [inp.store.get(rid) for rid in (e.source_record_ids if e else [])]
                recs = [r for r in recs if r is not None]
                rows.append({
                    "section": sec, "claim_id": st.claim_id, "claim_type": st.claim_type, "status": st.status,
                    "override": st.override, "verification": (st.verification or {}).get("status"),
                    "claim_text": st.text, "evidence_id": eid or "(geen)",
                    "evidence_statement": e.statement if e else "", "perspective": e.perspective if e else "",
                    "source_files": "; ".join(sorted({r.source_file for r in recs})),
                    "sheets": "; ".join(sorted({r.sheet for r in recs if r.sheet})),
                    "locators": "; ".join(r.locator for r in recs[:12]) + (f"; … (+{len(recs) - 12})" if len(recs) > 12 else ""),
                    "n_source_records": len(recs),
                })
    return rows


def evidence_map_csv(inp: ReportInputs) -> str:
    rows = evidence_map_rows(inp)
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return buf.getvalue()


def write_docx(markdown: str, path: str | Path) -> Path:
    """Minimal Markdown -> DOCX (headings, bullets, paragraphs, bold banner)."""
    import re
    import docx  # python-docx
    doc = docx.Document()
    for line in markdown.splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            doc.add_heading(line.lstrip("#").strip(), level=min(level, 4))
        elif line.startswith(("- ", "> - ")):
            doc.add_paragraph(line.split("- ", 1)[1].replace("**", ""), style="List Bullet")
        else:
            p = doc.add_paragraph()
            for part in re.split(r"(\*\*[^*]+\*\*)", line.lstrip("> ")):
                if part.startswith("**") and part.endswith("**"):
                    p.add_run(part[2:-2]).bold = True
                else:
                    p.add_run(part.replace("_", ""))
    path = Path(path)
    doc.save(path)
    return path


def export_case(inp: ReportInputs, out_dir: str | Path, preview: bool = False,
                required_sections: Optional[list[str]] = None) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md = render_markdown(inp, preview=preview, required_sections=required_sections)
    suffix = "_CONCEPT" if preview else ""
    files = {"markdown": out / f"buurtscan_{inp.case_id}{suffix}.md", "evidence_map": out / f"evidence_map_{inp.case_id}{suffix}.csv"}
    files["markdown"].write_text(md, encoding="utf-8")
    files["evidence_map"].write_text(evidence_map_csv(inp), encoding="utf-8")
    try:
        files["docx"] = write_docx(md, out / f"buurtscan_{inp.case_id}{suffix}.docx")
    except ImportError:
        pass
    return files
