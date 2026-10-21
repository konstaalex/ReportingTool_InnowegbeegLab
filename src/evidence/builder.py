"""Build Evidence items for one Buurtscan case. Pure Python, no model.

    packs = build_case_evidence(store, "passewaaij-tiel")
    packs["Sporten"]      # EvidencePack for the Sporten section
    packs["Algemeen"]     # context: respondents, demografie, general quotes

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

from .models import GENERAL_CODE, PERSPECTIVE_CODES, THEME_CODES, Evidence, EvidencePack

GENERAL = "Algemeen"
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

    def next(self, perspective: str, section: str) -> str:
        code = THEME_CODES.get(section, GENERAL_CODE)
        key = f"{PERSPECTIVE_CODES[perspective]}-{code}"
        self.counters[key] += 1
        return f"{key}-{self.counters[key]:02d}"


# ----------------------------------------------------------------- expert

def expert_evidence(records: list[SourceRecord], case_id: str, ids: _Ids) -> dict[str, list[Evidence]]:
    out: dict[str, list[Evidence]] = defaultdict(list)
    by_theme: dict[str, list[SourceRecord]] = defaultdict(list)
    for r in records:
        if r.extra.get("record_kind") == "expert_statement" and r.theme and r.value is not None:
            by_theme[r.theme].append(r)
    for theme in THEMES:
        rs = sorted(by_theme.get(theme, []), key=lambda r: r.extra["statement_number"])
        if not rs:
            continue
        scores = [r.value for r in rs]
        mean = statistics.mean(scores)
        low = [r for r in rs if r.value <= 2]
        high = [r for r in rs if r.value >= 4]
        out[theme].append(Evidence(
            evidence_id=ids.next("expert", theme), case_id=case_id, themes=[theme], perspective="expert",
            kind="theme_average",
            statement=(f"De experts beoordelen het thema {theme.lower()} gemiddeld met een {nl_num(mean)} "
                       f"op een schaal van 1 tot 5 ({len(rs)} stellingen; {len(low)} keer 2 of lager, "
                       f"{len(high)} keer 4 of hoger)."),
            data={"mean": round(mean, 2), "n_statements": len(rs), "min": min(scores), "max": max(scores),
                  "n_low": len(low), "n_high": len(high)},
            source_record_ids=[r.id for r in rs], salience=3.0,
        ))
        for r in rs:
            score = int(r.value)
            remark = r.extra.get("remark")
            stmt = (f"Stelling {r.extra['statement_number']} ({r.subtheme}): \"{r.text}\" krijgt van de experts "
                    f"score {score} ({SCALE_NL.get(score, '?')}).")
            if remark:
                stmt += f" Opmerking van de experts: \"{remark}\"."
            out[theme].append(Evidence(
                evidence_id=ids.next("expert", theme), case_id=case_id, themes=[theme], perspective="expert",
                kind="expert_statement", statement=stmt,
                data={"score": score, "statement_number": r.extra["statement_number"], "indicator": r.subtheme,
                      "remark": remark, "cells": r.extra.get("cells")},
                source_record_ids=[r.id], salience=abs(score - 3) + (0.5 if remark else 0.0),
                # expert remarks are professional observations, not resident data: no privacy flag
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
            out[GENERAL].append(Evidence(
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
            out[section].append(Evidence(
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
            out[section].append(Evidence(
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
            out[section].append(Evidence(
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
                out[sec].append(Evidence(
                    evidence_id=ids.next("bewoners", sec), case_id=case_id, themes=[sec] if theme else [],
                    perspective="bewoners", kind="option_share",
                    statement=(f"{pct(k, n)}% van de bewoners ({k} van {n}) {verb} '{option}' "
                               f"({label}, meerdere antwoorden mogelijk)."),
                    data={"n": n, "k": k, "pct": pct(k, n), "option": option, "question_id": qid},
                    source_record_ids=[r.id for r in rs_o] + totals, salience=0.5 + 2 * k / n,
                ))

        # open text attached to this question (Omdat:/Toelichting:/Anders/open-ended)
        out[section].extend(_quotes(rs, case_id, section, ids, label))
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


# ----------------------------------------------------------------- norms + cross-perspective

def norm_evidence(store: SourceStore, case_id: str, ids: _Ids) -> dict[str, list[Evidence]]:
    out: dict[str, list[Evidence]] = defaultdict(list)
    for r in store.all():
        if r.extra.get("record_kind") != "norm_rule":
            continue
        for theme in r.extra["themes"]:
            out[theme].append(Evidence(
                evidence_id=ids.next("reference", theme), case_id=case_id, themes=[theme], perspective="reference",
                kind="norm",
                statement=(f"RIVM-vuistregel {r.extra['rule_id']} ({r.extra['title']}): {r.text} "
                           f"De vuistregels zijn richtinggevend, geen norm. Deze Buurtscan bevat geen afstandsmetingen, "
                           f"dus of de buurt hieraan voldoet is niet getoetst."),
                data={"rule_id": r.extra["rule_id"], "threshold": r.value, "unit": r.extra["unit"],
                      "measured_in_dataset": False},
                source_record_ids=[r.id], salience=0.6,
            ))
    return out


def comparison_evidence(expert: dict[str, list[Evidence]], survey: dict[str, list[Evidence]], case_id: str,
                        ids: _Ids) -> dict[str, list[Evidence]]:
    out: dict[str, list[Evidence]] = defaultdict(list)
    for theme in THEMES:
        e_avg = next((e for e in expert.get(theme, []) if e.kind == "theme_average"), None)
        b_rat = next((e for e in survey.get(theme, []) if e.kind == "rating"), None)
        if not (e_avg and b_rat):
            continue
        diff = b_rat.data["mean"] - e_avg.data["mean"]
        if abs(diff) < 0.3:
            verdict = "liggen dicht bij elkaar"
        elif diff > 0:
            verdict = "bewoners zijn positiever dan de experts"
        else:
            verdict = "bewoners zijn kritischer dan de experts"
        out[theme].append(Evidence(
            evidence_id=ids.next("cross", theme), case_id=case_id, themes=[theme], perspective="cross",
            kind="comparison",
            statement=(f"Voor {theme.lower()} geven de experts gemiddeld {nl_num(e_avg.data['mean'])} en bewoners "
                       f"gemiddeld {nl_num(b_rat.data['mean'])} (verschil {nl_num(abs(diff))}): {verdict}. "
                       f"Let op: experts scoren stellingen, bewoners geven één cijfer; de vergelijking is indicatief."),
            data={"expert_mean": e_avg.data["mean"], "resident_mean": b_rat.data["mean"],
                  "difference": round(diff, 2), "from_evidence": [e_avg.evidence_id, b_rat.evidence_id]},
            source_record_ids=e_avg.source_record_ids + b_rat.source_record_ids, salience=2.5,
        ))
    return out


# ----------------------------------------------------------------- entry point

def build_case_evidence(store: SourceStore, case_id: str, n_respondents: Optional[int] = None) -> dict[str, EvidencePack]:
    records = [r for r in store.all() if r.case_id == case_id]
    if not records:
        raise ValueError(f"no records for case {case_id!r}")
    mapping = load_config("survey_mapping.toml")
    ids = _Ids()
    if n_respondents is None:
        n_respondents = len({r.extra["respondent"] for r in records if r.extra.get("record_kind") == "survey_answer"})

    expert = expert_evidence(records, case_id, ids)
    survey = survey_evidence(records, case_id, ids, mapping, n_respondents)
    data, unavailable = demografie_evidence(records, case_id, ids)
    norms = norm_evidence(store, case_id, ids)
    cross = comparison_evidence(expert, survey, case_id, ids)

    built_with = {
        "survey_mapping": config_fingerprint("survey_mapping.toml"),
        "demografie_mapping": config_fingerprint("demografie_mapping.toml"),
        "rivm_rules": config_fingerprint("reference/rivm_vuistregels.toml"),
        "builder_version": "2026-10-21",
    }
    packs: dict[str, EvidencePack] = {}
    for section in list(THEMES) + [GENERAL]:
        items: list[Evidence] = []
        for part in (expert, survey, data, cross, norms):
            items.extend(part.get(section, []))
        packs[section] = EvidencePack(case_id, section, items,
                                      unavailable if section == GENERAL else [], dict(built_with))
    return packs


def evidence_index(packs: dict[str, EvidencePack]) -> dict[str, Evidence]:
    idx: dict[str, Evidence] = {}
    for p in packs.values():
        idx.update(p.by_id())
    return idx

