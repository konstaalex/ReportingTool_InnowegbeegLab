"""Researcher confirmation of image-derived values (human in the loop at the
evidence level, before any drafting). Append-only, hash-chained, like review
sessions: confirm / correct / reject per image record id.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from drafting.models import now_iso

ACTIONS = ("confirm", "correct", "reject", "reopen")


@dataclass
class ConfirmationEvent:
    seq: int
    ts: str
    actor: str
    action: str
    record_id: str
    value: Optional[float] = None
    note: Optional[str] = None
    prev_hash: str = ""
    hash: str = ""

    def compute_hash(self) -> str:
        d = asdict(self)
        d.pop("hash")
        return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ConfirmationLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.events: list[ConfirmationEvent] = []
        if self.path.exists():
            self.events = [ConfirmationEvent(**e) for e in json.loads(self.path.read_text(encoding="utf-8"))]

    def _append(self, actor: str, action: str, record_id: str, value: Optional[float] = None,
                note: Optional[str] = None) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        if not actor or not actor.strip():
            raise ValueError("a confirmation needs a named actor")
        if action == "correct" and value is None:
            raise ValueError("a correction needs the corrected value")
        ev = ConfirmationEvent(len(self.events), now_iso(), actor.strip(), action, record_id, value, note,
                               self.events[-1].hash if self.events else "")
        ev.hash = ev.compute_hash()
        self.events.append(ev)
        self.save()

    def confirm(self, record_id: str, actor: str, note: Optional[str] = None) -> None:
        self._append(actor, "confirm", record_id, note=note)

    def correct(self, record_id: str, value: float, actor: str, note: Optional[str] = None) -> None:
        self._append(actor, "correct", record_id, value=value, note=note)

    def reject(self, record_id: str, actor: str, note: Optional[str] = None) -> None:
        self._append(actor, "reject", record_id, note=note)

    def reopen(self, record_id: str, actor: str, note: Optional[str] = None) -> None:
        self._append(actor, "reopen", record_id, note=note)

    def status(self, record_id: str) -> tuple[str, Optional[float], Optional[ConfirmationEvent]]:
        """('unconfirmed'|'confirmed'|'corrected'|'rejected', corrected value, last event)."""
        state, value, last = "unconfirmed", None, None
        for ev in self.events:
            if ev.record_id != record_id:
                continue
            last = ev
            if ev.action == "confirm":
                state, value = "confirmed", None
            elif ev.action == "correct":
                state, value = "corrected", ev.value
            elif ev.action == "reject":
                state, value = "rejected", None
            elif ev.action == "reopen":
                state, value = "unconfirmed", None
        return state, value, last

    def verify_chain(self) -> list[str]:
        problems, prev = [], ""
        for ev in self.events:
            if ev.prev_hash != prev or ev.compute_hash() != ev.hash:
                problems.append(f"event {ev.seq} was changed after it was recorded")
            prev = ev.hash
        return problems

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(e) for e in self.events], indent=2, ensure_ascii=False), encoding="utf-8")
