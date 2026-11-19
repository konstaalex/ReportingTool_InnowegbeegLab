"""Versioned prompt templates (prompts/<version>/*.txt, string.Template syntax).

Prompts are files, not code, so a run record can store (version, sha) and a
prompt change is visible when comparing experiments (brief section 16:
freeze prompts during a comparison or document every change).
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from string import Template

from ingestion.config import REPO_ROOT, load_config

PROMPTS_DIR = REPO_ROOT / "prompts"


@lru_cache(maxsize=None)
def _read(version: str, name: str) -> str:
    return (PROMPTS_DIR / version / f"{name}.txt").read_text(encoding="utf-8")


def render(version: str, name: str, **values) -> str:
    return Template(_read(version, name)).substitute(**values)


def prompt_sha(version: str) -> str:
    h = hashlib.sha256()
    for p in sorted((PROMPTS_DIR / version).glob("*.txt")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def writing_rules_text() -> str:
    rules = load_config("reference/writing_rules.toml")
    lines = [f"- {r['text']} ({r['id']})" for r in rules["rule"]]
    return "\n".join(lines)


# Most evidence ids one claim may cite, per prompt version (P7, docs/experiments/week1-pilot.md):
# on the CPU the model sometimes kept numbering invented ids until the output limit. From v3 the
# cap is in the prompt and in the JSON schema (maxItems), so a grammar-constrained provider such
# as Ollama cannot write more. v1/v2 have no cap, so runs recorded under them stay reproducible.
MAX_EVIDENCE_IDS = 6
FIRST_CAPPED_VERSION = 3


def evidence_id_cap(version: str) -> int | None:
    """The cap for prompt v3 and later; None for v1/v2 (and for names that are not vN)."""
    try:
        return MAX_EVIDENCE_IDS if int(version.lstrip("v")) >= FIRST_CAPPED_VERSION else None
    except ValueError:
        return None


def system_prompt(version: str) -> str:
    rules = load_config("reference/writing_rules.toml")
    return render(version, "system", audience=rules["audience"], writing_rules=writing_rules_text(),
                  max_ids=evidence_id_cap(version) or "")
