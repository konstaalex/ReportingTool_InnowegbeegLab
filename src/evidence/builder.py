"""Build Evidence items for one Buurtscan/Wijkscan case. Pure Python, no model.

    packs = build_case_evidence(store, "passewaaij-tiel")
    packs["Sporten"]      # EvidencePack for the Sporten section
    packs["Algemeen"]     # context: respondents, demografie, general quotes
    packs["Aanleiding"]   # project context from researcher notes (may be empty)
    packs["Conclusies"]   # cross-theme: per-theme headline figures, rule outcomes, recurring factors

Every number in a statement is computed here and also stored in `data`, so
the verifier can check model claims against it. Percentages are of the
respondents who answered that question (n is always stated).
"""
from __future__ import annotations

import re
import statistics
from collections import OrderedDict, defaultdict
from typing import Iterable, Optional

from ingestion.config import config_fingerprint, load_config
from ingestion.models import THEMES, SourceRecord
from ingestion.store import SourceStore

from .models import (AANLEIDING, CONCLUSIES, GENERAL, GENERAL_CODE, NOTE_CODE, PERSPECTIVE_CODES, SECTION_CODES,
                     THEME_CODES, Evidence, EvidencePack)
SCALE_NL = {1: "heel slecht", 2: "slecht", 3: "neutraal", 4: "goed", 5: "uitstekend"}
LIKERT_POSITIVE = ("belangrijk", "heel belangrijk")


# ----------------------------------------------------------------- formatting

def nl_num(x: float, decimals: int = 1) -> str:
    if decimals == 0:
        return f"{int(round(x)):,}".replace(",", ".")
    return f"{x:.{decimals}f}".replace(".", ",")


def pct(k: int, n: int) -> int:
    return int(round(100 * k / n)) if n else 0


def _clean_option(o) -> str:
    s = str(o)
    return s.split(" - ", 1)[1] if " - " in s else s


class _Ids:
    """Hands out readable ids per (perspective, section) in creation order."""
    def __init__(self) -> None:
        self.counters: dict[str, int] = defaultdict(int)

    def next(self, perspective: str, section: str, code: Optional[str] = None) -> str:
        sec = SECTION_CODES.get(section, GENERAL_CODE)
        key = f"{code or PERSPECTIVE_CODES[perspective]}-{sec}"
        self.counters[key] += 1
        return f"{key}-{self.counters[key]:02d}"


# ----------------------------------------------------------------- expert

def _stmt_text(r: SourceRecord) -> str:
    """Buurtscan records carry the statement in .text; Oss records carry the
    remark in .text and the statement in extra['statement']."""
    return (r.extra.get("statement") if "statement" in r.extra else r.text) or f"stelling {r.extra.get('statement_number')}"


def _score_txt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else nl_num(v)


def expert_evidence(records: list[SourceRecord], case_id: str, ids: _Ids) -> dict[str, list[Evidence]]:
    out: dict[str, list[Evidence]] = defaultdict(list)
    by_theme: dict[str, list[SourceRecord]] = defaultdict(list)
    for r in records:
        if r.extra.get("record_kind") == "expert_statement" and r.theme and r.value is not None:
            by_theme[r.theme].append(r)
    for theme in THEMES:
        rs = sorted(by_theme.get(theme, []), key=lambda r: (r.neighbourhood or "", r.extra["statement_number"]))
        if not rs:
            continue
        buurten = sorted({r.neighbourhood for r in rs if r.neighbourhood})
        multi = len(buurten) > 1

        def avg_evidence(group: list[SourceRecord], where: str) -> Evidence:
            scores = [r.value for r in group]
            mean = statistics.mean(scores)
            low = [r for r in group if r.value <= 2]
            high = [r for r in group if r.value >= 4]
            return Evidence(
                evidence_id=ids.next("expert", theme), case_id=case_id, themes=[theme], perspective="expert",
                kind="theme_average",
                statement=(f"De experts beoordelen het thema {theme.lower()}{where} gemiddeld met een {nl_num(mean)} "
                           f"op een schaal van 1 tot 5 ({len(group)} stellingen; {len(low)} keer 2 of lager, "
                           f"{len(high)} keer 4 of hoger)."),
                data={"mean": round(mean, 2), "n_statements": len(group), "min": min(scores), "max": max(scores),
                      "n_low": len(low), "n_high": len(high), "neighbourhood": where.replace(" in ", "") or None},
                source_record_ids=[r.id for r in group], salience=3.0 if not where or not multi else 2.0,
            )

        out[theme].append(avg_evidence(rs, f" in {len(buurten)} buurten" if multi else ""))
        if multi:
            for b in buurten:
                out[theme].append(avg_evidence([r for r in rs if r.neighbourhood == b], f" in {b}"))
        for r in rs:
            score = r.value
            remark = r.extra.get("remark")
            evals = r.extra.get("evaluator_scores") or {}
            where = f"{r.neighbourhood}, " if multi else ""
            stmt = (f"{where}stelling {r.extra['statement_number']} ({r.subtheme}): \"{_stmt_text(r)}\" krijgt van de "
                    f"experts {'gemiddeld ' if evals else ''}score {_score_txt(score)} "
                    f"({SCALE_NL.get(int(round(score)), '?')}).")
            if evals and len(evals) > 1 and max(evals.values()) - min(evals.values()) >= 2:
                stmt += f" De {len(evals)} experts verschillen: scores {int(min(evals.values()))} tot {int(max(evals.values()))}."
            if remark:
                stmt += f" Opmerking van de experts: \"{remark}\"."
            stmt = stmt[0].upper() + stmt[1:]
            out[theme].append(Evidence(
                evidence_id=ids.next("expert", theme), case_id=case_id, themes=[theme], perspective="expert",
                kind="expert_statement", statement=stmt,
                data={"score": score, "statement_number": r.extra["statement_number"], "indicator": r.subtheme,
                      "neighbourhood": r.neighbourhood, "remark": remark, "cells": r.extra.get("cells"),
                      "evaluator_scores": evals or None},
                source_record_ids=[r.id], salience=abs(score - 3) + (0.5 if remark else 0.0),
                # expert remarks are professional observations, not resident data: no privacy flag
            ))
    return out


def _norm_text(t: str) -> str:
    return re.sub(r"\W+", " ", (t or "").lower()).strip()


