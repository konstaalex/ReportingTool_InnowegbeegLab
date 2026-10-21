"""Reference norms (RIVM vuistregels) as citable SourceRecords.

The rules are transcribed in config/reference/rivm_vuistregels.toml with a
page locator into the PDF. When the PDF itself is present in the ingested
folder its checksum is recorded on every record, so a reviewer can confirm
the transcription refers to the same document version.
"""
from __future__ import annotations

from typing import Optional

from ..config import load_config
from ..models import SourceRecord


def load_rivm_rules(pdf_checksum: Optional[str] = None) -> list[SourceRecord]:
    cfg = load_config("reference/rivm_vuistregels.toml")
    source_file = cfg["source_file"]
    records = []
    for rule in cfg["rule"]:
        records.append(SourceRecord(
            id=SourceRecord.make_id("reference", source_file, None, rule["locator"]),
            perspective="reference",
            source_file=source_file,
            sheet=None,
            locator=rule["locator"],
            theme=None,
            subtheme=rule["id"],
            text=rule["text"],
            value=float(rule["threshold"]),
            extra={
                "record_kind": "norm_rule",
                "rule_id": rule["id"],
                "title": rule["title"],
                "unit": rule["unit"],
                "target_group": rule["target_group"],
                "themes": list(rule["themes"]),
                "status_note": cfg["status_note"],
                "citation": cfg["citation"],
                "pdf_sha256": pdf_checksum,
                "transcribed": True,
            },
        ))
    return records
