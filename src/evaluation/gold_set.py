"""The frozen gold set the harness scores every run against.

Mirrors the "frozen test set + automated scoring + logged runs" pattern
used by Inspect AI and DeepEval (see the Resources doc) -- the whole point
of freezing it is that condition A and condition B are always compared on
the exact same cases, repeat after repeat, run after run.

GOLD_SET here is synthetic, not drawn from the real Wijkscan data: no UC2
draft generator exists yet, so there is nothing real to grade. Each
GoldClaim stands in for "a claim UC2 should eventually be able to make",
with supporting_ids referencing SourceRecord ids that would exist in the
real store. TODO once UC2 exists: swap this for `load_gold_set` against a
small held-out real slice -- this module's shape won't change to allow
that, only the source of the data.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import GoldClaim

# 8 synthetic gold claims spanning the 6 real THEMES (ingestion.models),
# modelled on the shape a drafted report sentence will need to take: one
# factual statement, grounded in 1-2 SourceRecord ids, within one theme.
GOLD_SET: tuple[GoldClaim, ...] = (
    GoldClaim(id="g1", text="Residents most often cite a lack of sports fields in Testbuurt.",
              supporting_ids=("src-001", "src-002"), theme="Sporten"),
    GoldClaim(id="g2", text="The expert walkthrough rated walking/cycling paths a 3.4 average in Testbuurt.",
              supporting_ids=("src-010",), theme="Actieve mobiliteit"),
    GoldClaim(id="g3", text="A need for more formal play equipment was raised in Testbuurt.",
              supporting_ids=("src-020",), theme="Formeel spelen"),
    GoldClaim(id="g4", text="Informal play space is reported as sufficient in Testbuurt.",
              supporting_ids=("src-030", "src-031"), theme="Informeel spelen"),
    GoldClaim(id="g5", text="Residents value a central meeting bench near the Urban Ox park.",
              supporting_ids=("src-040",), theme="Ontmoeten"),
    GoldClaim(id="g6", text="The neighbourhood's visual upkeep scored lowest of the six themes.",
              supporting_ids=("src-050", "src-051"), theme="Uitstraling"),
    GoldClaim(id="g7", text="Two neighbourhoods rated sports provision very differently (3.0 vs 4.6).",
              supporting_ids=("src-060", "src-061"), theme="Sporten"),
    GoldClaim(id="g8", text="The Sport- en speelkaart map shows a cluster of informal play sites.",
              supporting_ids=("src-070",), theme="Informeel spelen"),
)


def load_gold_set(path: str | Path | None = None) -> tuple[GoldClaim, ...]:
    """Returns the frozen built-in gold set, or loads one from a JSON file
    (a list of GoldClaim-shaped dicts) when `path` is given -- the hook for
    swapping in a real held-out slice later without touching callers."""
    if path is None:
        return GOLD_SET
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(
        GoldClaim(id=d["id"], text=d["text"], supporting_ids=tuple(d["supporting_ids"]), theme=d.get("theme"))
        for d in data
    )