def remark_evidence(records: list[SourceRecord], case_id: str, ids: _Ids) -> dict[str, list[Evidence]]:
    """Expert remarks not already attached to a scored statement: section-level
    remarks and the 'Opmerkingen' sheet (deduplicated against statement remarks)."""
    out: dict[str, list[Evidence]] = defaultdict(list)
    seen = {_norm_text(r.extra.get("remark")) for r in records
            if r.extra.get("record_kind") == "expert_statement" and r.extra.get("remark")}
    for r in records:
        if r.extra.get("record_kind") not in ("expert_section_remark", "expert_remark_sheet") or not r.theme:
            continue
        key = _norm_text(r.text)
        if not key or key in seen or any(key in s_ or s_ in key for s_ in seen if len(s_) > 12):
            continue
        seen.add(key)
        where = f" ({r.neighbourhood})" if r.neighbourhood else ""
        out[r.theme].append(Evidence(
            evidence_id=ids.next("expert", r.theme), case_id=case_id, themes=[r.theme], perspective="expert",
            kind="expert_remark", statement=f"Opmerking van de experts{where} bij {r.theme.lower()}: \"{r.text}\".",
            data={"neighbourhood": r.neighbourhood}, source_record_ids=[r.id], salience=1.0,
        ))
    return out


# ----------------------------------------------------------------- survey

def _group_answers(records: Iterable[SourceRecord]) -> "OrderedDict[str, list[SourceRecord]]":
    groups: "OrderedDict[str, list[SourceRecord]]" = OrderedDict()
    for r in sorted(records, key=lambda r: (r.extra.get("block_id", ""), r.extra.get("respondent", 0))):
        if r.extra.get("record_kind") == "survey_answer" and r.extra.get("question_id"):
            groups.setdefault(r.extra["question_id"], []).append(r)
    return groups


def _totals_record(records: list[SourceRecord], block_id: str) -> list[str]:
    return [r.id for r in records if r.extra.get("record_kind") == "survey_column_total" and r.extra.get("block_id") == block_id]


def _quotes(rs: list[SourceRecord], case_id: str, section: str, ids: _Ids, question_label: str,
            exclude_labels: tuple[str, ...] = ()) -> list[Evidence]:
    grouped: "OrderedDict[str, list[SourceRecord]]" = OrderedDict()
    for r in rs:
        for label, text in zip(r.extra.get("open_labels", []), r.extra.get("open_text", [])):
            if label in exclude_labels:
                continue
            key = " ".join(text.split()).strip().rstrip(".")
            grouped.setdefault(key, []).append(r)
    out = []
    for text, rs_q in grouped.items():
        k = len(rs_q)
        who = "1 bewoner" if k == 1 else f"{k} bewoners"
        out.append(Evidence(
            evidence_id=ids.next("bewoners", section), case_id=case_id,
            themes=[section] if section in THEME_CODES else [], perspective="bewoners", kind="quote",
            statement=f"Open antwoord bij '{question_label}' ({who}): \"{text}\".",
            data={"count": k, "respondents": [r.extra["respondent"] for r in rs_q], "question": question_label},
            source_record_ids=[r.id for r in rs_q], salience=0.4 + 0.3 * k, contains_free_text=True,
        ))
    return out


