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


def git_commit() -> Optional[str]:
    """Short commit hash, with '-dirty' when the working tree has uncommitted changes."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}      # read-only: never take .git/index.lock during a run
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5, env=env)
        dirty = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=5, env=env).stdout.strip()
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
