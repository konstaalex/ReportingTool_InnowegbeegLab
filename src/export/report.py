"""UC6: assemble reviewed sections into the Wijkscan/Buurtscan report + evidence map.

Export is blocked until every claim in every included section is approved,
edited or rejected (FR11, NFR2); the error names the sections holding it up.
`preview=True` renders anyway, with a CONCEPT banner and every unresolved
claim visibly marked - for internal reading only.

Structure (follows the human Wijkscan report): Samenvatting, Aanleiding,
Methode, Resultaten (demografie + six themes), Conclusies, Aanbevelingen,
Bronnenlijst, Bijlage.

- Samenvatting: assembled from approved conclusions (cross-theme first).
- Aanleiding: only when the case has project-context notes and a reviewed draft.
- Methode: generated deterministically from what was ingested and measured
  for this case, labelled as such.
- Aanbevelingen: left to the researcher on purpose (brief section 5: no
  autonomous recommendations); approved 'aanbeveling' claims are included
  if a researcher added them.
"""
from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from evidence.models import AANLEIDING, CONCLUSIES, GENERAL, Evidence
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
    case_info: dict = field(default_factory=dict)   # ingestion report entry (label, layout, ...)

    @property
    def report_kind(self) -> str:
        """'Wijkscan' for the perspective-folder layout (Oss), else 'Buurtscan'."""
        return "Wijkscan" if self.case_info.get("layout") == "wijkscan" else "Buurtscan"


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


def _join_nl(items: list[str]) -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else (", ".join(items[:-1]) + " en " + items[-1] if items else "")


def _expert_line(ev: list[Evidence]) -> Optional[str]:
    stmts = [e for e in ev if e.kind == "expert_statement"]
    if not stmts:
        return None
    buurten = sorted({e.data.get("neighbourhood") for e in stmts if e.data.get("neighbourhood")})
    per_buurt = Counter(e.data.get("neighbourhood") for e in stmts)
    n_per = max(per_buurt.values())
    indicators = len({(tuple(e.themes), e.data.get("indicator")) for e in stmts if e.data.get("indicator")})
    raters = Counter(len(e.data.get("evaluator_scores") or {}) for e in stmts)
    n_raters, freq = raters.most_common(1)[0]
    by = f" door {n_raters} beoordelaars" if n_raters > 1 and freq >= 0.8 * len(stmts) else ""
    where = f"in {len(buurten)} buurten ({_join_nl(buurten)})" if len(buurten) > 1 else (f"in {buurten[0]}" if buurten else "")
    per = "Per buurt zijn" if len(buurten) > 1 else "Daarbij zijn"
    ind = f" ({indicators} indicatoren)" if indicators else ""
    return (f"- **Expertperspectief**: een observatiewandeling {where}. {per} {n_per} stellingen over zes thema's{ind} "
            f"gescoord{by} op een schaal van 1 (heel slecht) tot 5 (uitstekend), met opmerkingen.")


def _resident_line(ev: list[Evidence]) -> Optional[str]:
    resp = [e for e in ev if e.kind == "respondents"]
    parts = []
    for e in resp:
        n = e.data.get("n_respondents")
        parts.append(f"{e.data['group']} ({n} respondenten)" if e.data.get("group") else f"bewoners ({n} respondenten)")
    stakeholders = any(e.data.get("is_stakeholder") for e in ev if e.kind == "coded")
    coded = any(e.kind == "coded" for e in ev)
    if not parts and not coded:
        return None
    line = f"- **Bewonersperspectief**: {'een vragenlijst' if len(parts) == 1 else 'vragenlijsten'} onder {_join_nl(parts)}" if parts \
        else "- **Bewonersperspectief**: vragenlijsten onder bewoners"
    if stakeholders:
        line += ", en een vragenlijst onder stakeholders (professionals en betrokkenen in de buurten)"
    line += "."
    if coded:
        line += " De open antwoorden zijn door de onderzoekers gecodeerd als behoefte, belemmering of positieve bevinding."
    return line