def survey_evidence(records: list[SourceRecord], case_id: str, ids: _Ids, mapping: dict,
                    n_respondents: int) -> dict[str, list[Evidence]]:
    out: dict[str, list[Evidence]] = defaultdict(list)
    qcfg = {q["id"]: q for q in mapping["question"]}
    groups = _group_answers(records)

    def add(sec: str, ev: Evidence) -> None:
        ev.data.setdefault("question_id", qid)
        out[sec].append(ev)

    for qid, rs in groups.items():
        q = qcfg[qid]
        kind = rs[0].extra["question_kind"]
        block = rs[0].extra["block_id"]
        totals = _totals_record(records, block)
        section = q["theme"] if q["theme"] and q["theme"] != "*per_option" else GENERAL
        label = q["label"]
        closed = [r for r in rs if r.extra["selected"]]
        n = len(closed)

        if qid == "woonbuurt":
            counts = OrderedDict()
            for r in closed:
                for s in r.extra["selected"]:
                    counts[str(s)] = counts.get(str(s), 0) + 1
            parts = ", ".join(f"{k}: {v} ({pct(v, n)}%)" for k, v in counts.items())
            add(GENERAL, Evidence(
                evidence_id=ids.next("bewoners", GENERAL), case_id=case_id, themes=[], perspective="bewoners",
                kind="respondents",
                statement=(f"De bewonersvragenlijst telt {n_respondents} respondenten. Woonbuurt "
                           f"(n = {n}): {parts}."),
                data={"n_respondents": n_respondents, "n": n, "counts": dict(counts),
                      "pct": {k: pct(v, n) for k, v in counts.items()}},
                source_record_ids=[r.id for r in closed] + totals, salience=3.0,
            ))
            continue

        if kind == "rating" and n:
            values = [int(s) for r in closed for s in r.extra["selected"] if isinstance(s, (int, float))]
            mean = statistics.mean(values)
            dist = {i: values.count(i) for i in range(1, 6)}
            add(section, Evidence(
                evidence_id=ids.next("bewoners", section), case_id=case_id, themes=[section], perspective="bewoners",
                kind="rating",
                statement=(f"Bewoners geven het {label} gemiddeld een {nl_num(mean)} op een schaal van 1 tot 5 "
                           f"(n = {n}); {pct(dist[1] + dist[2], n)}% geeft een 1 of 2, "
                           f"{pct(dist[4] + dist[5], n)}% een 4 of 5."),
                data={"mean": round(mean, 2), "n": n, "distribution": dist,
                      "pct_low": pct(dist[1] + dist[2], n), "pct_high": pct(dist[4] + dist[5], n)},
                source_record_ids=[r.id for r in closed] + totals, salience=3.0,
            ))

        elif kind == "likert" and n:
            counts = OrderedDict()
            for r in closed:
                for s in r.extra["selected"]:
                    lvl = _clean_option(s)
                    counts[lvl] = counts.get(lvl, 0) + 1
            k_pos = sum(v for lvl, v in counts.items() if lvl.lower() in LIKERT_POSITIVE)
            p = pct(k_pos, n)
            add(section, Evidence(
                evidence_id=ids.next("bewoners", section), case_id=case_id,
                themes=[section] if section != GENERAL else [], perspective="bewoners", kind="likert",
                statement=(f"{p}% van de bewoners ({k_pos} van {n}) vindt {label} belangrijk of heel belangrijk."),
                data={"n": n, "k_positive": k_pos, "pct_positive": p, "counts": dict(counts),
                      "pct": {lvl: pct(v, n) for lvl, v in counts.items()}},
                source_record_ids=[r.id for r in closed] + totals, salience=1.0 + abs(p - 50) / 50,
            ))

        elif kind in ("choice", "multi") and q["theme"] != "*per_option" and n:
            counts = OrderedDict()
            for r in closed:
                for s in r.extra["selected"]:
                    counts[str(s)] = counts.get(str(s), 0) + 1
            ranked = sorted(counts.items(), key=lambda kv: -kv[1])
            if kind == "choice":
                parts = "; ".join(f"'{o}' {pct(v, n)}% ({v})" for o, v in ranked)
                stmt = f"Vraag over {label} (n = {n}): {parts}."
            else:
                parts = "; ".join(f"'{o}' {pct(v, n)}% ({v})" for o, v in ranked)
                stmt = f"Vraag over {label}, meerdere antwoorden mogelijk (n = {n}): {parts}."
            add(section, Evidence(
                evidence_id=ids.next("bewoners", section), case_id=case_id,
                themes=[section] if section != GENERAL else [], perspective="bewoners", kind=kind,
                statement=stmt,
                data={"n": n, "counts": dict(counts), "pct": {o: pct(v, n) for o, v in counts.items()},
                      "question": rs[0].extra["question_key"]},
                source_record_ids=[r.id for r in closed] + totals,
                salience=1.0 + (ranked[0][1] / n if ranked else 0),
            ))

        elif q["theme"] == "*per_option" and n:
            verb = q.get("verb", "kiest")
            per_option: dict[str, list[SourceRecord]] = OrderedDict()
            for r in closed:
                for s in r.extra["selected"]:
                    per_option.setdefault(str(s), []).append(r)
            for option, rs_o in sorted(per_option.items(), key=lambda kv: -len(kv[1])):
                theme = rs_o[0].extra.get("option_themes", {}).get(option)
                sec = theme if theme else GENERAL
                k = len(rs_o)
                add(sec, Evidence(
                    evidence_id=ids.next("bewoners", sec), case_id=case_id, themes=[sec] if theme else [],
                    perspective="bewoners", kind="option_share",
                    statement=(f"{pct(k, n)}% van de bewoners ({k} van {n}) {verb} '{option}' "
                               f"({label}, meerdere antwoorden mogelijk)."),
                    data={"n": n, "k": k, "pct": pct(k, n), "option": option, "question_id": qid},
                    source_record_ids=[r.id for r in rs_o] + totals, salience=0.5 + 2 * k / n,
                ))

        # open text attached to this question (Omdat:/Toelichting:/Anders/open-ended)
        for ev in _quotes(rs, case_id, section, ids, label):
            add(section, ev)
    return out


# ----------------------------------------------------------------- data (demografie)

def demografie_evidence(records: list[SourceRecord], case_id: str, ids: _Ids) -> tuple[dict[str, list[Evidence]], list[str]]:
    out: dict[str, list[Evidence]] = defaultdict(list)
    unavailable: list[str] = []
    for r in records:
        kind = r.extra.get("record_kind")
        if kind == "demografie_chart_image":
            unavailable.append(f"{r.source_file} › {r.locator}: {r.subtheme} — {r.text} (alleen als afbeelding; waarden niet ingelezen)")
            continue
        if kind == "demografie_inhabitants":
            out[GENERAL].append(Evidence(
                evidence_id=ids.next("data", GENERAL), case_id=case_id, themes=[], perspective="data",
                kind="demographic", statement=f"{r.extra['area']} telt {nl_num(r.value, 0)} inwoners ({r.extra['year']}).",
                data={"inhabitants": r.value, "area": r.extra["area"], "year": r.extra["year"]},
                source_record_ids=[r.id], salience=2.0,
            ))
            continue
        if kind != "demografie_indicator":
            continue
        raw = r.extra["raw"]
        unit = r.extra.get("unit")
        parts = ", ".join(f"{area} {raw[area]}" for area in r.extra["areas"] if area in raw)
        scale = "" if unit == "%" else " (index 0-100)"
        themes = [t for t in r.extra.get("themes", []) if t in THEME_CODES]
        values = [v for v in r.extra["values"].values() if v is not None]
        spread = (max(values) - min(values)) if values else 0
        sections = themes or [GENERAL]
        # one Evidence per section it belongs to, sharing the same source cell
        for sec in sections:
            out[sec].append(Evidence(
                evidence_id=ids.next("data", sec), case_id=case_id, themes=themes, perspective="data",
                kind="demographic", statement=f"{r.subtheme}{scale}: {parts}.",
                data={"indicator": r.subtheme, "values": r.extra["values"], "unit": unit,
                      "rivm_rules": r.extra.get("rules", [])},
                source_record_ids=[r.id], salience=1.0 + spread / 10,
            ))
    return out, unavailable


# ----------------------------------------------------------------- Oss: survey totals per group

def _short_group(g: str) -> str:
    return g.strip()


