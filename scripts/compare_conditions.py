"""Stage 1 comparison of the counted runs, by the rules of hypotheses-and-thresholds.md (sections 3 and 4).

    python scripts/compare_conditions.py --workspace workspace --freeze freeze-1 --out workspace/stage1-results.md

Reads only stored records: the verifier status of every claim (U) and the labels and numbers of every
statement of the original (S, number measures). Runs no model and changes no run record. Writes
condition names and numbers only, no research data. Refuses to compare when a run is missing, double,
dirty, not CPU-only, not cold or from another commit.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ingestion.models import THEMES  # noqa: E402

MMD = 0.05
RESAMPLES = 2000
BOOTSTRAP_SEED = 20261126
CANDIDATES = ("E1", "E2", "E4")                # E1R is a reference, never a candidate
CONDITIONS = (*CANDIDATES, "E1R")
SEEDS = (42, 43, 44)
CREDIT = {"covered": 1.0, "partial": 0.5, "missing": 0.0}
EPS = 1e-9                                     # a difference of exactly 5 points counts as 5 points


class NotComparable(Exception):
    pass


# ------------------------------------------------------------------ reading the records

def load_runs(workspace: Path, case: str, conditions, seeds, commit: str | None, tag: str | None) -> dict:
    """{condition: {seed: run}}. commit: the only code version accepted (counted runs)."""
    found: dict = defaultdict(lambda: defaultdict(list))
    for p in sorted((workspace / "runs").glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        exp = rec["experiment"]["id"]
        if exp not in conditions or (rec.get("tag") or None) != tag:
            continue
        if commit and (rec.get("code_version") or "").removesuffix("-dirty") != commit:
            continue                           # untagged runs from another commit never count
        for res in rec["results"]:
            if res["case_id"] == case and res["seed"] in seeds:
                found[exp][res["seed"]].append((rec, res))
    problems: list[str] = []
    out: dict = {}
    for exp in conditions:
        for seed in seeds:
            runs = found[exp][seed]
            if len(runs) != 1:
                problems.append(f"{exp} seed {seed}: {len(runs)} runs instead of 1")
                continue
            rec, res = runs[0]
            rt = rec.get("runtime") or {}
            if commit and rec.get("code_version") != commit:
                problems.append(f"{exp} seed {seed}: code version is {rec.get('code_version')}")
            if not rt.get("cpu_only") or not rt.get("cold_start"):
                problems.append(f"{exp} seed {seed}: not a cold CPU-only run")
            if not res.get("report_run_id"):
                problems.append(f"{exp} seed {seed}: no report")
                continue
            out.setdefault(exp, {})[seed] = read_report(workspace, case, rec, res)
    if problems:
        raise NotComparable("; ".join(problems))
    reports = [r for by_seed in out.values() for r in by_seed.values()]
    if len({json.dumps(r["checksums"], sort_keys=True) for r in reports}) != 1:
        raise NotComparable("the runs used different source files")
    if commit and len({json.dumps({k: v for k, v in r["fingerprints"].items() if k != "prompts"}, sort_keys=True)
                       for r in reports}) != 1:
        raise NotComparable("the runs used different configuration files")
    if len({tuple((s["section"], s["index"], s["supportable"], s["in_reference"]) for s in r["statements"])
            for r in reports}) != 1:
        raise NotComparable("the runs were scored against different statements or another ceiling")
    return out


def read_report(workspace: Path, case: str, rec: dict, res: dict) -> dict:
    d = workspace / "reports" / case / res["report_run_id"]
    claims = []
    for f in sorted((d / "drafts").glob("*.json")):
        draft = json.loads(f.read_text(encoding="utf-8"))
        claims += [(draft["section"], (c.get("verification") or {}).get("status")) for c in draft["claims"]]
    statements: list[dict] = []
    n: dict = defaultdict(int)
    with open(d / "evaluation" / "alignment.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["section"] == "aanbevelingen":                      # excluded by design
                continue
            ref, gen = set(row["numbers_reference"].split()), set(row["numbers_generated"].split())
            statements.append({"section": row["section"], "index": n[row["section"]],
                               "credit": CREDIT[row["label"]], "supportable": row["evidence_label"] != "missing",
                               "in_reference": len(ref), "found_back": len(ref & gen),
                               "conflict": row["numbers_differ"] == "True"})
            n[row["section"]] += 1
    return {"run_id": rec["run_id"], "claims": claims, "statements": statements, "totals": res["totals"],
            "failed_sections": res.get("failed_sections") or [], "fingerprints": rec["fingerprints"],
            "checksums": rec["dataset_checksums"]}


# ------------------------------------------------------------------ measures

def pooled_claims(runs: dict, sections=None) -> dict[str, list[int]]:
    """Claims of all seeds together, per section: 1 = unsupported."""
    out = defaultdict(list)
    for r in runs.values():
        for sec, status in r["claims"]:
            if sections is None or sec in sections:
                out[sec].append(int(status == "unsupported"))
    return dict(out)


def rate(strata: dict) -> float | None:
    n = sum(len(v) for v in strata.values())
    return sum(map(sum, strata.values())) / n if n else None


def pooled_statements(runs: dict, sections=None) -> dict[str, list[tuple]]:
    """Per chapter, per statement of the original: (credit, supportable, found back, numbers in the original,
    conflicts), each summed over the seeds. Credit counts only for statements the data can support."""
    k = len(runs)
    out = defaultdict(list)
    for rows in zip(*(r["statements"] for r in runs.values())):
        s = rows[0]
        if sections is None or s["section"] in sections:
            out[s["section"]].append((sum(x["credit"] for x in rows) * s["supportable"], k * s["supportable"],
                                      sum(x["found_back"] for x in rows), k * s["in_reference"],
                                      sum(x["conflict"] for x in rows)))
    return dict(out)


def ratio(strata: dict, num: int, den: int) -> float | None:
    d = sum(r[den] for v in strata.values() for r in v)
    return sum(r[num] for v in strata.values() for r in v) / d if d else None


def s_rate(strata: dict) -> float | None:
    return ratio(strata, 0, 1)


def found_rate(strata: dict) -> float | None:
    return ratio(strata, 2, 3)


def conflicts(strata: dict) -> int:
    return sum(r[4] for v in strata.values() for r in v)


# ------------------------------------------------------------------ bootstrap and the decision rule

def interval(values: list[float]) -> tuple[float, float]:
    v = sorted(values)
    return v[round(0.025 * (len(v) - 1))], v[round(0.975 * (len(v) - 1))]


def bootstrap_u(x: dict, y: dict, n: int = RESAMPLES, seed: int = BOOTSTRAP_SEED) -> tuple[float, float]:
    """Claims are resampled within each section, separately per condition (they are different claims)."""
    rng = random.Random(seed)

    def draw(strata):
        return {k: rng.choices(v, k=len(v)) for k, v in sorted(strata.items())}
    return interval([rate(draw(x)) - rate(draw(y)) for _ in range(n)])


def bootstrap_statements(x: dict, y: dict, measure, n: int = RESAMPLES,
                         seed: int = BOOTSTRAP_SEED) -> tuple[float, float]:
    """The original's statements are resampled within each chapter; both conditions get the same draw."""
    rng = random.Random(seed)
    diffs = []
    for _ in range(n):
        bx, by = {}, {}
        for k in sorted(x):
            pick = rng.choices(range(len(x[k])), k=len(x[k]))
            bx[k], by[k] = [x[k][i] for i in pick], [y[k][i] for i in pick]
        a, b = measure(bx), measure(by)
        if a is not None and b is not None:
            diffs.append(a - b)
    return interval(diffs)