def _data_line(ev: list[Evidence], inp: Optional[ReportInputs] = None) -> Optional[str]:
    maps_ = sorted({e.statement.split(":")[0].split(" (")[0] for e in ev
                    if e.kind == "map_measurement" and e.data.get("area") == "onderzoeksgebied"})
    readings = [e for e in ev if e.kind == "image_reading"]
    demo = [e for e in ev if e.kind == "demographic"]
    infographics = [] if inp is None else [r for r in inp.store.all() if r.case_id == inp.case_id
                                           and r.extra.get("record_kind") == "image"
                                           and r.extra.get("image_kind") == "infographic"]
    parts = []
    if demo:
        parts.append("demografische kerncijfers en de index beweegvriendelijke omgeving voor buurt, gemeente en Nederland")
    if readings:
        conf = sum(1 for e in readings if e.data.get("confirmed"))
        parts.append(f"demografische infographics, afgelezen met een taalmodel ({conf} van {len(readings)} waarden "
                     f"bevestigd door een onderzoeker)")
    elif infographics:
        parts.append(f"{len(infographics)} demografische infographics (nog niet afgelezen; hun cijfers staan daarom "
                     f"niet in dit concept)")
    if maps_:
        parts.append(f"kaarten van het onderzoeksgebied ({_join_nl(maps_)})")
    if not parts:
        return None
    line = f"- **Dataperspectief**: {_join_nl(parts)}."
    if maps_:
        line += (" Kaartwaarden zijn deterministisch uit de kaartafbeeldingen geteld (aandeel bebouwd oppervlak of "
                 "aantal markeringen; indicatief, met een controleafbeelding per kaart).")
    return line


def _rules_line(ev: list[Evidence]) -> str:
    rivm = load_config("reference/rivm_vuistregels.toml")
    titles = {r["id"]: r["title"] for r in rivm["rule"]}
    tested = sorted({rule for e in ev if e.kind == "map_measurement" and e.data.get("tests_rule")
                     and e.data.get("area") == "onderzoeksgebied" for rule in e.data.get("rules", [])})
    if not tested:
        return ("De RIVM-vuistregels Bewegen (2025) dienen als kader. Deze gegevens bevatten geen afstandsmetingen; "
                "de vuistregels zijn daarom niet getoetst.")
    untested = [r for r in titles if r not in tested]
    line = (f"De RIVM-vuistregels Bewegen (2025) dienen als kader. Getoetst met kaartgegevens: "
            f"{_join_nl([f'{r} ({titles[r].lower()})' for r in tested])}.")
    if untested:
        line += f" Niet getoetst, omdat daarvoor geen gegevens zijn: {_join_nl([f'{r} ({titles[r].lower()})' for r in untested])}."
    return line


def _notes_line(ev: list[Evidence]) -> Optional[str]:
    notes = [e for e in ev if e.kind == "note"]
    if not notes:
        return None
    kinds = Counter(e.data.get("note_type_label", e.data.get("note_type")) for e in notes)
    return (f"- **Onderzoekersnotities**: {len(notes)} vastgelegde notities "
            f"({_join_nl([f'{v}× {k}' for k, v in kinds.most_common()])}), elk met bron en datum.")


def _plain(line: Optional[str]) -> Optional[str]:
    """'- **Expertperspectief**: text' -> 'text' (for renderers without Markdown)."""
    if not line:
        return None
    return line.split("**: ", 1)[1] if "**: " in line else line.lstrip("- ")


def methode_parts(evidence: dict, store: SourceStore, case_id: str, report_kind: str, case_info: Optional[dict] = None) -> dict:
    """The method section as structured parts (one per perspective + the rules),
    for the Word renderer. Same facts as methode_text."""
    ev = list(evidence.values())
    probe = ReportInputs(case_id, "", {}, evidence, store, [], case_info or {})
    rivm = load_config("reference/rivm_vuistregels.toml")
    tested = sorted({rule for e in ev if e.kind == "map_measurement" and e.data.get("tests_rule")
                     and e.data.get("area") == "onderzoeksgebied" for rule in e.data.get("rules", [])})
    return {
        "intro": f"Deze {report_kind} combineert drie perspectieven per thema: data, experts en bewoners.",
        "expert": _plain(_expert_line(ev)), "bewoners": _plain(_resident_line(ev)),
        "data": _plain(_data_line(ev, probe)), "notes": _plain(_notes_line(ev)), "rules_text": _rules_line(ev),
        "tested_rules": [r for r in rivm["rule"] if r["id"] in tested],
        "untested_rules": [r for r in rivm["rule"] if r["id"] not in tested],
    }


def methode_text(inp: ReportInputs) -> str:
    """Deterministic method section, built from what was actually ingested and
    measured for this case (no fixed text that could be false for a case)."""
    ev = list(inp.evidence.values())
    lines = ["_Automatisch samengesteld uit de ingelezen bronbestanden._", "",
             f"Deze {inp.report_kind} combineert drie perspectieven per thema:"]
    lines += [x for x in (_expert_line(ev), _resident_line(ev), _data_line(ev, inp), _notes_line(ev)) if x]
    lines += ["", _rules_line(ev)]
    return "\n".join(lines)


