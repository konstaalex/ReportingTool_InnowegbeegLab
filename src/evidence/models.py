"""Evidence items: the only thing a drafting model is allowed to see.

An Evidence item is a deterministic, pre-computed fact (a statistic, an
expert score, a grouped resident quote, a demographic comparison, a norm)
with:

- a short readable id the model cites ("B-SP-03"), stable for the same input;
- a Dutch `statement` that already says the fact correctly, so the model only
  has to select, order and connect facts - never compute them (brief rule 6);
- `data` with every number in machine form (the verifier checks numbers in
  claims against these);
- `source_record_ids` pointing to the SourceRecords (cells) it came from, which
  is the second hop of claim -> evidence -> cell traceability.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

THEME_CODES: dict[str, str] = {
    "Sporten": "SP",
    "Actieve mobiliteit": "AM",
    "Formeel spelen": "FS",
    "Informeel spelen": "IS",
    "Ontmoeten": "ON",
    "Uitstraling": "UI",
}
GENERAL_CODE = "AL"
GENERAL = "Algemeen"
AANLEIDING = "Aanleiding"        # project context, from researcher notes of type 'Projectcontext'
CONCLUSIES = "Conclusies"        # cross-theme conclusions (own evidence pack)
SECTION_CODES: dict[str, str] = {**THEME_CODES, GENERAL: GENERAL_CODE, AANLEIDING: "AA", CONCLUSIES: "CO"}
PERSPECTIVE_CODES: dict[str, str] = {"expert": "X", "bewoners": "B", "data": "D", "reference": "N", "cross": "C",
                                     "context": "P"}
NOTE_CODE = "O"                  # every researcher note gets an O-.. id, whatever its perspective
PERSPECTIVE_LABELS_NL: dict[str, str] = {
    "expert": "expertwandeling", "bewoners": "bewonersvragenlijst", "data": "data en kaarten",
    "reference": "RIVM-vuistregels", "cross": "vergelijking tussen perspectieven",
    "context": "projectcontext en literatuur",
}


@dataclass
class Evidence:
    evidence_id: str
    case_id: str
    themes: list[str]                  # [] = general / context
    perspective: str                   # expert | bewoners | data | reference | cross | context
    kind: str                          # theme_average | expert_statement | rating | likert | choice | multi | option_share | quote | demographic | norm | comparison | respondents | coded | map_measurement | image_reading | note | cross_theme
    statement: str
    data: dict = field(default_factory=dict)
    source_record_ids: list[str] = field(default_factory=list)
    salience: float = 0.0
    contains_free_text: bool = False   # resident/expert free text -> privacy rules apply (FR15)
    method: str = "deterministic"
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Evidence":
        return cls(**d)

    def prompt_line(self) -> str:
        """Compact line handed to the model: id, perspective, statement."""
        return f"[{self.evidence_id}] ({PERSPECTIVE_LABELS_NL.get(self.perspective, self.perspective)}) {self.statement}"


@dataclass
class EvidencePack:
    """All evidence for one case and one report section (theme or general)."""
    case_id: str
    section: str                       # a theme name, "Algemeen", "Aanleiding" or "Conclusies"
    items: list[Evidence]
    unavailable: list[str] = field(default_factory=list)  # inputs known to exist but not usable (e.g. chart images)
    built_with: dict = field(default_factory=dict)        # config fingerprints etc.

    def by_id(self) -> dict[str, Evidence]:
        return {e.evidence_id: e for e in self.items}

    def ids(self) -> list[str]:
        return [e.evidence_id for e in self.items]

    def select(self, ids: list[str]) -> "EvidencePack":
        keep = set(ids)
        return EvidencePack(self.case_id, self.section, [e for e in self.items if e.evidence_id in keep],
                            list(self.unavailable), dict(self.built_with))

    def top_k(self, k: int) -> "EvidencePack":
        """Highest-salience items, returned in original (stable) order."""
        ranked = sorted(self.items, key=lambda e: -e.salience)[:k]
        return self.select([e.evidence_id for e in ranked])

    def to_dict(self) -> dict:
        return {"case_id": self.case_id, "section": self.section, "items": [e.to_dict() for e in self.items],
                "unavailable": self.unavailable, "built_with": self.built_with}

    @classmethod
    def from_dict(cls, d: dict) -> "EvidencePack":
        return cls(d["case_id"], d["section"], [Evidence.from_dict(x) for x in d["items"]],
                   d.get("unavailable", []), d.get("built_with", {}))
