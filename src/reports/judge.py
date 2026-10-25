"""Optional LLM judge for the comparison with the original report.

Word overlap (reports.evaluate.Matcher) finds the candidate sentences quickly and
deterministically, but it judges paraphrases poorly. On a sample of 89 statements
from the Oss report, labelled by the developer, it agreed with the labels at
Cohen's kappa ~0.35. The judge asks a model to decide, for each statement of the
original, whether it is in the few candidate sentences the overlap found.

- The judge sees ONLY one statement of the original plus at most 8 candidate
  sentences. It never drafts anything, and nothing it says reaches a report.
- The original report is confidential ("NIET OPENBAAR"), so a hosted judge model is
  refused unless allow_hosted=True is passed explicitly.
- The answers are cached per case and judge model (reports/<case>/judge/<model>.json).
  A rerun gives the same labels, and the judgements of the evidence (the ceiling)
  are made once per case, not once per run.
- Calibrate the judge like the overlap measure: fill in human_label in
  alignment.csv and run `python -m reports.cli calibrate`. It reports kappa for both.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from llm.base import GenerationRequest, Message, PrivacyViolation
from llm.providers import extract_json

LABELS = ("covered", "partial", "missing")
SYSTEM = ("Je bent een zorgvuldige beoordelaar van onderzoeksrapporten. Je vergelijkt één uitspraak uit een "
          "origineel rapport met een paar genummerde zinnen. Antwoord alleen met JSON.")
SOURCES = {"report": "Zinnen uit het gegenereerde concept (zelfde hoofdstuk)",
           "evidence": "Bewijsstukken uit de data van de casus"}
INSTRUCTION = """Beoordeel of de uitspraak in deze zinnen staat:
- covered: de kern van de uitspraak (wat, wie, waar, hoeveel) staat erin, eventueel verdeeld over meerdere zinnen;
- partial: een deel staat erin, of het staat er algemener (zonder plek, groep of getal);
- missing: het staat er niet in, of alleen hetzelfde onderwerp zonder dezelfde bewering.
Zegt een zin hetzelfde maar met een ander getal, kies dan partial en zet conflict op true.
Antwoord als JSON: {"label": "covered" | "partial" | "missing", "zinnen": [nummers], "conflict": true | false, "reden": "hooguit 15 woorden"}"""
SCHEMA = {"type": "object", "properties": {
    "label": {"type": "string", "enum": list(LABELS)}, "zinnen": {"type": "array", "items": {"type": "integer"}},
    "conflict": {"type": "boolean"}, "reden": {"type": "string"}}, "required": ["label"]}


def build_prompt(statement: str, candidates: list[str], kind: str) -> str:
    lines = [f'Uitspraak uit het originele rapport:\n"{statement}"', "", f"{SOURCES[kind]}:"]
    lines += [f"{i}. {c}" for i, c in enumerate(candidates, start=1)] or ["(geen)"]
    return "\n".join(lines + ["", INSTRUCTION])


class Judge:
    def __init__(self, provider, cache_path: str | Path, allow_hosted: bool = False):
        info = provider.model_info
        if info.location != "local" and not allow_hosted:
            raise PrivacyViolation(
                f"judge model {info.model_id} is hosted; the original report is confidential. Use a local "
                "model, or pass allow_hosted=True (--allow-hosted-judge) as an explicit override.")
        self.provider, self.model_id = provider, info.model_id
        self.cache_path = Path(cache_path)
        self.cache: dict = json.loads(self.cache_path.read_text(encoding="utf-8")) if self.cache_path.exists() else {}
        self.calls = self.failures = 0

    def _key(self, statement: str, candidates: list[str], kind: str) -> str:
        raw = json.dumps([self.model_id, kind, statement, candidates], ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def __call__(self, statement: str, candidates: list[str], kind: str) -> Optional[dict]:
        """-> {"label", "sentences" (0-based), "conflict", "reason"}, or None if the model gave no usable answer."""
        if not candidates:
            return {"label": "missing", "sentences": [], "conflict": False, "reason": "geen kandidaten"}
        key = self._key(statement, candidates, kind)
        if key in self.cache:
            return self.cache[key]
        req = GenerationRequest(task=f"judge_{kind}", temperature=0.0, max_output_tokens=300, seed=42,
                                json_schema=SCHEMA, contains_resident_free_text=True,
                                messages=[Message("system", SYSTEM),
                                          Message("user", build_prompt(statement, candidates, kind))])
        self.calls += 1
        res = self.provider.generate(req)
        data = res.parsed if isinstance(res.parsed, dict) else extract_json(res.text or "")
        label = str((data or {}).get("label", "")).strip().lower()
        if res.error or label not in LABELS:
            self.failures += 1
            return None
        out = {"label": label,
               "sentences": [int(i) - 1 for i in (data.get("zinnen") or []) if str(i).isdigit()
                             and 1 <= int(i) <= len(candidates)],
               "conflict": bool(data.get("conflict")), "reason": str(data.get("reden") or "")[:200]}
        self.cache[key] = out
        return out

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, indent=1, ensure_ascii=False), encoding="utf-8")