def tally_evidence(records: list[SourceRecord], case_id: str, ids: _Ids, mapping: dict) -> dict[str, list[Evidence]]:
    """'Resultaten vragenlijst' totals: same questions as the respondent-level
    survey, one record per respondent group. Every statement names the group
    and its n, because groups differ (65+ vs jongvolwassenen)."""
    from ingestion.parsers.buurtscan_survey_parser import rating_value
    out: dict[str, list[Evidence]] = defaultdict(list)
    qcfg = {q["id"]: q for q in mapping["question"]}
    for r in records:
        if r.extra.get("record_kind") != "survey_tally" or not r.extra.get("question_id"):
            continue
        q = qcfg[r.extra["question_id"]]
        group = _short_group(r.extra["group"])

        def add(sec: str, ev: Evidence, _qid: str = q["id"]) -> None:
            ev.data.setdefault("question_id", _qid)
            out[sec].append(ev)

        counts: dict[str, int] = dict(r.extra["counts"])
        open_opts = set(r.extra.get("open_options", []))
        closed = {k: v for k, v in counts.items() if k not in open_opts}
        kind = r.extra["question_kind"]
        section = q["theme"] if q["theme"] and q["theme"] != "*per_option" else GENERAL
        label = q["label"]
        group_n = r.extra.get("group_n")
        base = dict(source_record_ids=[r.id], case_id=case_id, perspective="bewoners")

        if q["id"] == "woonbuurt":
            n = sum(closed.values())
            parts = ", ".join(f"{k}: {v} ({pct(v, n)}%)" for k, v in closed.items())
            add(GENERAL, Evidence(evidence_id=ids.next("bewoners", GENERAL), themes=[], kind="respondents",
                                         statement=f"Groep '{group}': {n} respondenten. Woonbuurt: {parts}.",
                                         data={"group": group, "n_respondents": n, "counts": closed,
                                               "pct": {k: pct(v, n) for k, v in closed.items()}},
                                         salience=3.0, **base))
            continue
        if kind == "rating":
            vals = {rating_value(k): v for k, v in closed.items() if rating_value(k) is not None}
            n = sum(vals.values())
            if not n:
                continue
            mean = sum(k * v for k, v in vals.items()) / n
            low, high = vals.get(1, 0) + vals.get(2, 0), vals.get(4, 0) + vals.get(5, 0)
            add(section, Evidence(
                evidence_id=ids.next("bewoners", section), themes=[section], kind="rating",
                statement=(f"Groep '{group}' geeft het {label} gemiddeld een {nl_num(mean)} op een schaal van 1 tot 5 "
                           f"(n = {n}); {pct(low, n)}% geeft een 1 of 2, {pct(high, n)}% een 4 of 5."),
                data={"group": group, "mean": round(mean, 2), "n": n, "distribution": vals,
                      "pct_low": pct(low, n), "pct_high": pct(high, n)},
                salience=3.0, **base))
        elif kind == "likert":
            n = sum(closed.values())
            if not n:
                continue
            k_pos = sum(v for k, v in closed.items() if _clean_option(k).lower() in LIKERT_POSITIVE)
            add(section, Evidence(
                evidence_id=ids.next("bewoners", section), themes=[section] if section != GENERAL else [],
                kind="likert",
                statement=f"In groep '{group}' vindt {pct(k_pos, n)}% ({k_pos} van {n}) {label} belangrijk of heel belangrijk.",
                data={"group": group, "n": n, "k_positive": k_pos, "pct_positive": pct(k_pos, n),
                      "counts": {_clean_option(k): v for k, v in closed.items()}},
                salience=1.0 + abs(pct(k_pos, n) - 50) / 50, **base))
        elif q["theme"] == "*per_option":
            n = group_n or 0
            if not n:
                continue
            for option, k in sorted(closed.items(), key=lambda kv: -kv[1]):
                theme = r.extra.get("option_themes", {}).get(option)
                sec = theme or GENERAL
                add(sec, Evidence(
                    evidence_id=ids.next("bewoners", sec), themes=[sec] if theme else [], kind="option_share",
                    statement=(f"In groep '{group}' {q.get('verb', 'kiest')} {pct(k, n)}% ({k} van de {n} respondenten) "
                               f"'{option}' ({label}, meerdere antwoorden mogelijk)."),
                    data={"group": group, "n": n, "k": k, "pct": pct(k, n), "option": option},
                    salience=0.5 + 2 * k / n, **base))
        elif kind in ("choice", "multi"):
            n = sum(closed.values()) if kind == "choice" else (group_n or sum(closed.values()))
            if not n:
                continue
            ranked = sorted(closed.items(), key=lambda kv: -kv[1])
            parts = "; ".join(f"'{o}' {pct(v, n)}% ({v})" for o, v in ranked)
            extra_txt = ", meerdere antwoorden mogelijk, percentage van alle respondenten in de groep" if kind == "multi" else ""
            add(section, Evidence(
                evidence_id=ids.next("bewoners", section), themes=[section] if section != GENERAL else [], kind=kind,
                statement=f"Groep '{group}', vraag over {label}{extra_txt} (n = {n}): {parts}.",
                data={"group": group, "n": n, "counts": closed, "pct": {o: pct(v, n) for o, v in closed.items()}},
                salience=1.0 + (ranked[0][1] / n if ranked else 0), **base))
    return out


def coded_evidence(records: list[SourceRecord], case_id: str, ids: _Ids) -> dict[str, list[Evidence]]:
    """Open answers coded by researchers into theme x Behoefte/Belemmering/
    Positieve bevinding with a count. The group is kept: 'Stakeholders' are
    professionals, not residents (the verifier checks that attribution)."""
    out: dict[str, list[Evidence]] = defaultdict(list)
    for r in records:
        if r.extra.get("record_kind") != "coded_response" or not r.theme or not r.text:
            continue
        k = int(r.value) if isinstance(r.value, (int, float)) else 1
        group = r.extra.get("group") or "onbekende groep"
        rtype = r.extra.get("response_type") or r.subtheme
        out[r.theme].append(Evidence(
            evidence_id=ids.next("bewoners", r.theme), case_id=case_id, themes=[r.theme], perspective="bewoners",
            kind="coded",
            statement=f"{rtype} genoemd door '{group}' (gecodeerd open antwoord): \"{r.text}\" ({k}×).",
            data={"group": group, "count": k, "response_type": rtype,
                  "is_stakeholder": "stakeholder" in group.lower()},
            source_record_ids=[r.id], salience=0.5 + 0.4 * k, contains_free_text=True,
        ))
    return out


# ----------------------------------------------------------------- images

def _pct_or_none(v) -> Optional[int]:
    return None if v is None else int(round(100 * v))


