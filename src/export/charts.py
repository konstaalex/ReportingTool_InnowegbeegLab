"""The report's bar charts ('Figuur n'), drawn from survey evidence.

Every bar is a count or percentage that already exists in an evidence item
(tally per respondent group), so a chart is traceable like a sentence:
ChartData.evidence_ids lists the items, and the renderer puts them in the
evidence map. Chart definitions live in config/report_structure.toml ([[chart]]).

Style follows the Wijkscan report: grouped bars, first group in the house
orange and second in black, the value above every bar, no y-axis, the title
in bold orange, the legend underneath.
"""
from __future__ import annotations

import re
import textwrap
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from evidence.models import Evidence

SERIES_COLOURS = ["{accent}", "#000000", "#9F9F9F", "#5F5F5F"]


@dataclass
class ChartData:
    chart_id: str
    title: str
    categories: list[str]
    series: list[tuple[str, list[Optional[float]]]]      # (group label, value per category)
    measure: str                                         # count | pct
    evidence_ids: list[str] = field(default_factory=list)
    missing_options: list[str] = field(default_factory=list)
    n: dict[str, int] = field(default_factory=dict)      # group label -> respondents in the group

    @property
    def empty(self) -> bool:
        return not self.series or all(v is None for _, vals in self.series for v in vals)


def _norm(s: str) -> str:
    s = re.sub(r"^\s*\d+\s*[-.)]\s*", "", str(s))          # "1 - Ja" -> "Ja"
    return " ".join(s.lower().split())


def _matches(answer: str, match: str) -> bool:
    return _norm(answer).startswith(_norm(match))


def chart_data(spec: dict, evidence: Iterable[Evidence], group_labels: Optional[dict] = None,
               fmt: Optional[dict] = None) -> ChartData:
    """Collect one chart's values from the evidence items of its survey question."""
    group_labels = group_labels or {}
    fmt = fmt or {}
    q = spec["question"]
    per_group: "OrderedDict[str, dict]" = OrderedDict()
    for e in evidence:
        if e.data.get("question_id") != q or e.perspective != "bewoners":
            continue
        g = e.data.get("group") or "Bewoners"
        slot = per_group.setdefault(g, {"counts": {}, "n": None, "ids": []})
        if e.kind == "option_share":
            slot["counts"][e.data["option"]] = e.data["k"]
            slot["n"] = e.data["n"]
        elif e.kind in ("choice", "multi"):
            slot["counts"].update(e.data["counts"])
            slot["n"] = e.data["n"]
        else:
            continue
        slot["ids"].append(e.evidence_id)
    order = [g for g in group_labels if g in per_group] + [g for g in per_group if g not in group_labels]
    categories = [label for _, label in spec["options"]]
    series, ids, n = [], [], {}
    seen_option = {match: False for match, _ in spec["options"]}
    for g in order:
        slot = per_group[g]
        if not slot["ids"]:
            continue
        values: list[Optional[float]] = []
        for match, _label in spec["options"]:
            k = sum(v for ans, v in slot["counts"].items() if _matches(ans, match))
            hit = any(_matches(ans, match) for ans in slot["counts"])
            seen_option[match] = seen_option[match] or hit
            if spec.get("measure", "count") == "pct":
                values.append(round(100 * k / slot["n"]) if slot["n"] else None)
            else:
                values.append(k)
        label = group_labels.get(g, g)
        series.append((label, values))
        n[label] = slot["n"] or 0
        ids += slot["ids"]
    return ChartData(spec["id"], spec["title"].format(**fmt) if fmt else spec["title"], categories, series,
                     spec.get("measure", "count"), list(dict.fromkeys(ids)),
                     [m for m, ok in seen_option.items() if not ok], n)


def render_chart(data: ChartData, path: str | Path, accent: str = "#FE5C01", font: str = "Calibri",
                 vertical_labels: bool = False, size: tuple[float, float] = (6.4, 3.4), dpi: int = 220) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    family = next((f for f in (font, "Carlito", "Arial", "Liberation Sans", "DejaVu Sans") if f in available), "DejaVu Sans")
    colours = [c.format(accent=accent) for c in SERIES_COLOURS]
    k = max(len(data.series), 1)
    group_width = 0.55 if len(data.categories) <= 3 else 0.8      # few categories: slimmer bars, as in the report
    width = group_width / (k + 0.4)
    fig, ax = plt.subplots(figsize=size, dpi=dpi)
    xs = list(range(len(data.categories)))
    top = max([v for _, vals in data.series for v in vals if v is not None] or [1])
    for i, (label, vals) in enumerate(data.series):
        offs = [x + (i - (k - 1) / 2) * width for x in xs]
        heights = [v or 0 for v in vals]
        ax.bar(offs, heights, width=width, color=colours[i % len(colours)], label=label, zorder=2)
        for x, v in zip(offs, vals):
            if v is None:
                continue
            ax.text(x, (v or 0) + top * 0.03, f"{int(v)}%" if data.measure == "pct" else f"{int(v)}",
                    ha="center", va="bottom", fontsize=8, family=family, color="#000000")
    ax.set_ylim(0, top * 1.22)
    ax.set_xticks(xs)
    def wrap(c: str, n: int) -> str:
        return "\n".join(textwrap.wrap(c, n, break_long_words=False, break_on_hyphens=False))

    if vertical_labels:
        ax.set_xticklabels([wrap(c, 18) for c in data.categories], rotation=90, fontsize=8, family=family)
    else:
        ax.set_xticklabels([wrap(c, 16) for c in data.categories], fontsize=8, family=family)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#D9D9D9")
    ax.tick_params(axis="x", length=0)
    ax.set_yticks([])
    ax.set_title("\n".join(textwrap.wrap(data.title, 48)), color=accent, fontsize=11, fontweight="bold", family=family,
                 pad=10)
    fig.tight_layout()
    # legend underneath the (possibly multi-line or vertical) category labels, whatever their height
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    label_bottom = min((t.get_window_extent(renderer).y0 for t in ax.get_xticklabels()), default=0)
    y = fig.transFigure.inverted().transform((0, label_bottom))[1]
    fig.legend(*ax.get_legend_handles_labels(), loc="upper center", bbox_to_anchor=(0.5, y - 0.02),
               ncol=len(data.series), frameon=False, prop={"family": family, "size": 8}, handlelength=0.8)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path
