"""Deterministic verifier. Flags, never rewrites (brief section 12C).

Per claim -> {"status": grounded|weak|unsupported, "flags": [...], "numbers": {...}}
Per section -> coverage gaps, repeated sentence openers, and metrics that the
experiment runner logs (citation validity, unsupported-claim rate, ...).

These checks are cheap enough to run on every draft on a 16 GB laptop, and
they make the reviewer's job targeted (US3): a red claim has a concrete,
named reason, not a vague low confidence score.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Iterable, Optional

from drafting.models import DraftClaim, SectionDraft
from evidence.models import AANLEIDING, CONCLUSIES, Evidence, EvidencePack
from ingestion.config import load_config
from ingestion.models import THEMES

ANECDOTAL = ("quote", "note")   # a few people's words: never "the residents"

SEVERITY_ORDER = {"unsupported": 3, "weak": 2, "style": 1}

# Dutch numbers: 3,4 | 45 | 1.500 (thousands) | 57,8
_NUM_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(?![\w])")
_RESIDENT_RE = re.compile(r"\b(bewoners?|inwoners?|respondenten?|buurtbewoners?|ondervraagden)\b", re.I)
_EXPERT_RE = re.compile(r"\b(experts?|expertwandeling|deskundigen)\b", re.I)
_NORM_RE = re.compile(r"\b(rivm|vuistregels?)\b", re.I)
_MAJORITY_RE = re.compile(
    r"\b(de meeste|meerderheid|merendeel|grootste deel|overgrote|(?:bijna|vrijwel) alle|alle bewoners|"
    r"veel bewoners|veel inwoners)\b", re.I)
# "Bewoners vinden/willen ..." without any number: presents an opinion as the group's view
_GENERIC_GROUP_RE = re.compile(r"\b(?:de )?(?:bewoners|inwoners) (?:vinden|willen|missen|ervaren|wensen|geven aan|zijn)\b", re.I)
_QUANTIFIED_RE = re.compile(r"\d+\s*%|\b\d+ van (?:de )?\d+\b")
_COMPLIANCE_RE = re.compile(r"\b(voldoet|voldoen|voldeed|voldoende afstand|niet binnen|wel binnen|haalt|halen)\b", re.I)
# 'worden' + past participle within 6 words; participles: ge-…d/t/en, or inseparable
# prefixes (be-, ver-, ont-, onder-, her-, er-, over-) …d/t/en, or -eerd. Heuristic.
_PASSIVE_RE = re.compile(
    r"\b(wordt|worden|werd|werden)\b(?:\s+\S+){0,6}?\s+"
    r"((?:ge|be|ver|ont|onder|her|er|over)\w{2,}(?:d|t|en)|\w+eerd)\b", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


@dataclass
class Flag:
    code: str
    severity: str          # unsupported | weak | style
    message: str
    rule: Optional[str] = None       # writing rule id, e.g. SW4.2
    source: Optional[str] = None     # rule document + locator

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ numbers

def parse_numbers(text: str) -> list[float]:
    out = []
    for m in _NUM_RE.finditer(text or ""):
        whole, dec = m.group(1), m.group(2)
        whole = whole.replace(".", "")
        out.append(float(f"{whole}.{dec}") if dec else float(whole))
    return out


def _walk_numbers(obj) -> Iterable[float]:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield float(obj)
    elif isinstance(obj, str):
        yield from parse_numbers(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, (int, float)) or (isinstance(k, str) and k.isdigit()):
                yield float(k)
            yield from _walk_numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_numbers(v)


def evidence_numbers(evs: list[Evidence]) -> list[float]:
    nums: list[float] = []
    for e in evs:
        nums.extend(parse_numbers(e.statement))
        nums.extend(_walk_numbers(e.data))
    return nums


def number_supported(n: float, pool: list[float], decimals_in_text: bool) -> bool:
    for m in pool:
        if decimals_in_text and abs(n - m) <= 0.051:
            return True
        if not decimals_in_text and (n == m or (not float(m).is_integer() and abs(n - m) < 0.5)):
            return True
    return False


# ------------------------------------------------------------------ style

def _sentences(text: str) -> list[str]:
    return [s for s in _SENT_SPLIT.split(text.strip()) if s]


def style_flags(text: str, rules: Optional[dict] = None) -> list[Flag]:
    rules = rules or load_config("reference/writing_rules.toml")
    sources = rules.get("sources", {})
    flags: list[Flag] = []
    quoted = re.sub(r"\"[^\"]*\"|'[^']*'", "", text)   # do not lint quoted resident/expert words
    for rule in rules["rule"]:
        check = rule["check"]
        src = sources.get(rule["source"], {}).get("file", rule["source"]) + f", {rule['locator']}"
        if check["type"] == "max_words":
            for s in _sentences(text):
                words = len(s.split())
                if words > check["max"]:
                    flags.append(Flag("style:long_sentence", "style",
                                      f"Zin van {words} woorden (max {check['max']}).", rule["id"], src))
        elif check["type"] == "phrase_list":
            for ph in check["phrases"]:
                if re.search(rf"(?<!\w){re.escape(ph)}(?!\w)", quoted, re.I):
                    flags.append(Flag("style:phrase", "style", f"Vermijd '{ph}'.", rule["id"], src))
        elif check["type"] == "passive_voice":
            m = _PASSIVE_RE.search(quoted)
            if m and not any(f.code == "style:passive" for f in flags):
                flags.append(Flag("style:passive", "style",
                                  f"Mogelijk lijdende vorm: '{m.group(0)}'. Schrijf actief.", rule["id"], src))
    return flags


# ------------------------------------------------------------------ claim checks

def _max_share(e: Evidence) -> float:
    d = e.data
    vals = []
    for key in ("pct", "pct_positive", "pct_high", "pct_low"):
        v = d.get(key)
        if isinstance(v, (int, float)):
            vals.append(v)
        elif isinstance(v, dict):
            vals.extend(x for x in v.values() if isinstance(x, (int, float)))
    return max(vals) if vals else 0.0


def verify_claim(claim: DraftClaim, section: str, index: dict[str, Evidence],
                 rules: Optional[dict] = None) -> dict:
    flags: list[Flag] = []
    cited = [index[i] for i in claim.evidence_ids if i in index]
    invalid = [i for i in claim.evidence_ids if i not in index]

    if not claim.evidence_ids:
        flags.append(Flag("no_citation", "unsupported", "Deze bewering verwijst naar geen enkel bewijsstuk."))
    if invalid:
        flags.append(Flag("invalid_citation", "unsupported",
                          f"Verwijst naar onbekende bewijsstukken: {', '.join(invalid)}."))

    # numbers
    nums_text = [(m.group(0), m.group(2) is not None) for m in _NUM_RE.finditer(claim.text)]
    pool = evidence_numbers(cited)
    unsupported_nums = []
    for raw, has_dec in nums_text:
        n = parse_numbers(raw)[0]
        if not number_supported(n, pool, has_dec):
            unsupported_nums.append(raw)
    if unsupported_nums and cited:
        flags.append(Flag("unsupported_number", "unsupported",
                          f"Getal(len) {', '.join(unsupported_nums)} staan niet in de aangehaalde bewijsstukken."))
    elif unsupported_nums and not cited:
        pass  # already unsupported via no_citation/invalid_citation

    perspectives = {e.perspective for e in cited}
    if cited and _RESIDENT_RE.search(claim.text) and not perspectives & {"bewoners", "cross"}:
        flags.append(Flag("attribution_mismatch", "weak",
                          "Noemt bewoners, maar verwijst niet naar bewonersdata."))
    if cited and _EXPERT_RE.search(claim.text) and not perspectives & {"expert", "cross"}:
        flags.append(Flag("attribution_mismatch", "weak",
                          "Noemt experts, maar verwijst niet naar de expertwandeling."))
    if cited and _NORM_RE.search(claim.text) and "reference" not in perspectives \
            and not any(e.kind in ("map_measurement", "cross_theme") and e.data.get("rules") for e in cited):
        flags.append(Flag("attribution_mismatch", "weak",
                          "Noemt de RIVM-vuistregels, maar verwijst er niet naar."))

    resident = [e for e in cited if e.perspective in ("bewoners", "cross")]
    if resident and _MAJORITY_RE.search(claim.text):
        if all(e.kind in ANECDOTAL for e in resident):
            flags.append(Flag("overgeneralisation", "weak",
                              "Formulering suggereert een meerderheid, maar steunt alleen op losse open antwoorden of notities."))
        elif max(_max_share(e) for e in resident) < 50:
            flags.append(Flag("overgeneralisation", "weak",
                              "Formulering suggereert een meerderheid, maar geen aangehaald aandeel is 50% of meer."))
    elif (resident and _GENERIC_GROUP_RE.search(claim.text) and not _QUANTIFIED_RE.search(claim.text)
          and all(e.kind in ANECDOTAL for e in resident)):
        flags.append(Flag("overgeneralisation", "weak",
                          "Stelt een mening als die van 'de bewoners', maar steunt alleen op losse open antwoorden "
                          "(noem hoeveel bewoners dit zeggen)."))

    if cited and _COMPLIANCE_RE.search(claim.text) and any(e.kind == "norm" for e in cited) \
            and not any(e.kind == "map_measurement" and e.data.get("tests_rule") for e in cited):
        flags.append(Flag("norm_not_measured", "weak",
                          "Uitspraak over het (niet) voldoen aan een RIVM-vuistregel zonder een meting die de regel toetst."))

    unconfirmed = [e.evidence_id for e in cited if e.kind in ("image_reading", "image_observation")
                   and not e.data.get("confirmed")]
    if unconfirmed:
        flags.append(Flag("image_unconfirmed", "weak",
                          f"Steunt op door een taalmodel afgelezen beeldinformatie die nog niet is bevestigd: "
                          f"{', '.join(unconfirmed)}."))

    resident_like = [e for e in cited if e.perspective == "bewoners"]
    if resident_like and _RESIDENT_RE.search(claim.text) and all(e.data.get("is_stakeholder") for e in resident_like):
        flags.append(Flag("attribution_mismatch", "weak",
                          "Noemt bewoners, maar de aangehaalde antwoorden komen van stakeholders (professionals)."))

    # only a theme section can be off-theme; Conclusies cites across themes by design
    off_theme = [e.evidence_id for e in cited if e.themes and section not in e.themes and section in THEMES]
    if off_theme:
        flags.append(Flag("theme_mismatch", "weak",
                          f"Bewijsstukken uit een ander thema: {', '.join(off_theme)}."))

    flags.extend(style_flags(claim.text, rules))
    grounding = [f for f in flags if f.severity != "style"]
    status = "grounded"
    if any(f.severity == "unsupported" for f in grounding):
        status = "unsupported"
    elif grounding:
        status = "weak"
    return {
        "status": status,
        "flags": [f.to_dict() for f in flags],
        "numbers": {"in_text": [r for r, _ in nums_text], "unsupported": unsupported_nums},
        "cited_valid": [e.evidence_id for e in cited],
        "cited_invalid": invalid,
    }


# ------------------------------------------------------------------ section checks

def salient_evidence(pack: EvidencePack) -> list[Evidence]:
    """Facts a reviewer would expect to see covered in this section."""
    if pack.section == CONCLUSIES:
        return [e for e in pack.items if e.kind == "cross_theme" and (e.data.get("n_themes", 3) >= 3)]
    if pack.section == AANLEIDING:
        return [e for e in pack.items if e.kind == "note"]
    out = []
    for e in pack.items:
        k = e.kind
        if k in ("theme_average", "rating", "comparison"):
            out.append(e)
        elif k == "expert_statement" and (e.data.get("score", 3) <= 2 or e.data.get("score", 3) == 5):
            out.append(e)
        elif k in ("option_share", "likert") and _max_share(e) >= 40:
            out.append(e)
        elif k in ("choice", "multi") and _max_share(e) >= 40:
            out.append(e)
        elif k == "map_measurement" and e.data.get("tests_rule") and e.data.get("area") == "onderzoeksgebied":
            out.append(e)
    return out


def _repeated_openers(claims: list[DraftClaim], min_repeats: int) -> list[str]:
    openers: dict[str, int] = {}
    for c in claims:
        words = re.findall(r"\w+", c.text.lower())[:2]
        if len(words) == 2:
            key = " ".join(words)
            openers[key] = openers.get(key, 0) + 1
    return [k for k, v in openers.items() if v >= min_repeats]


def verify_section(draft: SectionDraft, pack: EvidencePack, index: Optional[dict[str, Evidence]] = None) -> dict:
    """Verifies every claim in place and returns section-level checks + metrics."""
    index = index or pack.by_id()
    rules = load_config("reference/writing_rules.toml")
    for c in draft.claims:
        c.verification = verify_claim(c, draft.section, index, rules)

    cited_ids = {i for c in draft.claims for i in c.evidence_ids}
    salient = salient_evidence(pack)
    gaps = [e.evidence_id for e in salient if e.evidence_id not in cited_ids]
    rep_rule = next((r for r in rules["rule"] if r["check"]["type"] == "repeated_openers"), None)
    repeated = _repeated_openers(draft.claims, rep_rule["check"]["min_repeats"]) if rep_rule else []

    n = len(draft.claims) or 1
    all_ids = [i for c in draft.claims for i in c.evidence_ids]
    nums = [x for c in draft.claims for x in c.verification["numbers"]["in_text"]]
    bad_nums = [x for c in draft.claims for x in c.verification["numbers"]["unsupported"]]
    statuses = [c.verification["status"] for c in draft.claims]
    style_count = sum(1 for c in draft.claims for f in c.verification["flags"] if f["severity"] == "style")
    metrics = {
        "n_claims": len(draft.claims),
        "grounded_rate": statuses.count("grounded") / n,
        "weak_rate": statuses.count("weak") / n,
        "unsupported_rate": statuses.count("unsupported") / n,
        "citation_validity": (sum(1 for i in all_ids if i in index) / len(all_ids)) if all_ids else 0.0,
        "number_support_rate": (1 - len(bad_nums) / len(nums)) if nums else 1.0,
        "style_flags_per_claim": style_count / n,
        "coverage_salient": (1 - len(gaps) / len(salient)) if salient else 1.0,
        "claim_type_counts": {t: sum(1 for c in draft.claims if c.claim_type == t) for t in
                              ("bevinding", "interpretatie", "conclusie", "aanbeveling")},
        "perspectives_cited": sorted({index[i].perspective for i in all_ids if i in index}),
    }
    draft.section_checks = {
        "coverage_gaps": gaps,
        "repeated_openers": repeated,
        "metrics": metrics,
    }
    return draft.section_checks
