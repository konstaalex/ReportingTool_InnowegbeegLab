"""Source-document registry for the Buurtscan data set (UC1, FR1-FR3).

Every input file gets a SourceDocument with a sha256 checksum, so that

- byte-identical copies (the data set ships several, e.g. ``... [9].xlsx``)
  are skipped and *reported* instead of double-counting respondents;
- an experiment run can record exactly which file versions it used
  (brief section 15: dataset version/checksums).

Case detection: the Buurtscan files carry their neighbourhood in the
filename (``<Kind> - <Buurt> <Stad> (SYNTHETISCH).xlsx``). A *case* is one
neighbourhood scan = one future report.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

# filename prefix -> (perspective, source_type)
FILENAME_KINDS: dict[str, tuple[str, str]] = {
    "expertwandeling": ("expert", "buurtscan_expert"),
    "vragenlijst buurtbewoners": ("bewoners", "buurtscan_survey"),
    "data voor demografie": ("data", "buurtscan_demografie"),
}

_CASE_RE = re.compile(
    r"^(?P<kind>[^-]+?)\s+-\s+(?P<case>.+?)\s*(?:\((?P<flag>[^)]*)\))?\s*(?:\[\d+\])?$"
)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")


@dataclass
class SourceDocument:
    source_id: str              # first 12 hex chars of the checksum
    filename: str               # relative to the ingested root
    source_type: str            # buurtscan_expert / buurtscan_survey / ... / unknown
    perspective: Optional[str]
    checksum: str
    case_id: Optional[str] = None
    case_label: Optional[str] = None
    synthetic: bool = False     # filename says SYNTHETISCH
    status: str = "pending"     # parsed / duplicate / unparsed / skipped
    duplicate_of: Optional[str] = None
    note: Optional[str] = None
    records: int = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def classify_filename(filename: str) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str], bool]:
    """Returns (perspective, source_type, case_id, case_label, synthetic) from
    the filename alone. Unknown prefixes return (None, None, None, None, False)
    so the caller flags the file (FR3) instead of guessing."""
    stem = Path(filename).stem
    m = _CASE_RE.match(stem)
    if not m:
        return None, None, None, None, False
    kind = m.group("kind").strip().lower()
    if kind not in FILENAME_KINDS:
        return None, None, None, None, False
    perspective, source_type = FILENAME_KINDS[kind]
    case_label = m.group("case").strip()
    synthetic = "synthetisch" in (m.group("flag") or "").lower()
    return perspective, source_type, slugify(case_label), case_label, synthetic