def image_evidence(records: list[SourceRecord], case_id: str, ids: _Ids,
                   confirmations=None) -> tuple[dict[str, list[Evidence]], list[str]]:
    """Evidence from image records. Deterministic map measurements are stated
    with their method caveat; model readings carry their confirmation status
    (unconfirmed / confirmed / corrected) and rejected readings are dropped."""
    demo_map = load_config("demografie_mapping.toml").get("indicator", {})
    out: dict[str, list[Evidence]] = defaultdict(list)
    notes: list[str] = []
    for r in records:
        kind = r.extra.get("record_kind")
        if kind == "image_measurement":
            x = r.extra
            area = x["area"]
            whole_map = area == "onderzoeksgebied" and x["measure_kind"] == "counts" and not x.get("study_area_only", False)
            where = "op de hele kaart (ook buiten het onderzoeksgebied)" if whole_map else (
                "in het onderzoeksgebied" if area == "onderzoeksgebied" else f"in {area}")
            rules = ", ".join(x.get("rules", []))
            # coloured buildings and homes inside a distance zone are the rule's own outcome per home;
            # a count of locations is not
            tests_rule = x["measure_kind"] in ("shares", "coverage")
            if rules and tests_rule:
                rule_txt = f" Toetst RIVM-vuistregel {rules}."
            elif rules:
                rule_txt = f" Hoort bij RIVM-vuistregel {rules}, maar een aantal locaties toetst de afstandsregel niet."
            else:
                rule_txt = ""
            if x["measure_kind"] == "coverage":
                cov = x["coverage"]
                hp, ap = cov.get("homes_pct"), cov.get("area_pct")
                ref = x.get("reference_map") or "referentiekaart"
                if hp is None:
                    stmt = (f"{x['title']} ({x['measure']}): {where} staan op de {ref}kaart geen woningen; "
                            f"{ap}% van het gebied ligt binnen de zone (aandeel kaartoppervlak, indicatief).{rule_txt}")
                else:
                    stmt = (f"{x['title']} ({x['measure']}): {where} ligt {hp}% van de woningen binnen de zone en "
                            f"{100 - hp}% erbuiten (aandeel bebouwd oppervlak van de woningen op de {ref}kaart; "
                            f"indicatief).{rule_txt}")
                data = {"area": area, "homes_pct": hp, "homes_outside_pct": None if hp is None else 100 - hp,
                        "area_pct": ap, "zone": cov.get("zone"), "rules": x.get("rules", []), "tests_rule": True,
                        "method": x["method"], "warnings": x.get("warnings", [])}
                sal = 2.5 if area == "onderzoeksgebied" else 1.0 + (100 - (hp if hp is not None else ap or 0)) / 50
            elif x["measure_kind"] == "shares":
                shares = {k: _pct_or_none(v) for k, v in x["shares"].items()}
                if all(v is None for v in shares.values()):
                    stmt = f"{x['title']}: {where} staat geen gekleurde bebouwing (geen gegevens voor {x['measure']})."
                else:
                    parts = ", ".join(f"{v}% '{k}'" for k, v in shares.items() if v)
                    stmt = (f"{x['title']} ({x['measure']}): {where} valt {parts} "
                            f"(aandeel van het gekleurde kaartoppervlak, geteld uit de legendakleuren; indicatief).{rule_txt}")
                data = {"area": area, "shares_pct": shares, "rules": x.get("rules", []), "tests_rule": True,
                        "method": x["method"], "warnings": x.get("warnings", [])}
                minority = min((v for v in shares.values() if v), default=0)
                sal = 2.5 if area == "onderzoeksgebied" else 1.0 + (minority or 0) / 50
            else:
                counts = x["counts"]
                parts = ", ".join(f"{v}× '{k}'" for k, v in counts.items() if v) or "geen locaties"
                stmt = (f"{x['title']}: {where} staan {parts} "
                        f"(aantal gekleurde markeringen per legendatype, deterministisch geteld; ondergrens).{rule_txt}")
                data = {"area": area, "counts": counts, "rules": x.get("rules", []), "tests_rule": False,
                        "whole_map": whole_map, "method": x["method"], "warnings": x.get("warnings", [])}
                sal = 1.5 if area == "onderzoeksgebied" else 1.0
            themes = [t for t in x.get("themes", []) if t in THEME_CODES]
            for sec in themes or [GENERAL]:
                out[sec].append(Evidence(evidence_id=ids.next("data", sec), case_id=case_id, themes=themes,
                                         perspective="data", kind="map_measurement", statement=stmt, data=data,
                                         source_record_ids=[r.id, x.get("image_record_id")] if x.get("image_record_id") else [r.id],
                                         salience=sal, method="deterministic (image pixels)"))
        elif kind in ("image_reading", "image_observation"):
            status, corrected, _ = confirmations.status(r.id) if confirmations is not None else ("unconfirmed", None, None)
            if status == "rejected":
                notes.append(f"{r.locator}: afgewezen door onderzoeker")
                continue
            x = r.extra
            if kind == "image_reading":
                value = corrected if status == "corrected" else r.value
                shown = x.get("as_printed") if status != "corrected" else f"{_score_txt(value)}{x.get('unit') if x.get('unit') == '%' else ''}"
                status_txt = {"unconfirmed": "door een taalmodel afgelezen, nog niet bevestigd",
                              "confirmed": "afgelezen en bevestigd door een onderzoeker",
                              "corrected": f"gecorrigeerd door een onderzoeker (model las {x.get('as_printed')})"}[status]
                stmt = f"Uit '{x.get('title')}' ({x.get('chart')}, {x.get('area')}): {x.get('label')} {shown} – {status_txt}."
                ind = demo_map.get(str(x.get("label", "")).strip().capitalize(), {}) or demo_map.get(str(x.get("chart", "")).split(" - ")[-1].strip(), {})
                themes = [t for t in (ind.get("themes", []) if ind else []) if t in THEME_CODES]
                data = {"value": value, "unit": x.get("unit"), "area": x.get("area"), "label": x.get("label"),
                        "confirmed": status in ("confirmed", "corrected"), "status": status,
                        "agreement": x.get("agreement"), "checks": x.get("checks", []), "model": x.get("model")}
                sal = 1.0 + (0.5 if status != "unconfirmed" else 0)
                ekind = "image_reading"
            else:
                status_txt = "modelbeschrijving, bevestigd" if status == "confirmed" else "modelbeschrijving, niet bevestigd"
                stmt = f"{x.get('title')}: {r.text} ({status_txt})."
                themes = [t for t in x.get("themes", []) if t in THEME_CODES]
                data = {"area": x.get("area"), "confirmed": status == "confirmed", "status": status, "model": x.get("model")}
                sal, ekind = 0.3, "image_observation"
            for sec in themes or [GENERAL]:
                out[sec].append(Evidence(evidence_id=ids.next("data", sec), case_id=case_id, themes=themes,
                                         perspective="data", kind=ekind, statement=stmt, data=data,
                                         source_record_ids=[r.id], salience=sal, method=x.get("extraction_method", "model-assisted")))
    return out, notes


