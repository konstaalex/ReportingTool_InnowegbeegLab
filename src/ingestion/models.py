"""The addressable unit everything downstream cites.

One SourceRecord = one row, cell-group, or image that a later drafted
sentence could point back to (UC2/UC3 in the requirements doc). The
`locator` field must be precise enough to resolve without re-parsing the
whole source file again — that precision is the entire point of this
module: a citation that only says "the spreadsheet" is not a citation.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional

# The six themes shared by the report, the expert scoring sheets, and the
# resident coded-data taxonomy. Keeping this list in one place means every
# parser and the tagger agree on the same spelling.
THEMES: tuple[str, ...] = (
    "Sporten",
    "Actieve mobiliteit",
    "Formeel spelen",
    "Informeel spelen",
    "Ontmoeten",
    "Uitstraling",
)

# "reference" = normative/style documents (RIVM vuistregels, writing rules):
# not research data about a neighbourhood, but still citable by id.
PERSPECTIVES: tuple[str, ...] = ("data", "expert", "bewoners", "reference")


@dataclass
class SourceRecord:
    id: str
    perspective: str                    # one of PERSPECTIVES
    source_file: str                    # path relative to the ingested root
    sheet: Optional[str]                # None for images
    locator: str                        # e.g. "row 6" / "B6:C6" / "image"
    theme: Optional[str] = None         # one of THEMES, or None if unmapped
    subtheme: Optional[str] = None      # indicator / behoefte-belemmering-positief / etc.
    neighbourhood: Optional[str] = None
    text: Optional[str] = None          # the quotable content, when textual
    value: Optional[float] = None       # the numeric content, when numeric
    extra: dict = field(default_factory=dict)
    needs_manual_mapping: bool = False  # FR3: flagged, never silently guessed
    case_id: Optional[str] = None       # which Buurtscan case (one neighbourhood) this belongs to

    def __post_init__(self) -> None:
        if self.theme is not None and self.theme not in THEMES:
            raise ValueError(f"unknown theme {self.theme!r} — not in THEMES")
        if self.perspective not in PERSPECTIVES:
            raise ValueError(f"unknown perspective {self.perspective!r}")

    @staticmethod
    def make_id(perspective: str, source_file: str, sheet: Optional[str], locator: str) -> str:
        raw = f"{perspective}|{source_file}|{sheet}|{locator}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SourceRecord":
        return cls(**data)
