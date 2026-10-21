"""Privacy guard and pseudonymisation (FR15, FR16, NFR7).

Rule: resident free text may only go to a provider whose location is
"local", unless the experiment config explicitly sets
`allow_resident_text_to_hosted = true` — in which case the text is
pseudonymised first and the override is recorded on the StageLog.

The pseudonymiser is deliberately simple and conservative (regexes for
e-mail, phone, postcode, house-number addresses, and 'mijn naam is X'
style self-identification). It is a safety net, not a guarantee; the
design doc lists a proper NER pass as future work.
"""
from __future__ import annotations

import re

from .base import GenerationRequest, ModelInfo, PrivacyViolation

_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[e-mail]"),
    (re.compile(r"(?<!\d)(?:\+31|0031|0)\s?(?:\d[\s-]?){9}(?!\d)"), "[telefoon]"),
    (re.compile(r"\b\d{4}\s?[A-Z]{2}\b"), "[postcode]"),
    (re.compile(r"\b([A-Z][a-z]+(?:straat|laan|weg|plein|singel|hof|dreef|kade|pad|gracht))\s+\d+[a-zA-Z]?\b"), r"\1 [huisnummer]"),
    (re.compile(r"\b(mijn naam is|ik ben|ik heet)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?", re.I), r"\1 [naam]"),
]


def pseudonymise(text: str) -> str:
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def check_request(request: GenerationRequest, info: ModelInfo, allow_hosted_resident_text: bool) -> str:
    """Returns a short privacy note for the StageLog, or raises."""
    if info.location == "local":
        return "local: nothing left the device"
    if request.contains_resident_free_text and not allow_hosted_resident_text:
        raise PrivacyViolation(
            f"Request '{request.task}' contains resident free text and provider '{info.model_id}' is hosted. "
            "Use a local model, drop quote evidence, or set allow_resident_text_to_hosted = true "
            "in the experiment config (logged as an explicit override).")
    if request.contains_resident_free_text:
        for m in request.messages:
            m.content = pseudonymise(m.content)
        return f"OVERRIDE: pseudonymised resident free text sent to hosted provider {info.model_id} ({info.data_residency or 'residency unknown'})"
    return f"hosted ({info.data_residency or 'residency unknown'}): no resident free text sent"
