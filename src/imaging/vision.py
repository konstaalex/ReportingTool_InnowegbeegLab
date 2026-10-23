"""Model-assisted image reading, made checkable.

Where it is used: images whose information is printed text/numbers
(infographics) or icons (symbol maps) - things a deterministic pixel rule
cannot read. Local vision models are strong at reading printed numbers
(Qwen2.5-VL 7B / Qwen3-VL 8B: DocVQA ~95, ChartQA ~87) but can still misread,
mis-assign a value to the wrong neighbourhood, or invent one.

Three safeguards, all from the literature:
1. Self-ensembling (Berkane et al., 2026, "Self-Ensembling VLMs for Chart Data
   Extraction"): read the image N times, align the readings, take the median
   per value and use agreement across samples as an uncertainty signal.
2. Deterministic sanity checks on the extracted table: percentages in 0-100,
   donut/pie groups summing to ~100%.
3. Human confirmation: every reading starts as 'unconfirmed'; the verifier
   flags any claim that relies on an unconfirmed reading, and the review UI
   shows the image next to the value so a researcher confirms, corrects or
   rejects it (logged, hash-chained).

Tiling: large infographics (3508 x 2480) are downscaled by the model's image
encoder; small printed numbers survive better when the image is read in
overlapping tiles. Values seen in two overlapping tiles are de-duplicated by
the alignment step.
"""
from __future__ import annotations

import io
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from PIL import Image

from llm.base import GenerationRequest, LLMProvider, Message

Image.MAX_IMAGE_PIXELS = None

READING_SCHEMA = {
    "type": "object",
    "properties": {
        "values": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chart": {"type": "string"},
                    "area": {"type": "string"},
                    "label": {"type": "string"},
                    "value": {"type": "number"},
                    "unit": {"type": "string"},
                    "as_printed": {"type": "string"},
                },
                "required": ["chart", "area", "label", "value", "unit", "as_printed"],
            },
        }
    },
    "required": ["values"],
}

OBSERVATION_SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "area": {"type": "string"},
                               "legend_item": {"type": "string"}},
                "required": ["text", "area", "legend_item"],
            },
        }
    },
    "required": ["observations"],
}

READ_PROMPT = """Je leest een infographic uit een Nederlands onderzoeksrapport ({title}).
Neem ALLEEN getallen over die letterlijk in de afbeelding gedrukt staan. Schat niets en reken niets uit.
Geef voor elk getal:
- chart: het kopje of de grafiek waar het bij hoort (bijv. "Leeftijdsgroepen", "Vitaliteit - Overgewicht")
- area: het gebied (bijv. "Nederland", "Gemeente Oss" of de buurtnaam)
- label: wat het getal betekent (bijv. "65 jaar of ouder", "Overgewicht")
- value: het getal als nummer (52% -> 52; €29.400 -> 29400)
- unit: "%", "EUR", "index" of ""
- as_printed: precies zoals gedrukt (bijv. "52%")
Kun je bij een getal niet zien bij welk gebied of label het hoort, laat het dan weg.
Antwoord alleen met JSON: {{"values": [...]}}"""

DESCRIBE_PROMPT = """Je bekijkt een kaart uit een Nederlands onderzoeksrapport ({title}): {measure}.
Beschrijf alleen wat duidelijk zichtbaar is, per buurt waar mogelijk ({areas}).
Tel niets nauwkeurig en noem geen afstanden die niet op de kaart staan.
Antwoord alleen met JSON: {{"observations": [{{"text": "...", "area": "...", "legend_item": "..."}}]}}"""


def image_bytes(path: str | Path, max_side: int = 1792, box: Optional[tuple[int, int, int, int]] = None) -> bytes:
    im = Image.open(path).convert("RGB")
    if box:
        im = im.crop(box)
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def tiles(path: str | Path, n: int = 2, overlap: float = 0.12) -> list[tuple[int, int, int, int]]:
    """n x n overlapping crop boxes (x0, y0, x1, y1) in full-image pixels."""
    w, h = Image.open(path).size
    tw, th = w / n, h / n
    ox, oy = tw * overlap, th * overlap
    boxes = []
    for j in range(n):
        for i in range(n):
            boxes.append((int(max(0, i * tw - ox)), int(max(0, j * th - oy)),
                          int(min(w, (i + 1) * tw + ox)), int(min(h, (j + 1) * th + oy))))
    return boxes


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9%€]+", " ", str(s).lower()).strip()


@dataclass
class Reading:
    chart: str
    area: str
    label: str
    value: float
    unit: str
    as_printed: str
    samples_seen: int
    samples_total: int
    values_seen: list[float]
    tiles_seen: list[int]
    checks: list[str] = field(default_factory=list)

    @property
    def agreement(self) -> float:
        """Share of samples that produced this value (within 0.5 of the median)."""
        close = sum(1 for v in self.values_seen if abs(v - self.value) <= 0.5)
        return min(1.0, close / max(self.samples_total, 1))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["agreement"] = round(self.agreement, 3)
        return d