# ----------------------------------------------------------------- researcher notes

NOTE_PERSPECTIVE = {"observatie": "expert", "bijeenkomst": "bewoners", "stakeholder": "bewoners",
                    "context": "context", "literatuur": "context"}


def note_evidence(records: list[SourceRecord], case_id: str, ids: _Ids) -> dict[str, list[Evidence]]:
    """Researcher notes (ingestion.parsers.notes_parser) as citable evidence.
    Project context goes to the Aanleiding section; everything else to its
    theme(s), or Algemeen. Notes from the resident meeting or stakeholder talks
    can contain personal details, so they carry the free-text privacy flag."""
    out: dict[str, list[Evidence]] = defaultdict(list)
    for r in records:
        if r.extra.get("record_kind") != "researcher_note" or not r.text:
            continue
        x = r.extra
        key = x.get("note_type", "onbekend")
        persp = NOTE_PERSPECTIVE.get(key, "context")
        src = x.get("source") or "bron niet ingevuld"
        when = f", {x['date']}" if x.get("date") else ""
        if key == "context":
            sections = [AANLEIDING]
            stmt = f"Projectcontext: {r.text} (bron: {src}{when})."
        elif key == "literatuur":
            sections = x.get("themes") or [GENERAL]
            stmt = f"Literatuur: {r.text} (bron: {src})."
        else:
            sections = (x.get("themes") or []) + ([GENERAL] if x.get("general") and x.get("themes") else [])
            sections = sections or [GENERAL]
            place = f", {x['place']}" if x.get("place") else ""
            cnt = f"; {x['count']}× genoemd" if x.get("count") else ""
            stmt = f"Onderzoekersnotitie ({x.get('note_type_label', 'notitie')}{place}{cnt}): \"{r.text}\" (bron: {src}{when})."
        themes = [t for t in (x.get("themes") or []) if t in THEME_CODES]
        for sec in sections:
            out[sec].append(Evidence(
                evidence_id=ids.next(persp, sec, code=NOTE_CODE), case_id=case_id, themes=themes if sec in THEME_CODES else [],
                perspective=persp, kind="note", statement=stmt,
                data={"note_type": key, "note_type_label": x.get("note_type_label"), "text": r.text,
                      "place": x.get("place"), "source": x.get("source"), "date": x.get("date"),
                      "count": x.get("count"), "is_stakeholder": bool(x.get("is_stakeholder")),
                      "group": x.get("note_type_label") if persp == "bewoners" else None},
                source_record_ids=[r.id], salience=1.5 + (0.3 * (x.get("count") or 0)),
                contains_free_text=key in ("bijeenkomst", "stakeholder"),
                method="researcher note (recorded by the research team)",
            ))
    return out


# ----------------------------------------------------------------- cross-theme (Conclusies)

def _quoted(statement: str) -> str:
    m = re.search(r'"(.+)"', statement or "")
    return m.group(1) if m else ""


def _term_text(e: Evidence) -> Optional[str]:
    """The researcher-/respondent-written words of an evidence item, if any
    (never the generated framing, which contains group names like '65+')."""
    if e.kind in ("coded", "quote"):
        return _quoted(e.statement)
    if e.kind == "expert_statement":
        return e.data.get("remark")
    if e.kind == "expert_remark":
        return _quoted(e.statement)
    if e.kind == "note" and e.data.get("note_type") in ("observatie", "bijeenkomst", "stakeholder"):
        return e.data.get("text")
    return None


def _nl_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " en " + items[-1]


def recurring_terms(theme_items: dict[str, list[Evidence]], case_id: str, ids: _Ids) -> list[Evidence]:
    cfg = load_config("reference/cross_theme_terms.toml")
    out = []
    rows = []
    for term in cfg.get("term", []):
        rx = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(p.lower()) for p in term["patterns"]) + ")", re.I)
        hits: list[Evidence] = []
        themes: list[str] = []
        for theme in THEMES:
            th = [e for e in theme_items.get(theme, []) if (_term_text(e) and rx.search(_term_text(e)))]
            if th:
                themes.append(theme)
                hits += th
        if len(themes) < 2:
            continue
        coded = [e for e in hits if e.kind == "coded"]
        quotes = [e for e in hits if e.kind == "quote"]
        expert = [e for e in hits if e.kind in ("expert_statement", "expert_remark")]
        notes = [e for e in hits if e.kind == "note"]
        n_mentions = sum(e.data.get("count", 1) for e in coded + quotes)
        rows.append((len(themes), n_mentions, term, themes, coded, quotes, expert, notes, hits))
    rows.sort(key=lambda r: (-r[0], -r[1]))
    for n_th, n_mentions, term, themes, coded, quotes, expert, notes, hits in rows:
        parts = []
        if coded:
            k = sum(e.data.get("count", 1) for e in coded)
            parts.append(f"{k}× genoemd in gecodeerde antwoorden van bewoners en stakeholders")
        if quotes:
            k = sum(e.data.get("count", 1) for e in quotes)
            parts.append(f"{k}× in open antwoorden van bewoners")
        if expert:
            parts.append(f"in {len(expert)} {'opmerking of stelling' if len(expert) == 1 else 'opmerkingen of stellingen'} "
                         f"van de experts")
        if notes:
            parts.append(f"in {len(notes)} {'onderzoekersnotitie' if len(notes) == 1 else 'onderzoekersnotities'}")
        n_coded = n_mentions
        out.append(Evidence(
            evidence_id=ids.next("cross", CONCLUSIES), case_id=case_id, themes=list(themes), perspective="cross",
            kind="cross_theme",
            statement=(f"'{term['label']}' komt terug bij {n_th} thema's ({_nl_list([t.lower() for t in themes])}): "
                       f"{_nl_list(parts)} (telling van trefwoorden; indicatief)."),
            data={"term": term["id"], "label": term["label"], "n_themes": n_th, "themes": themes,
                  "mentions": n_coded, "coded_items": len(coded), "quote_items": len(quotes), "expert_items": len(expert),
                  "notes": len(notes), "from_evidence": [e.evidence_id for e in hits]},
            source_record_ids=sorted({rid for e in hits for rid in e.source_record_ids}),
            salience=1.0 + n_th / 2 + min(n_coded, 30) / 15, method="keyword count (deterministic)",
        ))
    return out


