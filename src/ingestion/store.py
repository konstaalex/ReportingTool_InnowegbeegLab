"""The in-memory + on-disk store of ingested SourceRecords.

Deliberately dumb (a list + JSON) at this stage — UC1's postcondition is
just "all source rows/images are stored and individually addressable",
not a database. Swapping this for something heavier later is a contained
change because nothing outside this module touches the storage format.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from .models import SourceRecord


class SourceStore:
    def __init__(self) -> None:
        self._records: list[SourceRecord] = []
        self._by_id: dict[str, SourceRecord] = {}

    def add(self, record: SourceRecord) -> None:
        if record.id in self._by_id:
            # Same file/sheet/locator ingested twice (e.g. a re-run) —
            # last one wins rather than silently duplicating.
            self._records = [r for r in self._records if r.id != record.id]
        self._records.append(record)
        self._by_id[record.id] = record

    def add_many(self, records: Iterable[SourceRecord]) -> None:
        for r in records:
            self.add(r)

    def remove_where(self, predicate) -> int:
        """Drop every record for which predicate(record) is true; returns how many."""
        keep = [r for r in self._records if not predicate(r)]
        removed = len(self._records) - len(keep)
        self._records = keep
        self._by_id = {r.id: r for r in keep}
        return removed

    def __len__(self) -> int:
        return len(self._records)

    def all(self) -> list[SourceRecord]:
        return list(self._records)

    def get(self, record_id: str) -> Optional[SourceRecord]:
        return self._by_id.get(record_id)

    def by_theme(self, theme: str) -> list[SourceRecord]:
        return [r for r in self._records if r.theme == theme]

    def by_perspective(self, perspective: str) -> list[SourceRecord]:
        return [r for r in self._records if r.perspective == perspective]

    def flagged(self) -> list[SourceRecord]:
        return [r for r in self._records if r.needs_manual_mapping]

    def summary(self) -> dict:
        by_perspective: dict[str, int] = {}
        by_theme: dict[str, int] = {}
        for r in self._records:
            by_perspective[r.perspective] = by_perspective.get(r.perspective, 0) + 1
            key = r.theme or "(unmapped)"
            by_theme[key] = by_theme.get(key, 0) + 1
        return {
            "total": len(self._records),
            "by_perspective": by_perspective,
            "by_theme": by_theme,
            "flagged_for_manual_mapping": len(self.flagged()),
        }

    def save(self, path: str | Path) -> None:
        data = [r.to_dict() for r in self._records]
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "SourceStore":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        store = cls()
        for d in data:
            store.add(SourceRecord.from_dict(d))
        return store
