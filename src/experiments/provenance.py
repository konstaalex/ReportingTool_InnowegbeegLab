"""What a run record needs to be reproducible: code version and config/prompt fingerprints.

Shared by experiment runs (experiments.runner) and full-report runs (reports.runs), so both
kinds of record can be checked against the freeze (docs/experiments/hypotheses-and-thresholds.md).
"""
from __future__ import annotations

import os
import subprocess
from typing import Optional

from drafting.prompts import prompt_sha
from ingestion.config import REPO_ROOT, config_fingerprint


# What can change a run's output (P8, docs/experiments/week1-pilot.md). An uncommitted change
# here marks the run "-dirty"; edits elsewhere (docs, tests, helper scripts) do not, because
# they cannot change what a run produces. Paths are relative to the repository root.
RUN_RELEVANT = ("src/", "prompts/", "config/", "requirements.txt")


def changed_paths(porcelain_z: str) -> list[str]:
    """Paths from `git status --porcelain=v1 -z` (renames give the new and the old path)."""
    out, fields, i = [], porcelain_z.split("\0"), 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        out.append(entry[3:])
        if entry[0] in "RC" and i < len(fields):      # rename/copy: the next field is the old path
            out.append(fields[i])
            i += 1
    return out


def run_relevant(paths: list[str]) -> list[str]:
    return [p for p in paths if p.replace("\\", "/").startswith(RUN_RELEVANT)]


def git_commit() -> Optional[str]:
    """Short commit hash, with '-dirty' when code, prompts or configuration have uncommitted
    changes (RUN_RELEVANT). Documentation edits during a run do not make it dirty."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}      # read-only: never take .git/index.lock during a run
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5, env=env)
        status = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain=v1", "-z"],
                                capture_output=True, text=True, timeout=5, env=env).stdout
        dirty = run_relevant(changed_paths(status))
        return (out.stdout.strip() + ("-dirty" if dirty else "")) or None
    except Exception:  # noqa: BLE001
        return None


def fingerprints(prompt_version: str) -> dict:
    return {
        "prompts": prompt_sha(prompt_version),
        "survey_mapping": config_fingerprint("survey_mapping.toml"),
        "writing_rules": config_fingerprint("reference/writing_rules.toml"),
        "rivm_rules": config_fingerprint("reference/rivm_vuistregels.toml"),
        "models": config_fingerprint("models.toml"),
        "experiments": config_fingerprint("experiments.toml"),
    }