def cross_theme_evidence(theme_items: dict[str, list[Evidence]], case_id: str, ids: _Ids) -> list[Evidence]:
    """New cross-theme facts plus the existing headline items of every theme.
    The headline items keep their own ids (they are the same objects as in
    the theme packs), so a conclusion cites the original evidence."""
    new: list[Evidence] = []
    overall = {t: next((e for e in theme_items.get(t, []) if e.kind == "theme_average"), None) for t in THEMES}
    overall = {t: e for t, e in overall.items() if e is not None}
    if len(overall) >= 3:
        ranked = sorted(overall.items(), key=lambda kv: -kv[1].data["mean"])
        new.append(Evidence(
            evidence_id=ids.next("cross", CONCLUSIES), case_id=case_id, themes=list(overall), perspective="expert",
            kind="cross_theme",
            statement=("Gemiddelde expertscore per thema (schaal 1 tot 5), van hoog naar laag: "
                       + "; ".join(f"{t.lower()} {nl_num(e.data['mean'])}" for t, e in ranked) + "."),
            data={"means": {t: e.data["mean"] for t, e in ranked}, "from_evidence": [e.evidence_id for _, e in ranked]},
            source_record_ids=sorted({rid for _, e in ranked for rid in e.source_record_ids}), salience=3.0,
            method="ranking of the theme averages (deterministic)",
        ))
    by_group: dict[str, dict[str, Evidence]] = defaultdict(dict)
    for t in THEMES:
        for e in theme_items.get(t, []):
            if e.kind == "rating":
                by_group[e.data.get("group") or "bewoners"][t] = e
    for group, per_theme in by_group.items():
        if len(per_theme) < 3:
            continue
        ranked = sorted(per_theme.items(), key=lambda kv: -kv[1].data["mean"])
        who = "bewoners" if group == "bewoners" else f"groep '{group}'"
        new.append(Evidence(
            evidence_id=ids.next("cross", CONCLUSIES), case_id=case_id, themes=list(per_theme), perspective="bewoners",
            kind="cross_theme",
            statement=(f"Gemiddeld cijfer van {who} per thema (schaal 1 tot 5), van hoog naar laag: "
                       + "; ".join(f"{t.lower()} {nl_num(e.data['mean'])} (n = {e.data['n']})" for t, e in ranked) + "."),
            data={"group": None if group == "bewoners" else group, "means": {t: e.data["mean"] for t, e in ranked},
                  "n": {t: e.data["n"] for t, e in ranked}, "from_evidence": [e.evidence_id for _, e in ranked]},
            source_record_ids=sorted({rid for _, e in ranked for rid in e.source_record_ids}), salience=3.0,
            method="ranking of the resident ratings (deterministic)",
        ))
    rule_items, seen = [], set()
    for t in THEMES:
        for e in theme_items.get(t, []):
            key = tuple(e.source_record_ids)
            if e.kind == "map_measurement" and e.data.get("tests_rule") and key not in seen:
                seen.add(key)
                rule_items.append(e)
    study = [e for e in rule_items if e.data.get("area") == "onderzoeksgebied"]
    if study:
        parts = []
        for e in study:
            title = e.statement.split(":")[0]
            if e.data.get("homes_pct") is not None:
                parts.append(f"{', '.join(e.data['rules'])} – {title}: {e.data['homes_pct']}% van de woningen binnen de zone")
            elif e.data.get("shares_pct"):
                sh = ", ".join(f"{v}% '{k}'" for k, v in e.data["shares_pct"].items() if v)
                parts.append(f"{', '.join(e.data['rules'])} – {title}: {sh}")
        new.append(Evidence(
            evidence_id=ids.next("cross", CONCLUSIES), case_id=case_id, themes=sorted({x for e in study for x in e.themes}),
            perspective="data", kind="cross_theme",
            statement="Uitkomst van de getoetste RIVM-vuistregels in het onderzoeksgebied (kaartmetingen; indicatief): "
                      + "; ".join(parts) + ".",
            data={"rules": sorted({r for e in study for r in e.data.get("rules", [])}),
                  "homes_pct": {", ".join(e.data["rules"]): e.data.get("homes_pct") for e in study if e.data.get("homes_pct") is not None},
                  "shares_pct": {", ".join(e.data["rules"]): e.data.get("shares_pct") for e in study if e.data.get("shares_pct")},
                  "from_evidence": [e.evidence_id for e in study]},
            source_record_ids=sorted({rid for e in study for rid in e.source_record_ids}), salience=3.0,
            method="summary of rule-testing map measurements (deterministic)",
        ))
    new += recurring_terms(theme_items, case_id, ids)
    headline = []
    for t in THEMES:
        for e in theme_items.get(t, []):
            if e.kind in ("theme_average", "rating", "comparison") and (e.kind != "theme_average" or e is overall.get(t)):
                headline.append(e)
    return new + headline + rule_items


# ----------------------------------------------------------------- norms + cross-perspective

