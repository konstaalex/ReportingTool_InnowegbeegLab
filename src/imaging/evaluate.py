"""Score model readings of images against a hand-checked gold file (SQ3:
which vision model, how many samples, tiled or not - measured, not assumed).

Gold file (TOML): [[value]] image, chart, area, label, value, unit.
Matching: same image, same area (after aliases), label tokens overlapping
(Jaccard >= 0.5, best match wins). Reported per image and overall:

  recall           share of gold values the model returned at all
  value_accuracy   share of gold values returned with the right number (within tolerance)
  precision        share of returned values that match a gold value with the right number
  unmatched_rate   share of returned values that match no gold value at all
                   (hallucinated, or genuinely outside the gold set - inspect them)
  flagged_wrong    of the wrong values, how many the safeguards flagged
                   (agreement < 1 or a failed sanity check): what the reviewer is warned about
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

AREA_ALIASES = {
    "oss": "gemeente oss", "gemeente oss": "gemeente oss", "nl": "nederland",
    "beethovengaarde lisztgaarde": "beethovengaarde en lisztgaarde",
    "beethovengaarde en lisztgaarde": "beethovengaarde en lisztgaarde",
    "bl": "beethovengaarde en lisztgaarde", "v": "vlashoek", "s": "staatsliedenbuurt", "d": "dichtersbuurt",
    "k": "kortfoort", "cw": "centrum west", "centrum west": "centrum west",
    "staatslieden buurt": "staatsliedenbuurt", "dichters buurt": "dichtersbuurt",
}
STOP = {"jaar", "de", "het", "van", "per", "aan", "en", "%"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+]+", " ", str(s).lower().replace("&", " en ")).strip()


def norm_area(a: str) -> str:
    n = _norm(a)
    return AREA_ALIASES.get(n, AREA_ALIASES.get(n.replace(" en ", " "), n))


def _tokens(s: str) -> set[str]:
    t = {w for w in _norm(s).replace("+", " ouder ").split() if w not in STOP}
    return t


def _sim(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def load_gold(path: str | Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def score_readings(records: list, gold: dict) -> dict:
    tol = float(gold.get("value_tolerance", 0.5))
    gold_vals = gold["value"]
    preds = [{"id": r.id, "image": r.extra.get("image_id"), "area": r.extra.get("area", ""),
              "label": r.extra.get("label", ""), "chart": r.extra.get("chart", ""), "value": r.value,
              "flagged": (r.extra.get("agreement", 1) or 0) < 1 or bool(r.extra.get("checks"))} for r in records]
    used: set[int] = set()
    found = correct = 0
    misses, wrong = [], []
    per_image: dict[str, dict] = {}
    matched_pred: dict[int, bool] = {}
    for g in gold_vals:
        img = g["image"]
        pi = per_image.setdefault(img, {"gold": 0, "found": 0, "correct": 0})
        pi["gold"] += 1
        best, best_s = None, 0.0
        for i, p in enumerate(preds):
            if i in used or p["image"] != img or norm_area(p["area"]) != norm_area(g["area"]):
                continue
            s_ = max(_sim(p["label"], g["label"]), _sim(f"{p['chart']} {p['label']}", g["label"]))
            if s_ > best_s:
                best, best_s = i, s_
        if best is None or best_s < 0.5:
            misses.append(f"{img} | {g['chart']} | {g['area']} | {g['label']} = {g['value']}")
            continue
        used.add(best)
        found += 1
        pi["found"] += 1
        ok = preds[best]["value"] is not None and abs(float(preds[best]["value"]) - float(g["value"])) <= tol
        matched_pred[best] = ok
        if ok:
            correct += 1
            pi["correct"] += 1
        else:
            wrong.append(f"{img} | {g['area']} | {g['label']}: model {preds[best]['value']} vs gold {g['value']}"
                         + (" (flagged)" if preds[best]["flagged"] else " (NOT flagged)"))
    n_gold, n_pred = len(gold_vals), len(preds)
    unmatched = [i for i in range(n_pred) if i not in used]
    wrong_idx = [i for i, ok in matched_pred.items() if not ok]
    summary = {
        "gold_values": n_gold, "model_values": n_pred,
        "recall": round(found / n_gold, 3) if n_gold else None,
        "value_accuracy": round(correct / n_gold, 3) if n_gold else None,
        "precision": round(correct / n_pred, 3) if n_pred else None,
        "unmatched_rate": round(len(unmatched) / n_pred, 3) if n_pred else None,
        "wrong_values": len(wrong_idx),
        "flagged_wrong": sum(1 for i in wrong_idx if preds[i]["flagged"]),
        "per_image": {k: {**v, "value_accuracy": round(v["correct"] / v["gold"], 3)} for k, v in per_image.items()},
    }
    return {"summary": summary, "misses": misses, "wrong": wrong,
            "unmatched": [f"{preds[i]['image']} | {preds[i]['area']} | {preds[i]['label']} = {preds[i]['value']}" for i in unmatched]}
