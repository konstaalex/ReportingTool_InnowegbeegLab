"""Summarise the pilot run records into a table and charts.

Reads the experiment run records in ``workspace/runs/*.json`` (git-ignored, they
stay on the machine) and writes only aggregate numbers:

    docs/experiments/pilot-runs-summary.csv
    docs/experiments/figures/pilot-*.png

No text from drafts, source data or the original report is written: the CSV
holds counts, rates, times and memory figures only (NFR7).

Usage (from the repository root):

    python scripts/pilot_summary.py
    python scripts/pilot_summary.py --tags pilot-week1 pilot-week1b smoke-models
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "workspace" / "runs"
OUT_CSV = ROOT / "docs" / "experiments" / "pilot-runs-summary.csv"
FIG_DIR = ROOT / "docs" / "experiments" / "figures"

DEFAULT_TAGS = ("pilot-week1", "pilot-week1b", "smoke-models")
ROUND = {"pilot-week1": "1", "pilot-week1b": "2", "smoke-models": "M"}
GIB = 1024.0  # model_peak_mb is in MiB; report GB of 1024^3 bytes like the 9.6 GB limit
MEMORY_LIMIT_GB = 9.6

FIELDS = [
    "run_id", "tag", "round", "experiment", "pipeline", "model", "device", "seeds",
    "code_version", "minutes", "model_calls", "tokens_in", "tokens_out",
    "output_tokens_per_s", "model_peak_gb", "claims", "grounded_rate", "weak_rate",
    "unsupported_rate", "coverage", "ceiling", "coverage_of_supportable",
    "numbers_agree_rate", "failed_sections",
]


def _mean(summary: dict, key: str):
    value = summary.get(key)
    if isinstance(value, dict):
        return value.get("mean")
    return value


def load_rows(tags: tuple[str, ...]) -> list[dict]:
    rows = []
    for path in sorted(RUNS.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        tag = record.get("tag")
        if tag not in tags:
            continue
        s = record.get("summary", {})
        results = record.get("results", [])
        cpu_only = any((r.get("resources") or {}).get("cpu_only") for r in results) or (
            record.get("runtime") or {}).get("cpu_only")
        experiment = record.get("experiment") or {}
        failed = sorted({sec for r in results for sec in r.get("failed_sections", [])})
        peak = _mean(s, "model_peak_mb")
        rows.append({
            "run_id": record.get("run_id", path.stem),
            "tag": tag,
            "round": ROUND.get(tag, ""),
            "experiment": experiment.get("id", ""),
            "pipeline": experiment.get("pipeline", ""),
            "model": (record.get("model") or {}).get("model", "none (template)"),
            "device": "CPU only" if cpu_only else ("GPU" if record.get("model") else "-"),
            "seeds": len(results),
            "code_version": record.get("code_version", ""),
            "minutes": round((record.get("total_wall_s") or 0) / 60, 1),
            "model_calls": _mean(s, "model_calls"),
            "tokens_in": _mean(s, "tokens_in"),
            "tokens_out": _mean(s, "tokens_out"),
            "output_tokens_per_s": _mean(s, "output_tokens_per_s"),
            "model_peak_gb": round(peak / GIB, 2) if peak else "",
            "claims": _mean(s, "n_claims"),
            "grounded_rate": _mean(s, "grounded_rate"),
            "weak_rate": _mean(s, "weak_rate"),
            "unsupported_rate": _mean(s, "unsupported_rate"),
            "coverage": _mean(s, "coverage"),
            "ceiling": _mean(s, "ceiling"),
            "coverage_of_supportable": _mean(s, "coverage_of_supportable"),
            "numbers_agree_rate": _mean(s, "numbers_agree_rate"),
            "failed_sections": "; ".join(failed),
        })
    return rows


def write_csv(rows: list[dict]) -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _label(row: dict) -> str:
    names = {"template": "E0 template", "naive": "E1 single", "staged": "E2 staged"}
    name = names.get(row["pipeline"], row["experiment"])
    if row["device"] == "CPU only":
        name += " (CPU)"
    return name


def charts(rows: list[dict]) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:  # charts are optional; the CSV is the record
        print("matplotlib not installed: CSV written, charts skipped")
        return
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    ink, muted, grid = "#1d2733", "#55657a", "#d9dee5"
    colours = {"1": "#9db4cf", "2": "#1f3a5f"}
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": muted, "axes.labelcolor": ink,
                         "xtick.color": ink, "ytick.color": ink})

    report_rows = [r for r in rows if r["round"] in ("1", "2")]
    order = ["E0 template", "E1 single", "E2 staged", "E2 staged (CPU)"]

    def by_round(metric: str, scale: float = 1.0):
        out = {}
        for rnd in ("1", "2"):
            vals = []
            for name in order:
                match = [r for r in report_rows if r["round"] == rnd and _label(r) == name]
                v = match[0][metric] if match and match[0][metric] not in ("", None) else None
                vals.append(None if v is None else float(v) * scale)
            out[rnd] = vals
        return out

    def grouped(metric, scale, ylabel, title, fname, fmt="{:.0f}", ymax=None):
        data = by_round(metric, scale)
        fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=200)
        width = 0.38
        for i, rnd in enumerate(("1", "2")):
            xs = [k + (i - 0.5) * width for k in range(len(order))]
            vals = [v if v is not None else 0 for v in data[rnd]]
            bars = ax.bar(xs, vals, width, color=colours[rnd],
                          label=f"Round {rnd} ({'3 Nov' if rnd == '1' else '4 Nov'})")
            for b, v in zip(bars, data[rnd]):
                if v is not None:
                    ax.text(b.get_x() + b.get_width() / 2, b.get_height(), fmt.format(v),
                            ha="center", va="bottom", fontsize=8, color=ink)
        ax.set_xticks(range(len(order)), order)
        ax.set_ylabel(ylabel)
        ax.set_title(title, loc="left", fontsize=11, color=ink)
        ax.grid(axis="y", color=grid, linewidth=0.6)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        if ymax:
            ax.set_ylim(0, ymax)
        ax.legend(frameon=False, fontsize=8)
        fig.tight_layout()
        fig.savefig(FIG_DIR / fname)
        plt.close(fig)

    grouped("unsupported_rate", 100, "% of claims", "Unsupported claims (lower is better)",
            "pilot-unsupported.png", ymax=32)
    grouped("coverage_of_supportable", 100, "% of supportable statements",
            "Coverage of what the data supports (higher is better)",
            "pilot-coverage.png", ymax=34)

    # time per full report and peak model memory (round 2 only, model runs)
    r2 = [r for r in report_rows if r["round"] == "2" and r["pipeline"] != "template"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.2), dpi=200)
    names = [_label(r) for r in r2]
    a1.barh(names, [r["minutes"] for r in r2], color="#1f3a5f")
    for y, r in enumerate(r2):
        a1.text(r["minutes"], y, f" {r['minutes']:.0f} min", va="center", fontsize=8, color=ink)
    a1.set_xlim(0, 100)
    a1.set_title("Time per full report (round 2)", loc="left", fontsize=10, color=ink)
    a1.invert_yaxis()
    a2.barh(names, [r["model_peak_gb"] for r in r2], color="#2f6b3f")
    a2.axvline(MEMORY_LIMIT_GB, color="#b07d10", linestyle="--", linewidth=1)
    a2.text(MEMORY_LIMIT_GB, -0.6, " limit 9.6 GB", color="#b07d10", fontsize=8, va="center")
    for y, r in enumerate(r2):
        a2.text(r["model_peak_gb"], y, f" {r['model_peak_gb']:.1f} GB", va="center", fontsize=8,
                color=ink)
    a2.set_xlim(0, 12)
    a2.set_yticks([])
    a2.set_title("Peak model memory", loc="left", fontsize=10, color=ink)
    a2.invert_yaxis()
    for ax in (a1, a2):
        ax.grid(axis="x", color=grid, linewidth=0.6)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "pilot-time-memory.png")
    plt.close(fig)
    print(f"charts written to {FIG_DIR.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tags", nargs="+", default=list(DEFAULT_TAGS))
    args = parser.parse_args()
    rows = load_rows(tuple(args.tags))
    if not rows:
        raise SystemExit(f"no run records with tags {args.tags} in {RUNS}")
    write_csv(rows)
    print(f"{len(rows)} runs -> {OUT_CSV.relative_to(ROOT)}")
    charts(rows)


if __name__ == "__main__":
    main()