def _visible(st: ClaimState, preview: bool) -> bool:
    return st.status != "rejected" and (st.status in ("approved", "edited") or preview)


def literature(inp: ReportInputs) -> list[str]:
    """Literature cited by researcher notes of type 'Literatuur' (deduplicated)."""
    seen: list[str] = []
    for e in inp.evidence.values():
        if e.kind == "note" and e.data.get("note_type") == "literatuur" and e.data.get("source"):
            if e.data["source"] not in seen:
                seen.append(e.data["source"])
    return seen


def render_markdown(inp: ReportInputs, preview: bool = False, with_ids: bool = True,
                    required_sections: Optional[list[str]] = None) -> str:
    reasons = blocking_reasons(inp, required_sections)
    if reasons and not preview:
        raise ExportBlocked(reasons)
    out = [f"# {inp.report_kind} {inp.case_label}", ""]
    if preview and reasons:
        out += ["> **CONCEPT — NIET VOOR VERSPREIDING.** Nog niet volledig beoordeeld:", ""]
        out += [f"> - {r}" for r in reasons] + [""]
    states = {sec: list(s.states().values()) for sec, s in inp.sessions.items()}
    cite = (lambda ids: _cite(ids)) if with_ids else (lambda ids: "")

    cross = [st for st in states.get(CONCLUSIES, []) if st.claim_type == "conclusie" and _visible(st, preview)]
    per_theme = [(sec, st) for sec in THEMES for st in states.get(sec, [])
                 if st.claim_type == "conclusie" and _visible(st, preview)]
    out += ["## Samenvatting", ""]
    if cross:
        out += [f"- {st.text}{cite(st.evidence_ids)}" for st in cross]
    else:
        out += [f"- **{sec}**: {st.text}{cite(st.evidence_ids)}" for sec, st in per_theme] or ["_(nog geen goedgekeurde conclusies)_"]
    if any(st.status != "rejected" for st in states.get(AANLEIDING, [])):
        out += ["", "## Aanleiding", "", _claims_md(states[AANLEIDING], preview, with_ids) or "_(geen goedgekeurde beweringen)_"]
    out += ["", "## Methode", "", methode_text(inp), "", "## Resultaten", ""]
    if GENERAL in states:
        out += ["### Demografie en respondenten", "", _claims_md(states[GENERAL], preview, with_ids), ""]
    for i, theme in enumerate(THEMES, start=1):
        if theme not in states:
            continue
        body = _claims_md([s for s in states[theme] if s.claim_type != "aanbeveling"], preview, with_ids)
        out += [f"### Thema {i}: {theme}", "", body or "_(geen goedgekeurde beweringen)_", ""]
    out += ["## Conclusies", ""]
    if CONCLUSIES in states:
        out += [_claims_md([s for s in states[CONCLUSIES] if s.claim_type != "aanbeveling"], preview, with_ids)
                or "_(geen goedgekeurde beweringen)_", ""]
        if per_theme:
            out += ["### Per thema", ""]
    out += [f"**{sec}.** {st.text}{cite(st.evidence_ids)}" for sec, st in per_theme] \
        or ([] if CONCLUSIES in states else ["_(nog geen goedgekeurde conclusies)_"])
    out += ["", "## Aanbevelingen", ""]
    recs = [st for sec in states for st in states[sec] if st.claim_type == "aanbeveling" and st.status in ("approved", "edited")]
    out += [f"- {st.text}" for st in recs] or ["_Aanbevelingen worden door de onderzoeker opgesteld op basis van de goedgekeurde bevindingen en conclusies._"]
    out += ["", "## Bronnenlijst", ""]
    rivm = load_config("reference/rivm_vuistregels.toml")
    out.append(f"- {rivm['citation']}")
    out += [f"- {ref}" for ref in literature(inp)]
    out += ["", "**Bronbestanden van deze analyse**", ""]
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
    stem = f"{inp.report_kind.lower()}_{inp.case_id}{suffix}"
    files = {"markdown": out / f"{stem}.md", "evidence_map": out / f"evidence_map_{inp.case_id}{suffix}.csv"}
    files["markdown"].write_text(md, encoding="utf-8")
    files["evidence_map"].write_text(evidence_map_csv(inp), encoding="utf-8")
    try:
        files["docx"] = write_docx(md, out / f"{stem}.docx")
    except ImportError:
        pass
    return files