def verdict(diff: float, ci: tuple[float, float], lower_is_better: bool) -> str:
    """Section 3, for X minus Y."""
    if (ci[0] > 0 or ci[1] < 0) and abs(diff) >= MMD - EPS:
        return "better" if (diff < 0) == lower_is_better else "worse"
    if ci[0] >= -MMD - EPS and ci[1] <= MMD + EPS:
        return "no meaningful difference"
    return "inconclusive"


def stage1_winner(scores_by: dict) -> dict:
    """scores_by: {condition: {"U", "S", "minutes"}}. The rule of section 4, on the pooled values."""
    best_u = min(v["U"] for v in scores_by.values())
    eligible = [c for c, v in scores_by.items() if v["U"] - best_u <= MMD + EPS]
    top = max(eligible, key=lambda c: (scores_by[c]["S"], -scores_by[c]["minutes"]))
    tied = [c for c in eligible if abs(scores_by[c]["S"] - scores_by[top]["S"]) <= MMD + EPS
            and abs(scores_by[c]["U"] - scores_by[top]["U"]) <= MMD + EPS]
    return {"eligible": eligible, "highest_S": top, "tied": tied,
            "winner": min(tied, key=lambda c: scores_by[c]["minutes"])}


def compare(x: dict, y: dict, sections=None) -> dict:
    """X minus Y, pooled over the seeds."""
    ux, uy = pooled_claims(x, sections), pooled_claims(y, sections)
    sx, sy = pooled_statements(x, sections), pooled_statements(y, sections)
    out: dict = {"conflicts": {"x": conflicts(sx), "y": conflicts(sy)}}
    for name, a, b, ci, low in (
            ("U", rate(ux), rate(uy), lambda: bootstrap_u(ux, uy), True),
            ("S", s_rate(sx), s_rate(sy), lambda: bootstrap_statements(sx, sy, s_rate), False),
            ("found back", found_rate(sx), found_rate(sy), lambda: bootstrap_statements(sx, sy, found_rate), False)):
        if a is not None and b is not None:
            c = ci()
            out[name] = {"x": a, "y": b, "diff": a - b, "ci": c, "verdict": verdict(a - b, c, low)}
    return out


