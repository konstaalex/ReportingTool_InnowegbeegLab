"""Locate and load the TOML configuration (stdlib tomllib, Python >= 3.11).

Config lives in <repo>/config by default; set INNO_CONFIG_DIR to point a run
at a different (e.g. frozen, versioned) copy for an experiment.
"""
from __future__ import annotations

import hashlib
import os
import tomllib
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return Path(os.environ.get("INNO_CONFIG_DIR", REPO_ROOT / "config"))


@lru_cache(maxsize=None)
def _load(path: str) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def load_config(relative: str) -> dict:
    return _load(str(config_dir() / relative))


def config_fingerprint(relative: str) -> str:
    """sha256 (first 12 chars) of a config file, recorded in run records so a
    changed mapping or rule set is visible when comparing experiments."""
    data = (config_dir() / relative).read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]