@dataclass
class ReadingRun:
    image: str
    model: dict
    samples: int
    tiles: int
    readings: list[Reading]
    raw_outputs: list[str]
    errors: list[str]
    latency_s: float

    def to_dict(self) -> dict:
        return {"image": self.image, "model": self.model, "samples": self.samples, "tiles": self.tiles,
                "readings": [r.to_dict() for r in self.readings], "raw_outputs": self.raw_outputs,
                "errors": self.errors, "latency_s": round(self.latency_s, 2)}


def ensemble(samples: list[list[dict]], tile_of_sample: list[int], n_samples: int) -> list[Reading]:
    """Align readings across samples on (chart, area, label); median value.
    Within one sample a key seen in two overlapping tiles counts once."""
    by_key: dict[tuple, dict] = {}
    for s_idx, values in enumerate(samples):
        sample_no = s_idx // max(1, len(set(tile_of_sample)) or 1)
        for v in values:
            try:
                val = float(v["value"])
            except (KeyError, TypeError, ValueError):
                continue
            key = (_norm(v.get("chart", "")), _norm(v.get("area", "")), _norm(v.get("label", "")))
            e = by_key.setdefault(key, {"first": v, "per_sample": defaultdict(list), "tiles": set()})
            e["per_sample"][sample_no].append(val)
            e["tiles"].add(tile_of_sample[s_idx])
    out = []
    for key, e in by_key.items():
        per_sample_vals = [statistics.median(vs) for vs in e["per_sample"].values()]
        med = statistics.median(per_sample_vals)
        f = e["first"]
        out.append(Reading(chart=str(f.get("chart", "")), area=str(f.get("area", "")), label=str(f.get("label", "")),
                           value=med, unit=str(f.get("unit", "")), as_printed=str(f.get("as_printed", "")),
                           samples_seen=len(per_sample_vals), samples_total=n_samples,
                           values_seen=per_sample_vals, tiles_seen=sorted(e["tiles"])))
    return out


def sanity_checks(readings: list[Reading], pct_sum_tolerance: float = 3.0) -> None:
    """Adds human-readable check failures to each reading in place."""
    groups: dict[tuple, list[Reading]] = defaultdict(list)
    for r in readings:
        if r.unit.strip() == "%":
            if not (0 <= r.value <= 100):
                r.checks.append(f"percentage {r.value} outside 0-100")
            groups[(_norm(r.chart), _norm(r.area))].append(r)
    for (chart, area), rs in groups.items():
        if len(rs) >= 3 and any(w in chart for w in ("leeftijd", "woning", "soorten", "herkomst", "migratie")):
            total = sum(r.value for r in rs)
            if abs(total - 100) > pct_sum_tolerance:
                for r in rs:
                    r.checks.append(f"parts of '{r.chart}' / '{r.area}' sum to {total:g}%, not ~100%")


def read_infographic(path: str | Path, provider: LLMProvider, title: str, samples: int = 3, n_tiles: int = 1,
                     temperature: float = 0.3, max_side: int = 1792) -> ReadingRun:
    import time
    boxes = tiles(path, n_tiles) if n_tiles > 1 else [None]
    all_values: list[list[dict]] = []
    tile_ids: list[int] = []
    raws, errors = [], []
    t0 = time.perf_counter()
    for s in range(samples):
        for t_idx, box in enumerate(boxes):
            req = GenerationRequest(
                task="read_infographic",
                messages=[Message("system", "Je leest getallen nauwkeurig af uit afbeeldingen."),
                          Message("user", READ_PROMPT.format(title=title))],
                temperature=temperature, max_output_tokens=2500, json_schema=READING_SCHEMA, seed=100 + s,
                images=[image_bytes(path, max_side, box)],
            )
            res = provider.generate(req)
            raws.append(res.text)
            if res.error or not isinstance(res.parsed, dict):
                errors.append(f"sample {s} tile {t_idx}: {res.error or 'no JSON'}")
                all_values.append([])
            else:
                all_values.append(list(res.parsed.get("values", [])))
            tile_ids.append(t_idx)
    readings = ensemble(all_values, tile_ids, samples)
    sanity_checks(readings)
    return ReadingRun(image=str(path), model=provider.model_info.to_dict(), samples=samples, tiles=len(boxes),
                      readings=readings, raw_outputs=raws, errors=errors, latency_s=time.perf_counter() - t0)


def describe_map(path: str | Path, provider: LLMProvider, title: str, measure: str, areas: list[str],
                 max_side: int = 1792) -> dict:
    req = GenerationRequest(
        task="describe_map",
        messages=[Message("system", "Je beschrijft kaarten feitelijk en voorzichtig."),
                  Message("user", DESCRIBE_PROMPT.format(title=title, measure=measure, areas=", ".join(areas) or "-"))],
        temperature=0.2, max_output_tokens=1500, json_schema=OBSERVATION_SCHEMA, images=[image_bytes(path, max_side)],
    )
    res = provider.generate(req)
    obs = res.parsed.get("observations", []) if isinstance(res.parsed, dict) else []
    return {"image": str(path), "model": provider.model_info.to_dict(), "observations": obs,
            "raw_output": res.text, "error": res.error, "latency_s": res.latency_s}
