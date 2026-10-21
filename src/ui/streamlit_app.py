"""Researcher review interface (UC1-UC6, SQ4). Thin: calls app.services only.

Run from the repo root:
    PYTHONPATH=src streamlit run src/ui/streamlit_app.py

Environment (optional):
    INNO_WORKSPACE   workspace folder (default ./workspace)
    INNO_DATA_DIR    default folder offered for ingestion
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make src/ importable

import streamlit as st  # noqa: E402

from app.services import SECTIONS, Workspace  # noqa: E402
from drafting.models import CLAIM_TYPE_LABELS  # noqa: E402
from drafting.pipelines import PIPELINES, PipelineConfig  # noqa: E402
from review.session import RESOLVED, NeedsOverride, ReviewError  # noqa: E402

STATUS_BADGE = {"grounded": "🟢 onderbouwd", "weak": "🟠 zwak onderbouwd", "unsupported": "🔴 niet onderbouwd"}
REVIEW_BADGE = {"draft": "⏳ te beoordelen", "approved": "✅ goedgekeurd", "edited": "✏️ aangepast", "rejected": "🗑️ verwijderd"}
PERSPECTIVE_NL = {"expert": "Expert", "bewoners": "Bewoners", "data": "Data", "reference": "RIVM", "cross": "Vergelijking"}

st.set_page_config(page_title="InnoBeweegLab rapportage", layout="wide")


def rerun() -> None:
    (getattr(st, "rerun", None) or st.experimental_rerun)()


@st.cache_resource
def get_ws(path: str) -> Workspace:
    return Workspace(Path(path))


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("Buurtscan-rapportage")
    st.caption("Onderzoeksprototype. Alle tekst is een CONCEPT tot een onderzoeker die goedkeurt.")
    ws_path = st.text_input("Werkmap", os.environ.get("INNO_WORKSPACE", "workspace"))
    ws = get_ws(ws_path)
    reviewer = st.text_input("Jouw naam (voor het audit-log)", st.session_state.get("reviewer", ""))
    st.session_state["reviewer"] = reviewer
    case_id = None
    if ws.has_data():
        cases = ws.cases()
        if cases:
            case_id = st.selectbox("Buurt (case)", sorted(cases), format_func=lambda c: cases[c]["label"])
    else:
        st.info("Nog geen data ingelezen. Begin bij tab 1.")

tab_data, tab_gen, tab_rev, tab_exp, tab_runs = st.tabs(
    ["1 · Data", "2 · Concept maken", "3 · Beoordelen", "4 · Exporteren", "5 · Experimenten"])

# ---------------------------------------------------------------- 1 data (UC1)
with tab_data:
    st.subheader("Bronbestanden inlezen")
    folder = st.text_input("Map met bronbestanden", os.environ.get("INNO_DATA_DIR", ""))
    if st.button("Inlezen", disabled=not folder):
        with st.spinner("Bestanden inlezen en controleren…"):
            try:
                ws.ingest(folder)
                st.success("Ingelezen.")
                rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Inlezen mislukt: {exc}")
    if ws.has_data():
        rep = ws.ingestion_report()
        c1, c2, c3 = st.columns(3)
        c1.metric("Bestanden", rep["files"])
        c2.metric("Buurten", len(rep["cases"]))
        c3.metric("Bronrecords", rep["records"]["total"])
        st.markdown("**Herkende buurten en perspectieven** (FR2: gedetecteerd, niet stilzwijgend toegepast)")
        st.dataframe([{"buurt": v["label"], "compleet": "ja" if v.get("complete") else "NEE",
                       **{p: Path(f).name for p, f in v["perspectives"].items()}} for v in rep["cases"].values()],
                     use_container_width=True)
        if rep["duplicates_skipped"]:
            with st.expander(f"{len(rep['duplicates_skipped'])} dubbele bestanden overgeslagen (identieke checksum)"):
                st.write(rep["duplicates_skipped"])
        if rep["unparsed"]:
            st.warning("Niet ingelezen: " + "; ".join(rep["unparsed"]))
        if rep["validation_problems"]:
            st.error("Tellingen wijken af van de totaalrij in de vragenlijst:")
            st.write(rep["validation_problems"])
        else:
            st.caption("✔ Alle tellingen uit de vragenlijsten komen overeen met de totaalrij van het bronbestand.")
        if rep["unmapped_survey_questions"]:
            st.warning("Vragen zonder thema-koppeling (pas config/survey_mapping.toml aan): "
                       + "; ".join(rep["unmapped_survey_questions"]))
        st.caption(f"{rep['records']['flagged_for_manual_mapping']} records gemarkeerd voor handmatige controle "
                   "(o.a. grafieken die alleen als afbeelding bestaan).")

# ---------------------------------------------------------------- 2 generate (UC2, UC5)
with tab_gen:
    if not case_id:
        st.info("Lees eerst data in.")
    else:
        st.subheader(f"Concept maken · {ws.case_label(case_id)}")
        section = st.selectbox("Onderdeel", SECTIONS, key="gen_section")
        packs = ws.evidence(case_id)
        pack = packs[section]
        col1, col2, col3 = st.columns(3)
        pipeline = col1.selectbox("Pijplijn", PIPELINES, index=1,
                                  help="template = zonder taalmodel; naive = één prompt; staged = per perspectief + synthese")
        models = ws.models()
        model_id = col2.selectbox("Model", list(models), disabled=pipeline == "template",
                                  format_func=lambda m: f"{m} ({models[m].get('location', 'local')})")
        selection = col3.selectbox("Bewijsselectie", ["all", "top_k:15", "top_k:25"])
        problems = [] if pipeline == "template" else ws.model_problems(model_id)
        for p in problems:
            st.error(p)
        if model_id == "fake" and pipeline != "template":
            st.warning("Het nepmodel maakt testuitvoer, geen onderzoeksresultaat.")
        with st.expander(f"Bewijsstukken voor dit onderdeel ({len(pack.items)})"):
            for e in pack.items:
                st.markdown(f"`{e.evidence_id}` **{PERSPECTIVE_NL.get(e.perspective, e.perspective)}** · {e.statement}")
            if pack.unavailable:
                st.caption("Niet beschikbaar: " + " | ".join(pack.unavailable))
        existing = ws.session(case_id, section)
        if existing and any(ev.claim_id for ev in existing.events):
            st.warning("Er is al een beoordeling voor dit onderdeel. Een nieuw concept archiveert die beoordeling (niets gaat verloren).")
        if st.button("Concept genereren", type="primary", disabled=bool(problems) or not reviewer):
            with st.spinner("Bezig… (lokale modellen kunnen enkele minuten nodig hebben)"):
                try:
                    d = ws.generate(case_id, section, PipelineConfig(pipeline_id=pipeline, selection=selection),
                                    None if pipeline == "template" else model_id, reviewer)
                    if d.errors:
                        st.error("Fouten: " + " | ".join(d.errors))
                    st.success(f"{len(d.claims)} beweringen gemaakt. Ga naar tab 3 om te beoordelen.")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Mislukt: {exc}")
        if not reviewer:
            st.caption("Vul links je naam in; elke actie wordt op naam vastgelegd.")

# ---------------------------------------------------------------- 3 review (UC3, UC4)
def render_evidence(eid: str, index: dict) -> None:
    e = index.get(eid)
    if e is None:
        st.error(f"`{eid}` bestaat niet in de bewijsstukken van deze buurt.")
        return
    st.markdown(f"`{e.evidence_id}` · **{PERSPECTIVE_NL.get(e.perspective, e.perspective)}** · {e.statement}")
    recs = ws.source_records(e)
    rows = [{"bestand": r.source_file, "blad": r.sheet or "", "locatie": r.locator,
             "waarde": r.value if r.value is not None else "", "tekst": (r.text or "")[:120]} for r in recs[:15]]
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    if len(recs) > 15:
        st.caption(f"… en nog {len(recs) - 15} bronrecords (allemaal in evidence_map.csv bij export).")


with tab_rev:
    if not case_id:
        st.info("Lees eerst data in.")
    else:
        rsec = st.selectbox("Onderdeel", SECTIONS, key="rev_section")
        session = ws.session(case_id, rsec)
        if session is None:
            st.info("Nog geen concept voor dit onderdeel (tab 2).")
        else:
            index = ws.evidence_index(case_id)
            d = session.draft
            states = session.states()
            stats = session.stats()
            vcounts = {k: sum(1 for s in states.values() if (s.verification or {}).get("status") == k)
                       for k in STATUS_BADGE}
            st.subheader(f"{rsec} · {ws.case_label(case_id)}")
            st.caption(f"Concept {d.draft_id} · pijplijn {d.pipeline_id} · model {(d.model or {}).get('model', 'geen')} · {d.created_at}")
            m = st.columns(6)
            m[0].metric("🔴 niet onderbouwd", vcounts["unsupported"])
            m[1].metric("🟠 zwak", vcounts["weak"])
            m[2].metric("🟢 onderbouwd", vcounts["grounded"])
            m[3].metric("Te beoordelen", stats["pending"])
            m[4].metric("Overrides", stats["overrides"])
            m[5].metric("Audit-log", "intact" if stats["chain_ok"] else "GEWIJZIGD")
            if d.errors:
                st.error("Fouten tijdens genereren: " + " | ".join(d.errors))
            checks = d.section_checks or {}
            if checks.get("coverage_gaps"):
                with st.expander(f"⚠️ {len(checks['coverage_gaps'])} opvallende bewijsstukken worden nergens genoemd"):
                    for gid in checks["coverage_gaps"]:
                        e = index.get(gid)
                        if e:
                            st.markdown(f"`{gid}` {e.statement}")
            if checks.get("repeated_openers"):
                st.caption("Herhaalde zinsopeningen (TR9): " + ", ".join(checks["repeated_openers"]))
            only_open = st.checkbox("Toon alleen nog te beoordelen beweringen", value=False)
            order = {"unsupported": 0, "weak": 1, "grounded": 2, None: 3}
            for cid, s in sorted(states.items(), key=lambda kv: (kv[1].paragraph, kv[0])):
                if only_open and s.status in RESOLVED:
                    continue
                v = s.verification or {}
                with st.container(border=True):
                    top = st.columns([3, 2, 2])
                    top[0].markdown(f"**{cid}** · {CLAIM_TYPE_LABELS.get(s.claim_type, s.claim_type)}"
                                    + (" · _door onderzoeker_" if s.origin == "human" else ""))
                    top[1].markdown(STATUS_BADGE.get(v.get("status"), "⚪ niet gecontroleerd"))
                    top[2].markdown(REVIEW_BADGE.get(s.status, s.status) + (" (override)" if s.override else ""))
                    st.markdown(f"> {s.text}")
                    grounding = [f for f in v.get("flags", []) if f["severity"] != "style"]
                    style = [f for f in v.get("flags", []) if f["severity"] == "style"]
                    for f in grounding:
                        st.markdown(f"{'🔴' if f['severity'] == 'unsupported' else '🟠'} {f['message']}")
                    if style:
                        st.caption("Schrijfregels: " + " · ".join(f"{f['message']} ({f['rule']})" for f in style))
                    with st.expander("Bewijs en bron" + (f" ({len(s.evidence_ids)})" if s.evidence_ids else " — geen bron gevonden")):
                        if not s.evidence_ids:
                            st.error("Geen bron gevonden: deze bewering verwijst naar geen enkel bewijsstuk.")
                        for eid in s.evidence_ids:
                            render_evidence(eid, index)
                    if s.status not in RESOLVED:
                        a = st.columns([1, 1, 1, 3])
                        flagged = v.get("status") in ("weak", "unsupported")
                        confirm = False
                        if flagged:
                            confirm = st.checkbox("Ik heb het bewijs gecontroleerd en keur deze gemarkeerde bewering bewust goed",
                                                  key=f"ovr_{rsec}_{cid}")
                        if a[0].button("Goedkeuren", key=f"ok_{rsec}_{cid}", disabled=not reviewer or (flagged and not confirm)):
                            try:
                                session.approve(cid, actor=reviewer, confirm_override=confirm)
                                ws.save_session(session)
                                rerun()
                            except (NeedsOverride, ReviewError) as exc:
                                st.error(str(exc))
                        if a[1].button("Verwijderen", key=f"rej_{rsec}_{cid}", disabled=not reviewer):
                            session.reject(cid, actor=reviewer)
                            ws.save_session(session)
                            rerun()
                    with st.expander("Aanpassen / opmerking / geschiedenis"):
                        new_text = st.text_area("Tekst", s.text, key=f"txt_{rsec}_{cid}")
                        new_ids = st.text_input("Bewijsstukken (komma-gescheiden)", ", ".join(s.evidence_ids),
                                                key=f"ids_{rsec}_{cid}")
                        note = st.text_input("Opmerking", key=f"note_{rsec}_{cid}")
                        b = st.columns(3)
                        if b[0].button("Aanpassing opslaan", key=f"edit_{rsec}_{cid}", disabled=not reviewer):
                            ids = [x.strip() for x in new_ids.split(",") if x.strip()]
                            try:
                                session.edit(cid, new_text, actor=reviewer, comment=note or None,
                                             evidence_ids=ids if ids != s.evidence_ids else None)
                                ws.save_session(session)
                                rerun()
                            except ReviewError as exc:
                                st.error(str(exc))
                        if b[1].button("Alleen opmerking", key=f"cm_{rsec}_{cid}", disabled=not reviewer or not note):
                            session.comment(cid, note, actor=reviewer)
                            ws.save_session(session)
                            rerun()
                        if s.status in RESOLVED and b[2].button("Heropenen", key=f"re_{rsec}_{cid}", disabled=not reviewer):
                            session.reopen(cid, actor=reviewer)
                            ws.save_session(session)
                            rerun()
                        if s.original_text and s.original_text != s.text:
                            st.caption("Oorspronkelijke AI-tekst:")
                            st.markdown(f"~~{s.original_text}~~")
                        for ev in session.events:
                            if ev.claim_id == cid:
                                st.caption(f"{ev.ts} · {ev.actor} · {ev.action}"
                                           + (f" · systeemstatus: {ev.verification_status}" if ev.verification_status else "")
                                           + (f" · {ev.comment}" if ev.comment else ""))
            with st.expander("➕ Eigen bewering toevoegen"):
                t = st.text_area("Tekst", key=f"new_txt_{rsec}")
                ctype = st.selectbox("Soort", list(CLAIM_TYPE_LABELS), key=f"new_type_{rsec}")
                ids = st.text_input("Bewijsstukken (komma-gescheiden)", key=f"new_ids_{rsec}")
                if st.button("Toevoegen", key=f"new_add_{rsec}", disabled=not (reviewer and t.strip())):
                    session.add_claim(t.strip(), ctype, [x.strip() for x in ids.split(",") if x.strip()], actor=reviewer,
                                      paragraph=max((s.paragraph for s in states.values()), default=1))
                    ws.save_session(session)
                    rerun()

# ---------------------------------------------------------------- 4 export (UC6)
with tab_exp:
    if not case_id:
        st.info("Lees eerst data in.")
    else:
        st.subheader(f"Rapport exporteren · {ws.case_label(case_id)}")
        present = list(ws.sessions(case_id))
        required = st.multiselect("Onderdelen die in het rapport moeten", SECTIONS, default=[s for s in SECTIONS if s in present] or SECTIONS[:6])
        blockers = ws.export_blockers(case_id, required)
        if blockers:
            st.error("Export geblokkeerd (FR11):")
            for b in blockers:
                st.markdown(f"- {b}")
        else:
            st.success("Alle beweringen in deze onderdelen zijn beoordeeld.")
        c = st.columns(2)
        files = None
        if c[0].button("Exporteren", type="primary", disabled=bool(blockers)):
            files = ws.export(case_id, required_sections=required)
        if c[1].button("Conceptversie (intern, met markering)"):
            files = ws.export(case_id, preview=True, required_sections=required)
        if files:
            for kind, path in files.items():
                st.download_button(f"Download {Path(path).name}", Path(path).read_bytes(), file_name=Path(path).name,
                                   key=f"dl_{kind}")
            st.markdown(Path(files["markdown"]).read_text(encoding="utf-8"))

# ---------------------------------------------------------------- 5 experiments (SQ3)
with tab_runs:
    st.subheader("Experimenten")
    st.caption("Lange runs liever via de command line: `python -m experiments.cli run E1 --workspace workspace`.")
    if ws.has_data():
        from experiments.runner import summarise_runs  # noqa: E402
        rows = summarise_runs(ws)
        if rows:
            st.dataframe([{"run": r["run_id"], "exp": r["experiment"], "pijplijn": r["pipeline"],
                           "selectie": r["selection"], "model": r["model"], "n": r["summary"]["n"],
                           "mislukt": r["summary"]["failed"],
                           "onderbouwd": r["summary"].get("grounded_rate", {}).get("mean"),
                           "niet onderbouwd": r["summary"].get("unsupported_rate", {}).get("mean"),
                           "dekking": r["summary"].get("coverage_salient", {}).get("mean"),
                           "sec/sectie": r["summary"].get("wall_s", {}).get("mean")} for r in rows],
                         use_container_width=True, hide_index=True)
        else:
            st.info("Nog geen runs.")