# ------------------------------------------------------------------ the report

def _pc(x) -> str:
    return "–" if x is None else f"{100 * x:.1f}"


def _yes(x: bool) -> str:
    return "yes" if x else "no"


def _share(num: float, den: float) -> float | None:
    return num / den if den else None


def scores(runs: dict, sections=None) -> dict:
    return {"U": rate(pooled_claims(runs, sections)), "S": s_rate(pooled_statements(runs, sections)),
            "minutes": statistics.mean(r["totals"]["wall_s"] for r in runs.values()) / 60}


def render(data: dict, title: str) -> str:
    out = [f"# {title}", "",
           "Numbers only. Rules: docs/experiments/hypotheses-and-thresholds.md, sections 3 and 4. "
           f"Bootstrap: {RESAMPLES} resamples, seed {BOOTSTRAP_SEED}. MMD: 5 percentage points (pp).", "",
           "## Runs", "",
           "| Condition | Seed | Run | Claims | U % | S % | Minutes | Peak model memory (GiB) | Failed sections |",
           "|---|---|---|---|---|---|---|---|---|"]
    for c, runs in data.items():
        for seed, r in runs.items():
            s, mem = scores({seed: r}), r["totals"].get("model_peak_mb")
            out.append(f"| {c} | {seed} | {r['run_id']} | {len(r['claims'])} | {_pc(s['U'])} | {_pc(s['S'])} | "
                       f"{s['minutes']:.1f} | {'–' if mem is None else round(mem / 1024, 1)} | "
                       f"{len(r['failed_sections'])} |")
    out += ["", "E1R has the six theme sections only: its S in these two tables is against the whole original and is "
            "not comparable with the others.", "", "## Pooled over the seeds", "",
            "| Condition | Claims | U % | Weak % | Grounded % | S % | Numbers found back | Number conflicts | "
            "Minutes (mean) |", "|---|---|---|---|---|---|---|---|---|"]
    for c, runs in data.items():
        st = [s for r in runs.values() for _, s in r["claims"]]
        sc, ps = scores(runs), pooled_statements(runs)
        flat = [r for v in ps.values() for r in v]
        out.append(f"| {c} | {len(st)} | {_pc(sc['U'])} | {_pc(_share(st.count('weak'), len(st)))} | "
                   f"{_pc(_share(st.count('grounded'), len(st)))} | {_pc(sc['S'])} | "
                   f"{sum(r[2] for r in flat)} of {sum(r[3] for r in flat)} | {conflicts(ps)} | {sc['minutes']:.1f} |")
    if "E4" in data:
        totals = [r["totals"] for r in data["E4"].values()]

        def tot(k):
            return sum(t.get(k) or 0 for t in totals)
        before = tot("revision_claims_before")
        out += ["", "## E4: the checker step (D4, D6)", "",
                f"Over {len(totals)} reports: {tot('revision_sent_back')} claims sent back, {tot('revision_revised')} "
                f"revised, {tot('revision_withdrawn')} withdrawn ({_pc(_share(tot('revision_withdrawn'), before))}% of "
                f"all claims), {tot('revision_still_unsupported')} revised but still unsupported. U before revision: "
                f"{_pc(_share(tot('revision_unsupported_before'), before))}%. Extra: {tot('revision_calls')} calls, "
                f"{tot('revision_minutes'):.1f} minutes."]
    out += ["", "## Differences (X minus Y, pp, with 95% interval)", "",
            "| Comparison | Sections | Measure | X | Y | Difference | 95% interval | Verdict for X |",
            "|---|---|---|---|---|---|---|---|"]
    cmp = {}
    for hyp, x, y, secs in ((" (H2)", "E2", "E1", None), (" (H3)", "E4", "E2", None), ("", "E4", "E1", None),
                            (" (H1)", "E1", "E1R", set(THEMES))):
        if x not in data or y not in data:
            continue
        c = cmp[(x, y)] = compare(data[x], data[y], secs)
        head = f"| {x} − {y}{hyp} | {'six themes' if secs else 'all'}"
        for m in ("U", "S", "found back"):
            if m in c:
                r = c[m]
                out.append(f"{head} | {m} | {_pc(r['x'])} | {_pc(r['y'])} | {100 * r['diff']:+.1f} | "
                           f"{100 * r['ci'][0]:+.1f} to {100 * r['ci'][1]:+.1f} | {r['verdict']} |")
        k = c["conflicts"]
        out.append(f"{head} | number conflicts (count) | {k['x']} | {k['y']} | {k['x'] - k['y']:+d} | – | – |")
    out += ["", "## Hypotheses", ""]
    if ("E1", "E1R") in cmp:
        c = cmp[("E1", "E1R")]
        k, f = c["conflicts"], c.get("found back")
        out.append(f"- **H1** (decided by the number measures, R5; six themes): number conflicts lower: "
                   f"{_yes(k['x'] < k['y'])} ({k['x']} against {k['y']}); numbers found back higher: "
                   + (f"{_yes(f['diff'] > 0)} ({f['verdict']})" if f else "no numbers")
                   + ". U is secondary and biased against E1R.")
    if ("E2", "E1") in cmp:
        c = cmp[("E2", "E1")]
        u_ok = c["U"]["diff"] <= MMD + EPS
        out.append(f"- **H2**: S {c['S']['verdict']}; U not worse by more than the MMD: {_yes(u_ok)}. "
                   f"Supported: {_yes(c['S']['verdict'] == 'better' and u_ok)}.")
    if ("E4", "E2") in cmp:
        c = cmp[("E4", "E2")]
        s_ok = c["S"]["diff"] >= -MMD - EPS
        out.append(f"- **H3**: U {c['U']['verdict']}; S not worse by more than the MMD: {_yes(s_ok)}. Automatic part: "
                   f"{'met' if c['U']['verdict'] == 'better' and s_ok else 'not met'}. H3 also needs the blind human "
                   "support check (D4), so this is not the final verdict.")
    if all(c in data for c in CANDIDATES):
        w = stage1_winner({c: scores(data[c]) for c in CANDIDATES})
        out += ["", "## Stage 1 winner by the pre-registered rule", "",
                f"- U within the MMD of the lowest U: {', '.join(w['eligible'])}",
                f"- highest S among these: {w['highest_S']}; within the MMD of it on both U and S: {', '.join(w['tied'])}",
                f"- **winner: {w['winner']}**" + (" (the fastest of the tied conditions)" if len(w["tied"]) > 1 else "")]
    out += ["", "S uses the word-overlap scorer (kappa 0.38 on developer labels): provisional until a scorer reaches "
            "0.61 (section 6). One case and three seeds: the intervals describe this report, not other neighbourhoods.",
            ""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default="workspace")
    ap.add_argument("--case", default="euterpepark-oss")
    ap.add_argument("--freeze", help="git tag or commit the counted runs must come from")
    ap.add_argument("--tag", help="compare runs with this tag instead (never counted)")
    ap.add_argument("--conditions", nargs="+", default=list(CONDITIONS))
    ap.add_argument("--seeds", nargs="+", type=int, default=list(SEEDS))
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if bool(a.freeze) == bool(a.tag):
        ap.error("give --freeze (counted runs) or --tag (a try-out)")
    commit = None
    if a.freeze:
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", f"{a.freeze}^{{commit}}"],
                                capture_output=True, text=True, check=True).stdout.strip()
    try:
        data = load_runs(Path(a.workspace), a.case, a.conditions, a.seeds, commit, a.tag)
    except NotComparable as exc:
        print(f"Not compared: {exc}", file=sys.stderr)
        return 2
    md = render(data, f"Stage 1 results, commit {commit}" if commit else f"NOT COUNTED: runs tagged {a.tag}")
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