def norm_evidence(store: SourceStore, case_id: str, ids: _Ids,
                  measured: Optional[dict[str, list[Evidence]]] = None) -> dict[str, list[Evidence]]:
    """RIVM rules as reference evidence. If a map in this case measures the
    rule, the norm statement points to that measurement; otherwise it says
    plainly that the rule is not tested in this data set."""
    measured_by_rule: dict[str, list[Evidence]] = defaultdict(list)
    for evs in (measured or {}).values():
        for e in evs:
            if e.kind == "map_measurement" and e.data.get("area") == "onderzoeksgebied" and e.data.get("tests_rule"):
                for rule in e.data.get("rules", []):
                    if e not in measured_by_rule[rule]:
                        measured_by_rule[rule].append(e)
    out: dict[str, list[Evidence]] = defaultdict(list)
    for r in store.all():
        if r.extra.get("record_kind") != "norm_rule":
            continue
        rule = r.extra["rule_id"]
        m = measured_by_rule.get(rule, [])
        if m:
            tail = (f"In deze Wijkscan getoetst met kaartgegevens (zie {', '.join(e.evidence_id for e in m)}); "
                    f"de vuistregels zijn richtinggevend, geen norm.")
        else:
            tail = ("De vuistregels zijn richtinggevend, geen norm. Deze gegevens bevatten geen afstandsmetingen, "
                    "dus of de buurt hieraan voldoet is niet getoetst.")
        for theme in r.extra["themes"]:
            out[theme].append(Evidence(
                evidence_id=ids.next("reference", theme), case_id=case_id, themes=[theme], perspective="reference",
                kind="norm",
                statement=f"RIVM-vuistregel {rule} ({r.extra['title']}): {r.text} {tail}",
                data={"rule_id": rule, "threshold": r.value, "unit": r.extra["unit"],
                      "measured_in_dataset": bool(m), "measured_by": [e.evidence_id for e in m]},
                source_record_ids=[r.id], salience=0.6,
            ))
    return out


def comparison_evidence(expert: dict[str, list[Evidence]], survey: dict[str, list[Evidence]], case_id: str,
                        ids: _Ids) -> dict[str, list[Evidence]]:
    out: dict[str, list[Evidence]] = defaultdict(list)
    for theme in THEMES:
        e_avg = next((e for e in expert.get(theme, []) if e.kind == "theme_average"), None)
        ratings = [e for e in survey.get(theme, []) if e.kind == "rating"]
        if not (e_avg and ratings):
            continue
        for b_rat in ratings:
            diff = b_rat.data["mean"] - e_avg.data["mean"]
            who = f"groep '{b_rat.data['group']}'" if b_rat.data.get("group") else "bewoners"
            if abs(diff) < 0.3:
                verdict = "liggen dicht bij elkaar"
            elif diff > 0:
                verdict = f"{who} is positiever dan de experts" if b_rat.data.get("group") else "bewoners zijn positiever dan de experts"
            else:
                verdict = f"{who} is kritischer dan de experts" if b_rat.data.get("group") else "bewoners zijn kritischer dan de experts"
            out[theme].append(Evidence(
                evidence_id=ids.next("cross", theme), case_id=case_id, themes=[theme], perspective="cross",
                kind="comparison",
                statement=(f"Voor {theme.lower()} geven de experts gemiddeld {nl_num(e_avg.data['mean'])} en {who} "
                           f"gemiddeld {nl_num(b_rat.data['mean'])} (verschil {nl_num(abs(diff))}): {verdict}. "
                           f"Let op: experts scoren stellingen, bewoners geven één cijfer; de vergelijking is indicatief."),
                data={"expert_mean": e_avg.data["mean"], "resident_mean": b_rat.data["mean"],
                      "difference": round(diff, 2), "group": b_rat.data.get("group"),
                      "from_evidence": [e_avg.evidence_id, b_rat.evidence_id]},
                source_record_ids=e_avg.source_record_ids + b_rat.source_record_ids, salience=2.5,
            ))
    return out


# ----------------------------------------------------------------- entry point

def build_case_evidence(store: SourceStore, case_id: str, n_respondents: Optional[int] = None,
                        confirmations=None) -> dict[str, EvidencePack]:
    records = [r for r in store.all() if r.case_id == case_id]
    if not records:
        raise ValueError(f"no records for case {case_id!r}")
    mapping = load_config("survey_mapping.toml")
    ids = _Ids()
    if n_respondents is None:
        n_respondents = len({r.extra["respondent"] for r in records if r.extra.get("record_kind") == "survey_answer"})

    expert = expert_evidence(records, case_id, ids)
    remarks = remark_evidence(records, case_id, ids)
    survey = survey_evidence(records, case_id, ids, mapping, n_respondents)
    tallies = tally_evidence(records, case_id, ids, mapping)
    coded = coded_evidence(records, case_id, ids)
    data, unavailable = demografie_evidence(records, case_id, ids)
    images, image_notes = image_evidence(records, case_id, ids, confirmations)
    norms = norm_evidence(store, case_id, ids, images)
    resident_ratings = {t: survey.get(t, []) + tallies.get(t, []) for t in THEMES}
    cross = comparison_evidence(expert, resident_ratings, case_id, ids)
    notes = note_evidence(records, case_id, ids)

    # images that need a model or were not used, so drafting knows they exist
    for r in records:
        if r.extra.get("record_kind") == "image":
            img_id = r.extra.get("image_id")
            has_derived = any(x.extra.get("image_record_id") == r.id for x in records)
            if not has_derived and r.extra.get("image_kind") in ("infographic", "map_vision"):
                unavailable.append(f"{r.source_file}: afbeelding nog niet afgelezen (vision-model nodig) – {r.text}")
            elif r.extra.get("image_kind") == "proposal_map":
                unavailable.append(f"{r.source_file}: ontwerpvoorstel, geen weergave van de huidige situatie")
    unavailable += image_notes

    built_with = {
        "survey_mapping": config_fingerprint("survey_mapping.toml"),
        "demografie_mapping": config_fingerprint("demografie_mapping.toml"),
        "rivm_rules": config_fingerprint("reference/rivm_vuistregels.toml"),
        "images": config_fingerprint("images.toml"),
        "cross_theme_terms": config_fingerprint("reference/cross_theme_terms.toml"),
        "builder_version": "2026-10-23b",
    }
    packs: dict[str, EvidencePack] = {}
    for section in list(THEMES) + [GENERAL, AANLEIDING]:
        items: list[Evidence] = []
        for part in (expert, remarks, survey, tallies, coded, data, images, notes, cross, norms):
            items.extend(part.get(section, []))
        packs[section] = EvidencePack(case_id, section, items, list(unavailable), dict(built_with))
    theme_items = {t: packs[t].items for t in THEMES}
    packs[CONCLUSIES] = EvidencePack(case_id, CONCLUSIES, cross_theme_evidence(theme_items, case_id, ids),
                                     list(unavailable), dict(built_with))
    return packs


def evidence_index(packs: dict[str, EvidencePack]) -> dict[str, Evidence]:
    idx: dict[str, Evidence] = {}
    for p in packs.values():
        idx.update(p.by_id())
    return idx
