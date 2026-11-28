"""Researcher review interface (UC1-UC6, SQ4). Thin: calls app.services and app.jobs only.

Run from the repo root (or double-click scripts/start_app.bat):
    PYTHONPATH=src streamlit run src/ui/streamlit_app.py

Normal flow: 1 Data -> 2 Concept (full report in the background, or one section) -> 3 Beoordelen -> 4 Exporteren.
"Expertmodus" in the sidebar adds the research parts: images, report per model, experiments.

Environment (optional):
    INNO_WORKSPACE   workspace folder (default ./workspace)
    INNO_DATA_DIR    default folder offered for ingestion
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make src/ importable

import streamlit as st  # noqa: E402

from app import jobs  # noqa: E402
from app.services import SECTIONS, Workspace  # noqa: E402
from drafting.models import CLAIM_TYPE_LABELS  # noqa: E402
from drafting.pipelines import PipelineConfig  # noqa: E402
from review.session import RESOLVED, NeedsOverride, ReviewError  # noqa: E402

STATUS_BADGE = {"grounded": "🟢 onderbouwd", "weak": "🟠 zwak onderbouwd", "unsupported": "🔴 niet onderbouwd"}
REVIEW_BADGE = {"draft": "⏳ te beoordelen", "approved": "✅ goedgekeurd", "edited": "✏️ aangepast", "rejected": "🗑️ verwijderd"}
JOB_BADGE = {"pending": "⏳ wacht", "running": "✍️ bezig", "done": "✅ klaar", "failed": "❌ mislukt", "skipped": "– geen bewijs"}
PERSPECTIVE_NL = {"expert": "Expert", "bewoners": "Bewoners", "data": "Data", "reference": "RIVM", "cross": "Vergelijking",
                  "context": "Context"}
SECTION_HELP = {
    "Aanleiding": "Gebaseerd op onderzoekersnotities van de soort 'Projectcontext' (tab 1 › Onderzoekersnotities).",
    "Conclusies": "Over de thema's heen: scores per thema, getoetste vuistregels, factoren die bij meerdere thema's "
                  "terugkomen. Aanbevelingen schrijf je zelf.",
}
# The pipelines a researcher can choose. staged (E2) is the stage-1 winner; prompt versions as in
# config/experiments.toml (E2: v3, E4: v4). Times measured on a 16 GB office laptop without GPU (stage 1).
PIPELINE_CHOICES = {
    "staged": ("Per perspectief + synthese (aanbevolen)", "v3",
               "Eerst bevindingen per perspectief (data, experts, bewoners), daarna één tekst. "
               "Ca. 10 minuten per onderdeel; een volledig rapport ca. 45 minuten (kleine buurtscan) tot 1,5 uur (Oss)."),
    "staged_checked": ("Per perspectief + controle", "v4",
                       "Als hierboven; daarna herschrijft het model alleen de beweringen die de controle afkeurt. "
                       "Ca. 15 minuten per onderdeel; een volledig rapport ca. 1 tot 2,5 uur."),
    "naive": ("Eén opdracht per onderdeel", "v3",
              "Sneller, maar vaker beweringen zonder goede onderbouwing. Ca. 8 minuten per onderdeel."),
    "template": ("Sjabloon zonder taalmodel", "v3",
                 "Vaste zinnen rechtstreeks uit de bewijsstukken. Klaar in seconden; handig om de tool te laten zien."),
}
DEFAULT_MODEL = "local_qwen7b"

st.set_page_config(page_title="InnoBeweegLab rapportage", layout="wide")


def rerun() -> None:
    (getattr(st, "rerun", None) or st.experimental_rerun)()


@st.cache_resource
def get_ws(path: str) -> Workspace:
    return Workspace(Path(path))


def _pct(x) -> str:
    return "–" if x is None else f"{round(100 * x)}%"


def _when(ts) -> str:
    """Local time from a timestamp or an ISO string (run records store UTC)."""
    if not ts:
        return "–"
    if isinstance(ts, str):
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (t.astimezone() if t.tzinfo else t).strftime("%d-%m %H:%M")
    return dt.datetime.fromtimestamp(ts).strftime("%d-%m %H:%M")


def pipeline_picker(key: str, include_template: bool = True) -> tuple[str, str]:
    opts = [p for p in PIPELINE_CHOICES if include_template or p != "template"]
    p = st.radio("Werkwijze", opts, index=0, key=key, format_func=lambda x: PIPELINE_CHOICES[x][0])
    st.caption(PIPELINE_CHOICES[p][2])
    return p, PIPELINE_CHOICES[p][1]


def model_picker(key: str, pipeline: str) -> str | None:
    if pipeline == "template":
        return None
    if not expert:
        return DEFAULT_MODEL
    models = ws.models()
    return st.selectbox("Model", list(models), index=list(models).index(DEFAULT_MODEL) if DEFAULT_MODEL in models else 0,
                        key=key, format_func=lambda m: f"{m} · {models[m].get('model', '')} ({models[m].get('location', 'local')})")


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("Wijkscan-rapportage")
    st.caption("Onderzoeksprototype. Alle tekst is een CONCEPT tot een onderzoeker die goedkeurt. "
               "Alles blijft op deze laptop.")
    ws_path = st.text_input("Werkmap", os.environ.get("INNO_WORKSPACE", "workspace"))
    ws = get_ws(ws_path)
    name_file = ws.p("laatste_naam.txt")              # remembered across reloads of the page
    saved = name_file.read_text(encoding="utf-8").strip() if name_file.exists() else ""
    reviewer = st.text_input("Jouw naam (voor het audit-log)", st.session_state.get("reviewer") or saved).strip()
    st.session_state["reviewer"] = reviewer
    if reviewer and reviewer != saved:
        name_file.write_text(reviewer, encoding="utf-8")
    case_id = None
    if ws.has_data():
        cases = ws.cases()
        if cases:
            case_id = st.selectbox("Buurt (case)", sorted(cases), format_func=lambda c: cases[c]["label"])
    else:
        st.info("Nog geen data ingelezen. Begin bij tab 1.")
    running = jobs.active_job(ws)
    if running:
        done = sum(v == "done" for v in running["sections"].values())
        st.info(f"Rapport wordt gemaakt: {done} van {len(running['sections'])} onderdelen klaar (tab 2).")
    st.divider()
    expert = st.checkbox("Expertmodus", value=False,
                         help="Toont de onderzoeksonderdelen: afbeeldingen, rapport per model, vergelijking met het "
                              "origineel, experimenten.")

names = ["1 · Data", "2 · Concept maken", "3 · Beoordelen", "4 · Exporteren"]
if expert:
    names += ["1b · Afbeeldingen", "5 · Rapport per model", "6 · Experimenten"]
tabs = dict(zip(names, st.tabs(names)))
tab_data, tab_gen, tab_rev, tab_exp = (tabs[n] for n in names[:4])

# ---------------------------------------------------------------- 1 data (UC1)
with tab_data:
    st.subheader("Bronbestanden inlezen")
    folder = st.text_input("Map met bronbestanden", os.environ.get("INNO_DATA_DIR", ""))
    st.caption("Buurtscan-map (bestanden per buurt naast elkaar) of Wijkscan-map (submappen per perspectief, zoals Oss). "
               "Eerder ingelezen buurten blijven behouden. Kaarten worden daarna automatisch gemeten (zonder taalmodel).")
    c_id, c_label = st.columns(2)
    new_case_id = c_id.text_input("Case-id (alleen Wijkscan-map)", "euterpepark-oss")
    new_case_label = c_label.text_input("Naam (alleen Wijkscan-map)", "Euterpepark Oss")
    if st.button("Inlezen", disabled=not folder):
        with st.spinner("Bestanden inlezen en controleren…"):
            try:
                res = ws.ingest(folder, case_id=new_case_id or None, case_label=new_case_label or None)
                for cid in (res.get("cases") or {}):
                    try:
                        ws.analyse_images(cid, None)
                    except Exception as exc:  # noqa: BLE001 - maps are optional; the report still works
                        st.warning(f"Kaarten van {cid} niet gemeten: {exc}")
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
                     width="stretch")
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

    if case_id:
        st.divider()
        st.subheader(f"Onderzoekersnotities · {ws.case_label(case_id)}")
        st.caption("Wat jullie weten maar wat in geen bronbestand staat: observaties tijdens de wandeling (plek, foto), "
                   "wat bewoners op de bijeenkomst zeiden, projectcontext voor de aanleiding, literatuur. Elke notitie "
                   "krijgt een bewijsstuk-id (O-…) en een bron. Wat hier niet staat, mag de tool niet schrijven.")
        tmpl = ws.p("notes", "Onderzoekersnotities_sjabloon.xlsx")
        if not tmpl.exists():
            ws.write_notes_template(tmpl)
        st.download_button("Download leeg sjabloon (Excel)", tmpl.read_bytes(), file_name="Onderzoekersnotities.xlsx")
        up = st.file_uploader("Ingevulde notities (Excel, werkblad 'Notities')", type=["xlsx"], key="notes_upload")
        if up is not None and st.button("Notities inlezen", disabled=not reviewer):
            dest = ws.p("notes", case_id, up.name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(up.getvalue())
            try:
                res = ws.ingest_notes(dest, case_id)
                st.success(f"{res['records']} notities ingelezen ({', '.join(f'{v}× {k}' for k, v in res['by_type'].items())}).")
                for prob in res["problems"]:
                    st.warning(prob)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Inlezen mislukt: {exc}")
        notes = ws.notes(case_id)
        if notes:
            st.dataframe([{"soort": r.subtheme, "thema": ", ".join(r.extra.get("themes") or []) or "Algemeen",
                           "plek": r.extra.get("place") or "", "notitie": r.text, "bron": r.extra.get("source") or "⚠️ ontbreekt",
                           "bestand": f"{r.source_file} › {r.locator}"} for r in notes],
                         width="stretch", hide_index=True)


# ---------------------------------------------------------------- 2 concept: full report (background job) or one section
def job_panel(case_id: str) -> None:
    """Progress of the latest full report of this case. Refreshes itself while the report is being made."""
    job = jobs.latest_job(ws, case_id)
    if job is None:
        return
    secs = job["sections"]
    n_done = sum(v == "done" for v in secs.values())
    head = (f"{PIPELINE_CHOICES.get(job['pipeline'], (job['pipeline'],))[0]} · gestart {_when(job.get('created'))}"
            + (f" · door {job['reviewer']}" if job.get("reviewer") else ""))
    if job["status"] in jobs.ACTIVE:
        st.info(f"Rapport wordt gemaakt: {n_done} van {len(secs)} onderdelen klaar. Je kunt dit venster sluiten; "
                f"het rapport loopt door. {head}")
    elif job["status"] == "done":
        st.success(f"Rapport klaar: {job.get('n_claims', '?')} beweringen in {round((job.get('wall_s') or 0) / 60)} minuten. {head}"
                   + (f" Mislukt: {', '.join(job['failed_sections'])}." if job.get("failed_sections") else ""))
    elif job["status"] == "interrupted":
        st.warning(f"Rapport onderbroken: {job.get('interrupted_reason', '')}. {n_done} van {len(secs)} onderdelen "
                   f"zijn klaar en blijven bewaard. {head}")
    elif job["status"] == "failed":
        st.error(f"Rapport mislukt: {job.get('error')}. {head}")
    st.dataframe([{"onderdeel": s, "status": JOB_BADGE.get(v, v)} for s, v in secs.items()],
                 hide_index=True, width="stretch")
    if job["status"] == "interrupted":
        if st.button("Hervatten (slaat klare onderdelen over)", type="primary", disabled=not reviewer):
            try:
                jobs.resume_job(ws, job["job_id"], reviewer)
                rerun()
            except (jobs.JobRunning, ValueError) as exc:
                st.error(str(exc))
    if job["status"] == "done" and job.get("run_id"):
        if st.button("Gebruik dit rapport voor de beoordeling (tab 3)", type="primary", disabled=not reviewer,
                     key=f"adopt_job_{job['job_id']}"):
            secs_adopted = ws.adopt_report_run(case_id, job["run_id"], reviewer)
            st.success(f"Beoordeling gestart voor {len(secs_adopted)} onderdelen. Ga naar tab 3.")


with tab_gen:
    if not case_id:
        st.info("Lees eerst data in.")
    else:
        st.subheader(f"Concept maken · {ws.case_label(case_id)}")
        full, one = st.tabs(["Volledig rapport", "Eén onderdeel"])
        with full:
            st.caption("Het taalmodel schrijft alle onderdelen; dat loopt op de achtergrond door, ook als je de browser "
                       "sluit. Laat de laptop aan de lader en open staan. Daarna beoordeel je elke bewering (tab 3).")
            pipe, pv = pipeline_picker("job_pipe")
            mid = model_picker("job_model", pipe)
            active = jobs.active_job(ws)
            if active:
                st.warning(f"Er wordt al een rapport gemaakt ({ws.case_label(active['case_id'])}). Wacht tot dat klaar is.")
            if st.button("Start volledig rapport", type="primary", disabled=bool(active) or not reviewer):
                try:
                    jobs.start_job(ws, case_id, mid, pipe, pv, reviewer=reviewer)
                    rerun()
                except (jobs.JobRunning, ValueError) as exc:
                    st.error(str(exc))
            if not reviewer:
                st.caption("Vul links je naam in; elke actie wordt op naam vastgelegd.")
            run_every = 10 if active and active["case_id"] == case_id else None

            @st.fragment(run_every=run_every)
            def _progress():
                job_panel(case_id)
            _progress()

        with one:
            section = st.selectbox("Onderdeel", SECTIONS, key="gen_section")
            packs = ws.evidence(case_id)
            pack = packs[section]
            if section in SECTION_HELP:
                st.caption(SECTION_HELP[section])
            if not pack.items:
                st.info("Geen bewijsstukken voor dit onderdeel." + (" Lees eerst onderzoekersnotities in (tab 1)."
                                                                     if section == "Aanleiding" else ""))
            pipeline, prompt_version = pipeline_picker("gen_pipe")
            model_id = model_picker("gen_model", pipeline)
            selection = st.selectbox("Bewijsselectie", ["all", "top_k:15", "top_k:25"]) if expert else "all"
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
            busy = jobs.active_job(ws)
            if busy and pipeline != "template":
                st.caption("Er wordt een volledig rapport gemaakt: één onderdeel met het taalmodel kan pas daarna.")
            if st.button("Concept genereren", type="primary",
                         disabled=bool(problems) or not reviewer or bool(busy and pipeline != "template")):
                with st.spinner("Bezig… (met het taalmodel ca. 10 minuten)"):
                    try:
                        d = ws.generate(case_id, section, PipelineConfig(pipeline_id=pipeline, selection=selection,
                                                                          prompt_version=prompt_version),
                                        model_id, reviewer)
                        if d.errors:
                            st.error("Fouten: " + " | ".join(d.errors))
                        st.success(f"{len(d.claims)} beweringen gemaakt. Ga naar tab 3 om te beoordelen.")
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Mislukt: {exc}")


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
        st.dataframe(rows, width="stretch", hide_index=True)
    if len(recs) > 15:
        st.caption(f"… en nog {len(recs) - 15} bronrecords (allemaal in evidence_map.csv bij export).")


def adopt_picker(case_id: str) -> None:
    runs = [r for r in ws.report_runs(case_id) if r.get("usable", r["totals"].get("n_claims", 0) > 0)]
    if not runs:
        st.caption("Nog geen volledig rapport. Maak er een in tab 2, of maak per onderdeel een concept.")
        return

    def label(r):
        t = r["totals"]
        return (f"{_when(r['created_at'])} · {PIPELINE_CHOICES.get(r['pipeline'], (r['pipeline'],))[0]} · "
                f"{r.get('model_id') or 'zonder model'} · {t['n_claims']} beweringen"
                + (f" · {r['tag']}" if r.get("tag") else ""))
    rid = st.selectbox("Volledig rapport", [r["run_id"] for r in reversed(runs)], key="adopt_run",
                       format_func=lambda x: label(next(r for r in runs if r["run_id"] == x)))
    if ws.sessions(case_id):
        st.caption("Er is al een beoordeling. Een nieuw startpunt archiveert die (niets gaat verloren).")
    if st.button("Start de beoordeling met dit rapport", disabled=not reviewer, key="adopt_btn"):
        secs = ws.adopt_report_run(case_id, rid, reviewer)
        st.success(f"Beoordeling gestart voor {len(secs)} onderdelen.")
        rerun()


with tab_rev:
    if not case_id:
        st.info("Lees eerst data in.")
    else:
        with st.expander("Startpunt: een volledig rapport", expanded=not ws.sessions(case_id)):
            adopt_picker(case_id)
        present = ws.sessions(case_id)
        open_by = {s: x.stats()["pending"] for s, x in present.items()}
        rsec = st.selectbox("Onderdeel", SECTIONS, key="rev_section",
                            format_func=lambda s: s + ("" if s not in open_by else
                                                       f"  ({open_by[s]} te beoordelen)" if open_by[s] else "  ✅"))
        session = ws.session(case_id, rsec)
        if session is None:
            st.info("Nog geen concept voor dit onderdeel (tab 2, of kies hierboven een volledig rapport).")
        else:
            index = ws.evidence_index(case_id)
            d = session.draft
            states = session.states()
            stats = session.stats()
            vcounts = {k: sum(1 for s in states.values() if (s.verification or {}).get("status") == k)
                       for k in STATUS_BADGE}
            st.subheader(f"{rsec} · {ws.case_label(case_id)}")
            st.caption(f"Concept {d.draft_id} · werkwijze {d.pipeline_id} · model {(d.model or {}).get('model', 'geen')} · {d.created_at}")
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

# ---------------------------------------------------------------- 4 export (UC6) + traceability check
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
        key = f"export_{case_id}"
        if c[0].button("Exporteren", type="primary", disabled=bool(blockers)):
            files = ws.export(case_id, required_sections=required)
            st.session_state[key] = {"files": {k: str(v) for k, v in files.items()}, "required": required, "final": True,
                                     "trace": ws.trace_check(case_id, files, required)}
        if c[1].button("Conceptversie (intern, met markering)"):
            files = ws.export(case_id, preview=True, required_sections=required)
            st.session_state[key] = {"files": {k: str(v) for k, v in files.items()}, "required": required, "final": False,
                                     "trace": ws.trace_check(case_id, files, required)}
        done = st.session_state.get(key)
        if done:
            tr = done["trace"]
            st.markdown("#### Herleidbaarheid")
            if tr["ok"]:
                st.success(f"Alle {tr['n_claims']} beweringen herleidbaar: {tr['n_evidence_ids']} bewijsstukken, "
                           f"{tr['n_source_records']} bronrecords (bestand, blad, cel of rij).")
            else:
                st.error(f"{len(tr['problems'])} problemen bij {tr['n_claims']} beweringen. Pas ze aan in tab 3 "
                         "(bron toevoegen of bewering verwijderen) en exporteer opnieuw.")
                for p in tr["problems"][:50]:
                    st.markdown(f"- {p}")
            if not done["final"]:
                st.caption("Dit is een conceptversie met markering; alleen een export na beoordeling is het rapport.")
            for kind, path in done["files"].items():
                if Path(path).is_file():
                    st.download_button(f"Download {Path(path).name}", Path(path).read_bytes(), file_name=Path(path).name,
                                       key=f"dl_{kind}")
            with st.expander("Rapport als tekst"):
                st.markdown(Path(done["files"]["markdown"]).read_text(encoding="utf-8"))

# ---------------------------------------------------------------- expert: 1b images
if expert:
    with tabs["1b · Afbeeldingen"]:
        if not case_id:
            st.info("Lees eerst data in.")
        else:
            st.subheader(f"Afbeeldingen · {ws.case_label(case_id)}")
            st.caption("Kaarten met gekleurde bebouwing of markeringen worden deterministisch gemeten (pixels per legendakleur); "
                       "bij sport- en speelkaarten wordt geteld welk deel van de woningen binnen de afstandszone ligt. "
                       "Infographics en symboolkaarten worden door een vision-model afgelezen; elke aflezing moet je bevestigen.")
            vision_models = {k: v for k, v in ws.models().items() if (v.get("capabilities") or {}).get("vision")}
            vm = st.selectbox("Vision-model voor infographics", ["(geen: alleen kaartmetingen)"] + list(vision_models))
            n_samples = st.slider("Aantal aflezingen per afbeelding (self-ensemble)", 1, 5, 3)
            if st.button("Afbeeldingen analyseren"):
                with st.spinner("Kaarten meten en afbeeldingen lezen… (lokale vision-modellen kunnen minuten nodig hebben)"):
                    try:
                        summ = ws.analyse_images(case_id, None if vm.startswith("(") else vm, n_samples)
                        st.success(f"{summ['records_added']} beeldgegevens gemaakt.")
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Mislukt: {exc}")
            summary_path = ws.images_dir(case_id) / "summary.json"
            if summary_path.exists():
                import json as _json
                summ = _json.loads(summary_path.read_text(encoding="utf-8"))
                st.dataframe([{"afbeelding": k, "soort": v["kind"], "status": v.get("status"),
                               "waarschuwingen": "; ".join(v.get("warnings") or [])} for k, v in summ["images"].items()],
                             width="stretch", hide_index=True)
                meas = ws.image_records(case_id, ("image_measurement",))
                for img_id in sorted({r.extra["image_id"] for r in meas}):
                    rs = [r for r in meas if r.extra["image_id"] == img_id]
                    with st.expander(f"Meting: {rs[0].extra.get('title')} ({len(rs)} gebieden)"):
                        ov = rs[0].extra.get("overlay")
                        if ov and Path(ov).exists():
                            st.image(ov, caption="Controlebeeld: geteld in legendakleur, buurtgrenzen in zwart", width="stretch")
                        rows = []
                        for r in rs:
                            if r.extra.get("measure_kind") == "coverage":
                                cv = r.extra["coverage"]
                                rows.append({"gebied": r.extra["area"],
                                             "woningen binnen de zone": "geen woningen" if cv["homes_pct"] is None else f"{cv['homes_pct']}%",
                                             "gebied binnen de zone": f"{cv['area_pct']}%"})
                                continue
                            vals = r.extra.get("shares") or r.extra.get("counts") or {}
                            rows.append({"gebied": r.extra["area"],
                                         **{k: (f"{round(v * 100)}%" if isinstance(v, float) else v) for k, v in vals.items()}})
                        st.dataframe(rows, width="stretch", hide_index=True)
                        for w in rs[0].extra.get("warnings") or []:
                            st.warning(w)
                conf = ws.confirmations(case_id)
                reads = ws.image_records(case_id, ("image_reading", "image_observation"))
                if reads:
                    st.markdown("#### Aflezingen door het vision-model")
                    if conf.verify_chain():
                        st.error("Het bevestigingslog is gewijzigd buiten de tool om.")
                    pending = [r for r in reads if conf.status(r.id)[0] == "unconfirmed"]
                    st.caption(f"{len(pending)} van {len(reads)} nog niet bevestigd.")
                    only_pending = st.checkbox("Toon alleen niet-bevestigde aflezingen", value=True)
                    shown_imgs = set()
                    for r in reads:
                        status, corrected, _ = conf.status(r.id)
                        if only_pending and status != "unconfirmed":
                            continue
                        src = next((x for x in ws.store().all() if x.id == r.extra.get("image_record_id")), None)
                        if src is not None and src.id not in shown_imgs and src.extra.get("absolute_path"):
                            shown_imgs.add(src.id)
                            st.image(src.extra["absolute_path"], caption=src.source_file, width="stretch")
                        with st.container(border=True):
                            if r.extra.get("record_kind") == "image_reading":
                                agree = r.extra.get("agreement")
                                st.markdown(f"**{r.extra.get('chart')} › {r.extra.get('area')} › {r.extra.get('label')}**: "
                                            f"{r.extra.get('as_printed')} (waarde {r.value})"
                                            + (f" → gecorrigeerd naar **{corrected}**" if status == "corrected" else ""))
                                st.caption(f"status: {status} · overeenstemming tussen aflezingen: "
                                           f"{'?' if agree is None else f'{round(agree * 100)}%'} · gezien: {r.extra.get('values_seen')}")
                                for chk in r.extra.get("checks") or []:
                                    st.markdown(f"🟠 {chk}")
                            else:
                                st.markdown(f"**{r.extra.get('title')}** (modelbeschrijving): {r.text}")
                                st.caption(f"status: {status}")
                            b = st.columns([1, 2, 1, 1])
                            if b[0].button("Klopt", key=f"imgok_{r.id}", disabled=not reviewer):
                                conf.confirm(r.id, reviewer)
                                rerun()
                            if r.extra.get("record_kind") == "image_reading":
                                newv = b[1].number_input("Juiste waarde", value=float(r.value or 0), key=f"imgval_{r.id}")
                                if b[2].button("Corrigeer", key=f"imgfix_{r.id}", disabled=not reviewer):
                                    conf.correct(r.id, newv, reviewer)
                                    rerun()
                            if b[3].button("Afwijzen", key=f"imgno_{r.id}", disabled=not reviewer):
                                conf.reject(r.id, reviewer)
                                rerun()

# ---------------------------------------------------------------- expert: 5 full report per model (SQ3)
if expert:
    with tabs["5 · Rapport per model"]:
        if not case_id:
            st.info("Lees eerst data in.")
        else:
            st.subheader(f"Volledig rapport per model · {ws.case_label(case_id)}")
            st.caption("Onderzoek (SQ3): elk model schrijft alle onderdelen. Volledige rapporten voor de beoordeling maak "
                       "je in tab 2 (op de achtergrond); dit tabblad wacht tot de rapporten klaar zijn.")
            models = ws.models()
            chosen = ws.selected_models()
            st.markdown("**De drie gekozen modellen** (`config/models.toml` › `[selection]`)")
            st.dataframe([{"model": m, "naam": models[m].get("model", ""), "geheugen (GB)": models[m].get("ram_gb_estimate"),
                           "status": "klaar" if not ws.model_problems(m) else "; ".join(ws.model_problems(m))}
                          for m in chosen], hide_index=True, width="stretch")

            st.markdown("#### Rapporten maken (alle gekozen modellen na elkaar)")
            c1, c2, c3 = st.columns([3, 1, 1])
            pick = c1.multiselect("Modellen", chosen + [m for m in models if m not in chosen], default=chosen,
                                  key="rep_models")
            pipe = c2.selectbox("Werkwijze", ["staged", "staged_checked", "naive"], key="rep_pipe",
                                format_func=lambda x: PIPELINE_CHOICES[x][0])
            with_template = c3.checkbox("+ template (zonder model)", value=True, key="rep_tpl")
            want_pdf = st.checkbox("Ook PDF maken (LibreOffice nodig)", key="rep_pdf")
            if st.button("Rapporten maken", type="primary", disabled=not (pick or with_template) or bool(jobs.active_job(ws))):
                todo = (["template"] if with_template else []) + pick
                for m in todo:
                    with st.status(f"{m} …", expanded=False) as box:
                        try:
                            if m == "template":
                                run = ws.report_run(case_id, m, pipeline="template", pdf=want_pdf, progress=box.write)
                            else:
                                from reports.runs import generate_report_run
                                run = generate_report_run(ws, case_id, m, pipeline=pipe,
                                                          prompt_version=PIPELINE_CHOICES[pipe][1], pdf=want_pdf,
                                                          progress=box.write)
                            t = run["totals"]
                            box.update(label=f"{m}: {t['n_claims']} beweringen, onderbouwd {_pct(t['grounded_rate'])}"
                                             + (f", mislukt: {', '.join(t['failed_sections'])}" if t["failed_sections"] else ""),
                                       state="complete")
                        except Exception as exc:  # noqa: BLE001
                            box.update(label=f"{m}: mislukt: {exc}", state="error")

            runs = ws.report_runs(case_id)
            st.markdown("#### Gemaakte rapporten")
            if not runs:
                st.info("Nog geen rapporten.")
            else:
                st.dataframe([{"run": r["run_id"], "model": r.get("model_id") or "template", "pijplijn": r["pipeline"],
                               "beweringen": r["totals"]["n_claims"], "onderbouwd": _pct(r["totals"]["grounded_rate"]),
                               "niet onderbouwd": _pct(r["totals"]["unsupported_rate"]), "tijd (s)": r["totals"]["wall_s"],
                               "dekking origineel": _pct((r.get("evaluation") or {}).get("content", {}).get("coverage"))
                               if r.get("evaluation") else "–"} for r in reversed(runs)],
                             hide_index=True, width="stretch")
                run_id = st.selectbox("Rapport", [r["run_id"] for r in reversed(runs)], key="rep_run")
                run = next(r for r in runs if r["run_id"] == run_id)
                rdir = ws.report_run_dir(case_id, run_id)
                cols = st.columns(4)
                for i, (kind, name) in enumerate(run.get("files", {}).items()):
                    f = rdir / name
                    if f.is_file():
                        cols[i].download_button(f"Download {kind.upper()}", f.read_bytes(), file_name=name, key=f"rep_dl_{kind}")

            st.markdown("#### Vergelijken met het originele rapport")
            ref = ws.reference_report(case_id)
            st.caption("Alleen evaluatie: het originele rapport is nooit invoer voor een concept. De uitkomst citeert het "
                       "origineel en blijft in de werkmap.")
            ref_path = st.text_input("Origineel rapport (PDF)", str(ref) if ref else "", key="rep_ref",
                                     help="Gevonden bij het inlezen als het in de casusmap staat; anders hier het pad invullen.")
            use_judge = st.checkbox("Laat een lokaal model de overeenkomst beoordelen (nauwkeuriger, duurt langer)",
                                    key="rep_judge")
            judge = st.selectbox("Beoordelaar", [m for m in chosen if models[m].get("location", "local") == "local"],
                                 key="rep_judge_model", disabled=not use_judge) if use_judge else None
            latest = ws.latest_report_runs(case_id)
            if st.button("Vergelijk de laatste run per model", disabled=not (latest and ref_path)):
                with st.spinner("Vergelijken…"):
                    try:
                        out = ws.compare_report_runs(case_id, list(latest.values()), reference_pdf=ref_path,
                                                     judge_model=judge)
                        st.session_state["rep_cmp"] = out["markdown"]
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Mislukt: {exc}")
            cmp_md = ws.p("reports", case_id, "comparison", "comparison.md")
            if cmp_md.is_file():
                st.markdown(cmp_md.read_text(encoding="utf-8"))
            if runs:
                ev_dir = ws.report_run_dir(case_id, st.session_state.get("rep_run") or runs[-1]["run_id"]) / "evaluation"
                if (ev_dir / "evaluation.md").is_file():
                    with st.expander("Details van het gekozen rapport (per hoofdstuk, afwijkende getallen)"):
                        st.markdown((ev_dir / "evaluation.md").read_text(encoding="utf-8"))
                        st.download_button("Download alignment.csv (voor kalibratie: vul human_label in)",
                                           (ev_dir / "alignment.csv").read_bytes(), file_name="alignment.csv",
                                           key="rep_dl_align")

# ---------------------------------------------------------------- expert: 6 experiments (SQ3)
if expert:
    with tabs["6 · Experimenten"]:
        st.subheader("Experimenten")
        st.caption("Lange runs liever via de command line: `python -m experiments.cli run E1 --workspace workspace`.")
        if ws.has_data():
            from experiments.runner import summarise_runs  # noqa: E402
            rows = summarise_runs(ws)
            if rows:
                st.dataframe([{"run": r["run_id"], "exp": r["experiment"], "eenheid": r["unit"], "tag": r["tag"],
                               "pijplijn": r["pipeline"], "selectie": r["selection"], "model": r["model"],
                               "alleen CPU": r["cpu_only"], "n": r["summary"]["n"],
                               "mislukt": r["summary"]["failed"],
                               "onderbouwd": r["summary"].get("grounded_rate", {}).get("mean"),
                               "niet onderbouwd": r["summary"].get("unsupported_rate", {}).get("mean"),
                               "dekking (bewijs)": r["summary"].get("coverage_salient", {}).get("mean"),
                               "dekking origineel (onderbouwbaar)":
                                   r["summary"].get("coverage_of_supportable", {}).get("mean"),
                               "sec per eenheid": r["summary"].get("wall_s", {}).get("mean"),
                               "modelgeheugen (GB)": round(r["model_peak_mb"] / 1024, 1) if r["model_peak_mb"] else None}
                              for r in rows],
                             width="stretch", hide_index=True)
            else:
                st.info("Nog geen runs.")
