"""Dataperspectief: QGIS-exported map images.

These are pre-rendered pictures, not raw GIS layers — the record we can
make here is metadata (which map, which RIVM vuistregel/theme it plausibly
supports, file size as a cheap sanity check) plus the file path itself as
the locator. A human still has to look at the image; UC3's "show the exact
... image region" is a later capability, not this one.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..models import SourceRecord
from ..theme_tagger import theme_for_map_filename

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def detect(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_SUFFIXES


def parse_file(path: str, source_file_label: Optional[str] = None) -> list[SourceRecord]:
    p = Path(path)
    source_file_label = source_file_label or p.name
    theme, needs_manual = theme_for_map_filename(p.name)
    locator = "image"
    rec_id = SourceRecord.make_id("data", source_file_label, None, locator)
    size_bytes = p.stat().st_size if p.exists() else None
    return [SourceRecord(
        id=rec_id,
        perspective="data",
        source_file=source_file_label,
        sheet=None,
        locator=locator,
        theme=theme,
        text=p.stem,
        extra={"size_bytes": size_bytes},
        needs_manual_mapping=needs_manual,
    )]
