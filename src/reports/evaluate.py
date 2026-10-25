"""How closely does a generated report follow the original (human) report?

EVALUATION ONLY. The original report is never a drafting input: ingestion registers
it as 'excluded_reference_report', and nothing in this module writes to the
evidence store or to anything a drafting pipeline reads. Its outputs (which quote
the original) stay in the workspace, which is git-ignored.

What is measured. Everything is deterministic and explainable, so the same report
always gets the same score. The thresholds are documented below and can be
calibrated against a researcher's labels with `calibrate`.

  structure   which chapters of the original the generated report has
  figures     maps (by name), charts (by title), photos and method figures of the
              original that the generated report has
  coverage    per statement of the original (sentences of >= 6 words, per chapter), the
              best-matching generated sentence in the same chapter is found. It is
              scored on shared content words, with rare words weighing more (IDF), and
              on shared numbers. The label is covered (>= COVERED), partial
              (>= PARTIAL) or missing.
  ceiling     the same match against ALL evidence of the case: is the statement
              supported by the data, at least partly? A statement the data cannot
              support (field knowledge, the resident meeting, literature) is not a
              model failure, so coverage_of_supportable is the fair score for a model.
  judge       optional (reports.judge): a local model labels each statement against
              the candidate sentences the word overlap found. Word overlap alone
              judges paraphrases poorly (kappa 0.38 on a developer-labelled sample).
  numbers     the numbers of the original that are found back in the matched
              sentence, and matched statements whose sentence carries different
              numbers (possible conflicts, listed for the researcher to check)
  additions   generated sentences with no counterpart in that chapter of the
              original. They are not wrong (the verifier checks grounding), but they
              show a different emphasis.

Recommendations are excluded from the overall coverage, because the tool does not
write them, by design (brief section 5). They are reported separately, and so are the
figures in the recommendations chapter (inspiration photos).

    result = evaluate_run(ws, case_id, run_id)                 # -> writes <run>/evaluation/
    result = evaluate_run(ws, case_id, run_id, judge_model="local_qwen7b")   # with the LLM judge
    table  = compare_runs(ws, case_id, [run ids])              # the three models side by side
    calibrate("<run>/evaluation/alignment.csv")                # after filling in the human_label column
"""
from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Iterable, Optional

from evidence.models import AANLEIDING, CONCLUSIES, GENERAL
from ingestion.config import load_config
from ingestion.models import THEMES

# Thresholds for the word-overlap labels. Initial values were set on 89 statements of the
# Oss report (Resultaten: algemeen, Sporten, Actieve mobiliteit, Formeel spelen) that the
# developer labelled against the evidence: kappa 0.38 (fair). Recalibrate them with
# researcher labels (calibrate()), or use the LLM judge (reports.judge).
COVERED, PARTIAL = 0.32, 0.26
SUPPORTABLE = PARTIAL            # "supportable" = at least partly supported by the evidence
MIN_SCORE_FOR_JUDGE = 0.08       # below this nothing overlaps: missing, without asking the judge
MIN_WORDS = 6
CHART_MATCH = 0.6

CHAPTER_HEADINGS = {
    "SAMENVATTING": "samenvatting", "AANLEIDING": "aanleiding", "METHODE": "methode", "RESULTATEN": "resultaten",
    "CONCLUSIES": "conclusies", "CONCLUSIE": "conclusies", "AANBEVELINGEN": "aanbevelingen",
    "BRONNENLIJST": "bronnen", "BRONNEN": "bronnen", "BIJLAGE1": "bijlage1", "BIJLAGE2": "bijlage2",
    "BIJLAGE3": "bijlage3", "COLOFON": "colofon", "CONTACTGEGEVENS": "colofon", "INHOUDSOPGAVE": "inhoud",
}
SCORED = ["samenvatting", "aanleiding", "methode", "resultaten", *THEMES, "conclusies"]
STOP = set("""de het een en of van in op te voor met aan bij door om als dat die dit deze er is zijn was waren
wordt worden werd werden kan kunnen zal zullen heeft hebben had ook nog niet geen wel meer veel zeer zo dan
maar want omdat hun hen zij ze wij we je u hij zich haar over uit naar tot toe onder tussen binnen buiten
na waar wat wie welke hoe dus al alle elk ieder iedere andere ander eigen even per sinds mits zoals
namelijk daarnaast hierbij hiervoor waarbij waarvan daarbij daarom echter vooral mede ten ter des den
the and één twee drie vier vijf zes geeft geven gaat gaan komt komen""".split())
_NUM = re.compile(r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(%?)")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9'\"(‘“])")
_CAPTION = re.compile(r"^(Afbeelding|Figuur)\s+(\d+)\s*:?\s*(.*)$")
_PLACEHOLDER = re.compile(r"^\[[^\]]*\][\s\d]*$")
_THEME_HEADING = re.compile(r"^Thema\s+\d\s*[-–]\s*(.+?)\s*$")


# ------------------------------------------------------------------ text units

