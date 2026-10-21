"""Aggregates RunResults into mean +/- stdev per condition, and keeps a
dated, append-only JSON log of every run -- the "logged runs" half of the
Inspect AI / DeepEval pattern (frozen test set + automated scoring +
logged runs) the Resources doc cites as precedent for this architecture.
Logging every run, not just the latest, is what lets a later comparison
ask "did switching models actually help?" instead of trusting one memory.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .models import RunResult

METRIC_NAMES = ("faithfulness", "citation_precision", "citation_recall",
                "fabrication_rate", "misgrounding_rate", "injected_error_catch_rate")


def summarize(results: list[RunResult]) -> dict[str, dict[str, dict[str, float]]]:
    """{condition: {metric: {"mean": ..., "stdev": ..., "n": ...}}}.
    stdev is 0.0 (not an error) when a condition has only one repeat --
    callers that care whether repeats matter should check "n" rather than
    assume stdev == 0.0 means "no variance"."""
    by_condition: dict[str, list[RunResult]] = {}
    for r in results:
        by_condition.setdefault(r.condition, []).append(r)

    summary: dict[str, dict[str, dict[str, float]]] = {}
    for condition, runs in by_condition.items():
        summary[condition] = {}
        for metric in METRIC_NAMES:
            values = [run.scores[metric] for run in runs if metric in run.scores]
            if not values:
                continue
            summary[condition][metric] = {
                "mean": statistics.mean(values),
                "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
                "n": len(values),
            }
    return summary


def append_run_log(path: str | Path, results: list[RunResult], label: str | None = None) -> None:
    """Appends one timestamped run (all its RunResults + their summary) to
    a JSON log file, creating it if it doesn't exist yet. Never overwrites
    earlier runs -- the log is the point."""
    path = Path(path)
    log = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "results": [asdict(r) for r in results],
        "summary": summarize(results),
    }
    log.append(entry)
    path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")


def load_run_log(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