def _stem(w: str) -> str:
    for suf in ("heden", "heid", "ingen", "ing", "en", "s", "e"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def terms(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-zà-ÿ0-9+\-]+", (text or "").lower()):
        w = w.strip("-")
        if not w or w in STOP or w.isdigit() or len(w) < 3:
            continue
        for part in w.split("-"):
            if len(part) >= 3 and part not in STOP and not part.isdigit():
                out.add(_stem(part))
    return out


def numbers(text: str) -> set[float]:
    out = set()
    for m in _NUM.finditer(text or ""):
        whole, dec = m.group(1).replace(".", ""), m.group(2)
        v = float(f"{whole}.{dec}") if dec else float(whole)
        if 1900 <= v <= 2100 and not m.group(3):      # years are not findings
            continue
        out.add(v)
    return out


def sentences(text: str) -> list[str]:
    text = " ".join((text or "").split())
    return [s.strip() for s in _SENT.split(text) if len(s.split()) >= MIN_WORDS]


class Matcher:
    """Weighted recall of a reference statement's content words in a candidate,
    plus the share of its numbers found back. Rare words weigh more (IDF over all
    texts of the comparison), so 'jeu de boules' counts more than 'buurt'."""

    def __init__(self, corpus: Iterable[str]):
        df: Counter = Counter()
        n = 0
        for t in corpus:
            n += 1
            df.update(terms(t))
        self.n, self.df = max(n, 1), df

    def weight(self, term: str) -> float:
        return math.log(1 + self.n / (1 + self.df.get(term, 0)))

    @staticmethod
    def prepare(units: list[str]) -> list[tuple[set, set]]:
        return [(terms(u), numbers(u)) for u in units]

    def score(self, ref: tuple[set, set], cand: tuple[set, set]) -> float:
        rt, rn = ref
        if not rt:
            return 0.0
        total = sum(self.weight(t) for t in rt)
        recall = sum(self.weight(t) for t in rt & cand[0]) / total
        if rn:
            return 0.75 * recall + 0.25 * len(rn & cand[1]) / len(rn)
        return recall

    def best(self, ref: tuple[set, set], cands: list[tuple[set, set]]) -> tuple[float, int]:
        best, idx = 0.0, -1
        for i, c in enumerate(cands):
            s = self.score(ref, c)
            if s > best:
                best, idx = s, i
        return best, idx


def label(score: float) -> str:
    return "covered" if score >= COVERED else ("partial" if score >= PARTIAL else "missing")


# ------------------------------------------------------------------ the original report

def _family(fontname: str) -> str:
    return re.split(r"[-,]", fontname.split("+")[-1])[0]


def style_profile(words: list[dict]) -> dict:
    """Body size and heading font of a report, read from the PDF itself: body text is the most
    common size between 9 and 12.5 pt; the heading font is the font of text well above that
    size. (Original Oss report: Arial Nova 11 pt, Trebuchet headings. The tool's own Word
    output converted by LibreOffice: Liberation Sans 10 pt, Carlito headings.)"""
    sizes = Counter(round(w["size"] * 2) / 2 for w in words if 9 <= w["size"] <= 12.5)
    body = sizes.most_common(1)[0][0] if sizes else 11.0
    body_family = Counter(_family(w["fontname"]) for w in words if abs(w["size"] - body) < 0.3).most_common(1)
    head_family = Counter(_family(w["fontname"]) for w in words if w["size"] >= body + 3).most_common(1)
    return {"body": body, "body_family": body_family[0][0] if body_family else "",
            "heading_family": head_family[0][0] if head_family else ""}


def _word_kind(w: dict, prof: dict) -> str:
    font, size, body = w["fontname"], w["size"], prof["body"]
    if prof["heading_family"] and prof["heading_family"] != prof["body_family"]:
        if _family(font) == prof["heading_family"]:
            return "heading" if size >= body + 0.4 else "other"      # same font, body size or less: page numbers
    elif size >= body + 0.9 and ("Bold" in font or size >= body + 3):
        return "heading"
    if size >= body - 0.5:
        return "body"
    if "Italic" in font or "Oblique" in font:
        return "caption"
    return "other"                                                  # chart labels, tables, footnotes


def _fragments(words: list[dict], prof: dict, page_height: float) -> list[dict]:
    """Runs of words of one kind on one line, in reading order; header and footer skipped."""
    frags: list[dict] = []
    for w in words:
        if w["top"] < 30 or w["bottom"] > page_height - 30:
            continue
        kind, bold = _word_kind(w, prof), "Bold" in w["fontname"]
        last = frags[-1] if frags else None
        if last and last["kind"] == kind and abs(last["top"] - w["top"]) < 3 and w["x0"] - last["x1"] < 30:
            last["text"] += " " + w["text"]
            last["x1"] = w["x1"]
            last["bold"] = last["bold"] and bold
        else:
            frags.append({"kind": kind, "bold": bold, "top": w["top"], "x0": w["x0"], "x1": w["x1"], "text": w["text"]})
    return frags


def _clean(text: str) -> str:
    text = re.sub(r"\s+([,.;:!?)])", r"\1", " ".join(text.split()))
    return text.replace("( ", "(")


def _chart_titles(frags: list[dict]) -> dict[int, str]:
    """Charts carry their title as bold body text above the 'Figuur n' caption, in the
    same column. Returns {index of the caption fragment: title} and marks the title
    fragments so they are not read as body text."""
    out = {}
    for i, f in enumerate(frags):
        if f["kind"] != "caption" or not re.match(r"^Figuur\s+\d+\s*$", _clean(f["text"])):
            continue
        parts = [g for g in frags if g["kind"] == "body" and g["bold"] and not g.get("used")
                 and f["top"] - 300 < g["top"] < f["top"] and abs(g["x0"] - f["x0"]) < 160
                 and len(g["text"].split()) <= 18]
        for g in parts:
            g["used"] = True
        if parts:
            out[i] = _clean(" ".join(g["text"] for g in sorted(parts, key=lambda g: g["top"])))
    return out


def load_reference(pdf_path: str | Path) -> dict:
    """Original report -> {"sections": {key: text}, "figures": [{kind, number, title, chapter}], "pages": n}.

    Uses the layout of the Wijkscan reports, read from the PDF (style_profile): headings in
    the heading font, body text at the body size, captions smaller and italic, chart labels
    smaller and upright (skipped). Header and footer lines are skipped."""
    import pdfplumber
    sections: "OrderedDict[str, list[str]]" = OrderedDict()
    figures: list[dict] = []
    chapter = current = "voorwerk"
    with pdfplumber.open(str(pdf_path)) as pdf:
        n_pages = len(pdf.pages)
        pages = [(p.extract_words(extra_attrs=["fontname", "size"], keep_blank_chars=False), p.height)
                 for p in pdf.pages]
    prof = style_profile([w for ws_, _ in pages for w in ws_])
    for words, height in pages:
        frags = _fragments(words, prof, height)
        if any(f["kind"] == "heading" and f["text"].replace(" ", "").upper() == "INHOUDSOPGAVE" for f in frags):
            continue
        titles = _chart_titles(frags)
        last_caption = None
        for i, f in enumerate(frags):
            text = _clean(f["text"])
            if f["kind"] == "heading":
                key = text.replace(" ", "").upper()
                m = re.match(r"^BIJLAGE(\d)", key)
                key = f"BIJLAGE{m.group(1)}" if m else key
                if key in CHAPTER_HEADINGS:
                    chapter = current = CHAPTER_HEADINGS[key]
                    continue
                m = _THEME_HEADING.match(text)
                if m and m.group(1) in THEMES and chapter == "resultaten":
                    current = m.group(1)
                continue
            if f["kind"] == "caption":
                m = _CAPTION.match(text)
                if m:
                    cap = {"kind": m.group(1), "number": int(m.group(2)),
                           "title": m.group(3) or titles.get(i, ""), "chapter": current}
                    figures.append(cap)
                    last_caption = cap
                elif last_caption is not None and len(last_caption["title"]) < 120:
                    last_caption["title"] = (last_caption["title"] + " " + text).strip()
                continue
            if f["kind"] == "body" and not f.get("used"):
                sections.setdefault(current, []).append(text)
    return {"sections": {k: _clean(" ".join(v)) for k, v in sections.items()}, "figures": figures,
            "pages": n_pages, "path": str(pdf_path), "style": prof}


def reference_path(ws, case_id: str) -> Optional[Path]:
    """The original report registered (and excluded) at ingestion, if the case folder is known."""
    root = ws.cases().get(case_id, {}).get("root")
    if not root:
        return None
    for d in ws.documents():
        if d.get("case_id") == case_id and d.get("status") == "excluded_reference_report":
            p = Path(root) / d["filename"]
            if p.exists():
                return p
    return None


# ------------------------------------------------------------------ the generated report

def generated_sections(ws, case_id: str, drafts: dict) -> dict[str, list[str]]:
    """Generated text per chapter, as units (claims split into sentences), placed the
    way the Word renderer places them (export/docx_report.py)."""
    from export.report import methode_parts

    def units(items: Iterable[str]) -> list[str]:
        out = []
        for t in items:
            out += sentences(t) or ([t.strip()] if t.strip() else [])
        return out

    def claims(sec: str, types: Optional[tuple] = None, exclude: tuple = ("aanbeveling",)) -> list[str]:
        d = drafts.get(sec)
        return [c.text for c in (d.claims if d else [])
                if (types is None or c.claim_type in types) and c.claim_type not in exclude]

    out = {t: units(claims(t)) for t in THEMES}
    out["resultaten"] = units(claims(GENERAL))
    out["aanleiding"] = units(claims(AANLEIDING))
    theme_conclusions = [t for th in THEMES for t in claims(th, ("conclusie",))]
    out["conclusies"] = units(claims(CONCLUSIES) + theme_conclusions)
    out["samenvatting"] = units(claims(CONCLUSIES, ("conclusie",)) or theme_conclusions)
    info = ws.cases().get(case_id, {})
    parts = methode_parts(ws.evidence_index(case_id), ws.store(), case_id,
                          "Wijkscan" if info.get("layout") == "wijkscan" else "Buurtscan", info)
    out["methode"] = units([parts.get(k) or "" for k in ("intro", "data", "expert", "bewoners", "notes", "rules_text")])
    out["aanbevelingen"] = units([t for sec in drafts for t in claims(sec, ("aanbeveling",), exclude=())])
    return out


# ------------------------------------------------------------------ figures

def _map_names() -> list[str]:
    titles = {c.get("title") for c in load_config("images.toml").get("image", []) if c.get("title")}
    return sorted(titles | {"Sunburst"}, key=len, reverse=True)


def _classify(title: str, kind: str, names: list[str]) -> tuple[str, str]:
    t = (title or "").lower()
    if kind == "Figuur":
        return "chart", title
    if "demografie" in t:
        return "infographic", title
    if "inspiratie" in t:
        return "inspiration", title
    for n in names:
        if t.startswith(n.lower()) or f" {n.lower()}" in f" {t}":
            return "map", n
    if any(k in t for k in ("bvo", "perspectieven", "geselecteerde buurten", "onderzoeksgebied en buurten")):
        return "method", title
    return "photo", title


def figure_comparison(reference: dict, run_figures: list[dict]) -> dict:
    names = _map_names()
    ref = [(*_classify(f["title"], f["kind"], names), f["chapter"]) for f in reference["figures"]]
    gen = [(*_classify(f["caption"], f["kind"], names), (f.get("chapter") or "").lower()) for f in run_figures]
    in_body = [(k, n) for k, n, ch in ref if ch != "aanbevelingen"]
    out: dict = {}
    rmaps, gmaps = sorted({n for k, n in in_body if k == "map"}), sorted({n for k, n, _ in gen if k == "map"})
    out["map"] = {"reference": rmaps, "generated": gmaps, "matched": [m for m in rmaps if m in gmaps],
                  "missing": [m for m in rmaps if m not in gmaps], "extra": [m for m in gmaps if m not in rmaps]}
    rcharts = [n for k, n in in_body if k == "chart"]
    gcharts = [n for k, n, _ in gen if k == "chart"]
    m = Matcher(rcharts + gcharts)
    gprep = Matcher.prepare(gcharts)
    pairs = []
    for c in rcharts:
        s, i = m.best((terms(c), numbers(c)), gprep) if gprep else (0.0, -1)
        pairs.append({"reference": c, "generated": gcharts[i] if i >= 0 and s >= CHART_MATCH else None,
                      "score": round(s, 3)})
    out["chart"] = {"reference": len(rcharts), "generated": len(gcharts),
                    "matched": sum(1 for x in pairs if x["generated"]), "pairs": pairs}
    for kind in ("photo", "method", "infographic"):
        r = sum(1 for k, _ in in_body if k == kind)
        g = sum(1 for k, _, _ in gen if k == kind)
        out[kind] = {"reference": r, "generated": g, "matched": min(r, g)}
    out["infographic"]["note"] = "in the original the demographic pages have no caption (counted as 0)"
    out["recommendations_chapter"] = {"reference": sum(1 for *_x, ch in ref if ch == "aanbevelingen"),
                                      "note": "inspiration figures of the recommendations: not generated by design"}
    total_ref = len(rmaps) + len(rcharts) + out["photo"]["reference"] + out["method"]["reference"]
    total_hit = len(out["map"]["matched"]) + out["chart"]["matched"] + out["photo"]["matched"] + out["method"]["matched"]
    out["coverage"] = round(total_hit / total_ref, 3) if total_ref else None
    out["coverage_maps_and_charts"] = round((len(out["map"]["matched"]) + out["chart"]["matched"])
                                            / (len(rmaps) + len(rcharts)), 3) if rmaps or rcharts else None
    return out


# ------------------------------------------------------------------ evaluation

def top_candidates(matcher: Matcher, ref: tuple[set, set], cands: list[tuple[set, set]], k: int) -> list[tuple[float, int]]:
    """The k best candidates by weighted overlap, topped up with the best by plain word
    overlap (a judge gets both, so a paraphrase with common words is not lost)."""
    scored = sorted(((matcher.score(ref, c), i) for i, c in enumerate(cands)), reverse=True)
    out = [x for x in scored[:k] if x[0] > 0]
    rt = ref[0]
    if rt:
        plain = sorted(((len(rt & c[0]) / len(rt), i) for i, c in enumerate(cands)), reverse=True)[:max(2, k // 3)]
        have = {i for _, i in out}
        out += [(matcher.score(ref, cands[i]), i) for p, i in plain if p > 0 and i not in have]
    return out


def evaluate(reference: dict, generated: dict[str, list[str]], evidence_statements: list[str],
             run_figures: Optional[list[dict]] = None, judge=None, progress=None) -> dict:
    """Align every statement of the original with the generated report (coverage) and with
    the evidence (ceiling). `judge` (reports.judge.Judge) decides the labels when given;
    otherwise the overlap score does, with the thresholds above."""
    keys = [*SCORED, "aanbevelingen"]
    evidence_statements = list(evidence_statements)
    # "[...]" are placeholders of the tool's own renderer (when a report is compared with itself)
    refs_by = {k: [x for x in sentences(reference["sections"].get(k, "")) if not _PLACEHOLDER.match(x)] for k in keys}
    # word weights from the original and the evidence only, so the ceiling is the same for every run
    matcher = Matcher([s for v in refs_by.values() for s in v] + evidence_statements)
    ev_prep = Matcher.prepare(evidence_statements)
    rows, per_section = [], {}
    n_total = sum(len(v) for k, v in refs_by.items() if k != "aanbevelingen")
    done = 0
    for key in keys:
        refs, gen = refs_by[key], generated.get(key, [])
        gen_prep = Matcher.prepare(gen)
        use_judge = judge is not None and key != "aanbevelingen"
        sec = {"reference_statements": len(refs), "generated_units": len(gen), "covered": 0, "partial": 0, "missing": 0,
               "supportable": 0, "covered_supportable": 0.0, "numbers_agree": 0, "numbers_reference": 0,
               "numbers_differ": 0}
        for s in refs:
            rp = (terms(s), numbers(s))
            g_top = top_candidates(matcher, rp, gen_prep, 6)
            e_top = top_candidates(matcher, rp, ev_prep, 8)
            score = g_top[0][0] if g_top else 0.0
            e_score = e_top[0][0] if e_top else 0.0
            lex, e_lex = label(score), label(e_score)
            lab, e_lab, picked, conflict, reason = lex, e_lex, [g_top[0][1]] if g_top else [], False, ""
            if use_judge:
                v = judge(s, [gen[i] for _, i in g_top], "report") if score >= MIN_SCORE_FOR_JUDGE else \
                    {"label": "missing", "sentences": [], "conflict": False, "reason": "geen overlap"}
                if v:
                    lab, conflict, reason = v["label"], v["conflict"], v["reason"]
                    picked = [g_top[i][1] for i in v["sentences"]] or picked
                ve = judge(s, [evidence_statements[i] for _, i in e_top], "evidence") if e_score >= MIN_SCORE_FOR_JUDGE \
                    else {"label": "missing"}
                if ve:
                    e_lab = ve["label"]
                done += 1
                if progress and done % 10 == 0:
                    progress(f"beoordeeld: {done} van {n_total} uitspraken")
            supportable = e_lab != "missing"
            rn = rp[1]
            matched = " ".join(gen[i] for i in picked) if lab != "missing" else ""
            gn = numbers(matched)
            differ = conflict or bool(rn and gn and not (rn & gn))
            sec[lab] += 1
            sec["supportable"] += supportable
            sec["covered_supportable"] += (1.0 if lab == "covered" else 0.5 if lab == "partial" else 0.0) if supportable else 0.0
            sec["numbers_reference"] += len(rn)
            sec["numbers_agree"] += len(rn & gn)
            sec["numbers_differ"] += differ
            rows.append({"section": key, "reference": s, "label": lab, "auto_label": lex, "score": round(score, 3),
                         "judge_label": lab if use_judge else "", "judge_reason": reason,
                         "best_generated": matched or (gen[g_top[0][1]] if g_top else ""),
                         "evidence_label": e_lab, "evidence_auto_label": e_lex, "evidence_score": round(e_score, 3),
                         "best_evidence": evidence_statements[e_top[0][1]][:300] if e_top else "",
                         "numbers_reference": " ".join(f"{n:g}" for n in sorted(rn)),
                         "numbers_generated": " ".join(f"{n:g}" for n in sorted(gn)),
                         "numbers_differ": differ, "human_label": "", "human_evidence_label": ""})
        ref_prep = Matcher.prepare(refs)
        sec["additions"] = sum(1 for g in gen_prep if matcher.best(g, ref_prep)[0] < PARTIAL) if refs else len(gen)
        per_section[key] = sec
    if judge is not None:
        judge.save()

    scored = [k for k in SCORED if per_section[k]["reference_statements"]]

    def total(field: str):
        return sum(per_section[k][field] for k in scored)

    n, supportable, units = total("reference_statements"), total("supportable"), total("generated_units")
    ref_ch = [k for k in keys if reference["sections"].get(k)]
    gen_ch = [k for k in keys if generated.get(k)]
    ref_body = [k for k in ref_ch if k != "aanbevelingen"]
    summary = {
        "method": f"llm-judge:{judge.model_id}" if judge is not None else "word-overlap",
        "structure": {"reference_chapters": ref_ch, "generated_chapters": gen_ch,
                      "coverage": round(sum(k in gen_ch for k in ref_body) / len(ref_body), 3) if ref_body else None,
                      "missing": [k for k in ref_body if k not in gen_ch],
                      "not_generated_by_design": ["aanbevelingen"] if "aanbevelingen" in ref_ch else []},
        "content": {
            "reference_statements": n, "covered": total("covered"), "partial": total("partial"),
            "missing": total("missing"),
            "coverage": round((total("covered") + 0.5 * total("partial")) / n, 3) if n else None,
            "supportable_by_evidence": supportable,
            "ceiling": round(supportable / n, 3) if n else None,
            "coverage_of_supportable": round(total("covered_supportable") / supportable, 3) if supportable else None,
            "numbers_in_reference": total("numbers_reference"), "numbers_agree": total("numbers_agree"),
            "possible_number_conflicts": total("numbers_differ"),
            "generated_units": units, "additions": total("additions"),
            "addition_rate": round(total("additions") / units, 3) if units else None,
        },
        "recommendations": {"reference_statements": per_section["aanbevelingen"]["reference_statements"],
                            "generated": per_section["aanbevelingen"]["generated_units"],
                            "note": "not generated by design: the researcher writes the recommendations"},
        "thresholds": {"covered": COVERED, "partial": PARTIAL, "supportable": SUPPORTABLE, "min_words": MIN_WORDS,
                       "applies_to": "word-overlap labels; with a judge the judge decides"},
    }
    if run_figures is not None:
        summary["figures"] = figure_comparison(reference, run_figures)
    return {"summary": summary, "per_section": per_section, "rows": rows}


# ------------------------------------------------------------------ outputs

def _pct(x) -> str:
    return "–" if x is None else f"{round(100 * x)}%"


SECTION_NL = {"samenvatting": "Samenvatting", "aanleiding": "Aanleiding", "methode": "Methode",
              "resultaten": "Resultaten (algemeen)", "conclusies": "Conclusies", "aanbevelingen": "Aanbevelingen"}


METHOD_NL = {"word-overlap": "gedeelde inhoudswoorden en getallen (snel, benadering)"}


def _method_nl(method: str) -> str:
    if method.startswith("llm-judge:"):
        return f"LLM-beoordelaar {method.split(':', 1)[1]} (lokaal) op de kandidaten uit de woordovereenkomst"
    return METHOD_NL.get(method, method)


def render_markdown(result: dict, title: str) -> str:
    s, c = result["summary"], result["summary"]["content"]
    lines = [f"# Vergelijking met het originele rapport · {title}", "",
             "_Alleen evaluatie: het originele rapport is nooit invoer voor het concept. "
             "Dit bestand citeert het origineel en blijft in de werkmap (niet in git)._", "",
             f"Beoordeeld met: {_method_nl(s.get('method', 'word-overlap'))}.", "",
             "| Maat | Waarde |", "|---|---|",
             f"| Hoofdstukken van het origineel aanwezig | {_pct(s['structure']['coverage'])} "
             f"(ontbreekt: {', '.join(s['structure']['missing']) or '–'}) |",
             f"| Uitspraken van het origineel (zonder aanbevelingen) | {c['reference_statements']} |",
             f"| Gedekt / deels / niet | {c['covered']} / {c['partial']} / {c['missing']} |",
             f"| **Dekking** (gedekt + ½ deels) | **{_pct(c['coverage'])}** |",
             f"| Plafond: uitspraken die de data (deels) onderbouwt | {_pct(c['ceiling'])} "
             f"({c['supportable_by_evidence']}) |",
             f"| **Dekking van wat de data onderbouwt** | **{_pct(c['coverage_of_supportable'])}** |",
             f"| Getallen van het origineel teruggevonden | {c['numbers_agree']} van {c['numbers_in_reference']} |",
             f"| Mogelijk andere getallen dan het origineel | {c['possible_number_conflicts']} |",
             f"| Aanvullingen (concept zonder tegenhanger) | {c['additions']} van {c['generated_units']} "
             f"({_pct(c['addition_rate'])}) |", ""]
    if "figures" in s:
        f = s["figures"]
        lines += ["## Afbeeldingen en grafieken", "",
                  f"- Kaarten: {len(f['map']['matched'])} van {len(f['map']['reference'])} "
                  f"(ontbreekt: {', '.join(f['map']['missing']) or '–'}; extra: {', '.join(f['map']['extra']) or '–'})",
                  f"- Grafieken: {f['chart']['matched']} van {f['chart']['reference']} op titel teruggevonden "
                  f"({f['chart']['generated']} gemaakt)",
                  f"- Foto's: {f['photo']['generated']} geplaatst, {f['photo']['reference']} in het origineel "
                  "(foto's komen alleen uit onderzoekersnotities)",
                  f"- Methodefiguren: {f['method']['generated']} van {f['method']['reference']}",
                  f"- Inspiratiebeelden bij de aanbevelingen: {f['recommendations_chapter']['reference']} "
                  "(niet gegenereerd, zie aanbevelingen)", ""]
    lines += ["## Per hoofdstuk", "",
              "| Hoofdstuk | Uitspraken | Gedekt | Deels | Niet | (Deels) onderbouwd door data | Aanvullingen |",
              "|---|---|---|---|---|---|---|"]
    for k, v in result["per_section"].items():
        lines.append(f"| {SECTION_NL.get(k, k)} | {v['reference_statements']} | {v['covered']} | {v['partial']} | "
                     f"{v['missing']} | {v['supportable']} | {v['additions']} |")
    conflicts = [r for r in result["rows"] if r["numbers_differ"]]
    if conflicts:
        lines += ["", "## Na te kijken: andere getallen dan het origineel", ""]
        for r in conflicts[:15]:
            lines += [f"- **{SECTION_NL.get(r['section'], r['section'])}** · origineel ({r['numbers_reference']}): "
                      f"{r['reference']}", f"  - concept ({r['numbers_generated']}): {r['best_generated']}"]
    lines += ["", f"Aanbevelingen: {s['recommendations']['reference_statements']} uitspraken in het origineel; "
                  "de tool schrijft geen aanbevelingen (dat doet de onderzoeker).", "",
              f"Drempels voor de woordovereenkomst: gedekt ≥ {COVERED}, deels ≥ {PARTIAL} (gedeelde inhoudswoorden, "
              "zeldzame woorden wegen zwaarder, en getallen). Kalibreer met `alignment.csv`: vul human_label "
              "(concept) en/of human_evidence_label (data) met covered / partial / missing en draai "
              "`python -m reports.cli calibrate`.", ""]
    return "\n".join(lines)


def write_outputs(result: dict, out_dir: str | Path, title: str) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation.json").write_text(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2,
                                                    ensure_ascii=False), encoding="utf-8")
    fields = list(result["rows"][0].keys()) if result["rows"] else ["section"]
    with open(out / "alignment.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(result["rows"])
    (out / "evaluation.md").write_text(render_markdown(result, title), encoding="utf-8")
    return {"json": out / "evaluation.json", "csv": out / "alignment.csv", "markdown": out / "evaluation.md"}


def evidence_statements(ws, case_id: str) -> list[str]:
    seen, out = set(), []
    for e in ws.evidence_index(case_id).values():
        if e.statement not in seen:          # the same rule appears in several theme packs
            seen.add(e.statement)
            out.append(e.statement)
    return out


def make_judge(ws, case_id: str, judge_model: Optional[str], allow_hosted: bool = False):
    if not judge_model:
        return None
    from llm.registry import get_provider, validate_model_config
    from .judge import Judge
    problems = validate_model_config(judge_model)
    if problems:
        raise ValueError("; ".join(problems))
    return Judge(get_provider(judge_model), ws.p("reports", case_id, "judge", f"{judge_model}.json"), allow_hosted)


def _reference(ws, case_id: str, reference_pdf: Optional[str | Path]) -> dict:
    pdf = Path(reference_pdf) if reference_pdf else reference_path(ws, case_id)
    if pdf is None or not Path(pdf).exists():
        raise FileNotFoundError("original report not found: pass its path (--reference) or ingest the case "
                                "folder again so the tool knows where it is")
    return load_reference(pdf)


def evaluate_run(ws, case_id: str, run_id: str, reference_pdf: Optional[str | Path] = None,
                 reference: Optional[dict] = None, judge_model: Optional[str] = None, allow_hosted: bool = False,
                 judge=None, progress=None) -> dict:
    from .runs import load_run, run_path
    reference = reference or _reference(ws, case_id, reference_pdf)
    judge = judge or make_judge(ws, case_id, judge_model, allow_hosted)
    run, drafts = load_run(ws, case_id, run_id)
    result = evaluate(reference, generated_sections(ws, case_id, drafts), evidence_statements(ws, case_id),
                      run.get("figures"), judge=judge, progress=progress)
    result["summary"]["run"] = {k: run.get(k) for k in ("run_id", "model_id", "pipeline", "prompt_version")}
    result["summary"]["run"]["verifier"] = run["totals"]
    result["summary"]["reference"] = {"file": Path(reference["path"]).name, "pages": reference["pages"]}
    if judge is not None:
        result["summary"]["judge"] = {"model": judge.model_id, "calls": judge.calls, "failures": judge.failures}
    title = f"{run.get('model_id') or 'template'} · {run['pipeline']}"
    result["files"] = {k: str(v) for k, v in write_outputs(result, run_path(ws, case_id, run_id) / "evaluation",
                                                          title).items()}
    return result


COMPARISON_COLUMNS = [
    ("model", "Model"), ("pipeline", "Pijplijn"), ("structure", "Hoofdstukken"), ("coverage", "Dekking"),
    ("coverage_of_supportable", "Dekking van wat data onderbouwt"), ("numbers", "Getallen terug"),
    ("conflicts", "Mogelijk andere getallen"), ("addition_rate", "Aanvullingen"), ("figures", "Kaarten + grafieken"),
    ("grounded_rate", "Onderbouwd (controle)"), ("unsupported_rate", "Niet onderbouwd (controle)"),
    ("failed", "Mislukte secties"), ("wall_s", "Tijd (s)"),
]
PCT_COLUMNS = {"structure", "coverage", "coverage_of_supportable", "addition_rate", "figures", "grounded_rate",
               "unsupported_rate"}


def comparison_row(run_id: str, summary: dict) -> dict:
    c, t = summary["content"], summary["run"]["verifier"]
    f = summary.get("figures", {})
    return {
        "run": run_id, "model": summary["run"]["model_id"] or "template (geen model)",
        "pipeline": summary["run"]["pipeline"], "method": summary.get("method", "word-overlap"),
        "structure": summary["structure"]["coverage"],
        "coverage": c["coverage"], "coverage_of_supportable": c["coverage_of_supportable"], "ceiling": c["ceiling"],
        "numbers": f"{c['numbers_agree']}/{c['numbers_in_reference']}", "conflicts": c["possible_number_conflicts"],
        "addition_rate": c["addition_rate"], "figures": f.get("coverage_maps_and_charts"),
        "grounded_rate": t.get("grounded_rate"), "unsupported_rate": t.get("unsupported_rate"),
        "failed": len(t.get("failed_sections") or []), "claims": t.get("n_claims"), "wall_s": t.get("wall_s"),
        "tokens_out": t.get("tokens_out"),
    }


def compare_runs(ws, case_id: str, run_ids: list[str], reference_pdf: Optional[str | Path] = None,
                 judge_model: Optional[str] = None, allow_hosted: bool = False, progress=None) -> dict:
    """Evaluate each run against the original and write one comparison table
    (reports/<case>/comparison/comparison.md + .csv)."""
    reference = _reference(ws, case_id, reference_pdf)
    judge = make_judge(ws, case_id, judge_model, allow_hosted)
    rows = []
    for rid in run_ids:
        if progress:
            progress(f"evalueren: {rid}")
        rows.append(comparison_row(rid, evaluate_run(ws, case_id, rid, reference=reference, judge=judge,
                                                     progress=progress)["summary"]))
    out = ws.p("reports", case_id, "comparison")
    out.mkdir(parents=True, exist_ok=True)
    md = ["# Modellen vergeleken met het originele rapport", "",
          f"Beoordeeld met: {_method_nl(rows[0]['method'])}.", "",
          "| " + " | ".join(h for _, h in COMPARISON_COLUMNS) + " |", "|" + "---|" * len(COMPARISON_COLUMNS)]
    for r in rows:
        md.append("| " + " | ".join(_pct(r[k]) if k in PCT_COLUMNS else str(r[k]) for k, _ in COMPARISON_COLUMNS)
                  + " |")
    md += ["", "Plafond (deel van het origineel dat de data (deels) onderbouwt): "
           + ", ".join(sorted({_pct(r['ceiling']) for r in rows})) + ". Dekking, getallen en kaarten/grafieken: "
           "hoger is beter. Aanvullingen zijn niet fout, wel een andere nadruk. Onderbouwd/niet onderbouwd komt uit "
           "de controle van elke bewering tegen de bewijsstukken.", ""]
    (out / "comparison.md").write_text("\n".join(md), encoding="utf-8")
    with open(out / "comparison.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return {"rows": rows, "markdown": str(out / "comparison.md"), "csv": str(out / "comparison.csv")}


CATS = ["covered", "partial", "missing"]


def _kappa(auto: list[str], human: list[str]) -> tuple[float, float]:
    n = len(auto)
    po = sum(a == h for a, h in zip(auto, human)) / n
    pe = sum((auto.count(c) / n) * (human.count(c) / n) for c in CATS)
    return po, ((po - pe) / (1 - pe) if pe < 1 else 1.0)


def _agreement(rows: list[dict], human_col: str, auto_col: str, score_col: str) -> Optional[dict]:
    rows = [r for r in rows if (r.get(human_col) or "").strip().lower() in CATS]
    if not rows:
        return None
    human = [r[human_col].strip().lower() for r in rows]
    auto = [r[auto_col] for r in rows]
    po, k = _kappa(auto, human)
    out = {"labelled": len(rows), "agreement": round(po, 3), "cohens_kappa": round(k, 3),
           "confusion_auto_vs_human": {a: {h: sum(1 for x, y in zip(auto, human) if x == a and y == h) for h in CATS}
                                       for a in CATS}}
    if score_col:
        scores = [float(r[score_col] or 0) for r in rows]
        best = (k, None, None)
        for cov in [x / 100 for x in range(16, 81, 2)]:
            for par in [x / 100 for x in range(8, int(round(cov * 100)), 2)]:
                kk = _kappa(["covered" if s >= cov else "partial" if s >= par else "missing" for s in scores], human)[1]
                if kk > best[0] + 1e-9:
                    best = (kk, cov, par)
        if best[1] is not None:
            out["suggested_thresholds"] = {"covered": best[1], "partial": best[2], "cohens_kappa": round(best[0], 3)}
    return out


def calibrate(alignment_csv: str | Path) -> dict:
    """Agreement between the automatic labels and a researcher's labels. Fill in, per row of
    alignment.csv, human_label (is the statement in the generated report?) and/or
    human_evidence_label (is it supported by the evidence?) with covered / partial / missing.
    Cohen's kappa is reported for the word-overlap labels and, if the run was judged, for
    the judge; `suggested_thresholds` are the overlap thresholds that agree best."""
    with open(alignment_csv, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = {"report": {"word_overlap": _agreement(rows, "human_label", "auto_label", "score")},
           "evidence": {"word_overlap": _agreement(rows, "human_evidence_label", "evidence_auto_label",
                                                   "evidence_score")}}
    if any(r.get("judge_label") for r in rows):
        out["report"]["judge"] = _agreement([r for r in rows if r.get("judge_label")], "human_label", "judge_label", "")
        out["evidence"]["judge"] = _agreement([r for r in rows if r.get("judge_label")], "human_evidence_label",
                                              "evidence_label", "")
    if not any(v for part in out.values() for v in part.values()):
        return {"labelled": 0, "note": "fill in human_label and/or human_evidence_label first "
                                       "(covered / partial / missing)"}
    return out
