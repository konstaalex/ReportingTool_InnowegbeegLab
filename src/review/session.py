"""Human-in-the-loop review (UC3/UC4; FR8-FR10; NFR2, NFR4, NFR11).

A ReviewSession wraps one SectionDraft. The draft itself is never modified:
the reviewer's decisions live in an append-only event log, and the current
state of each claim is *derived* from draft + events. Each event carries the
hash of the previous one, so verify_chain() detects edits to the log file.

    s = ReviewSession.start(draft, reviewer="mika")
    s.approve("SP-03")
    s.edit("SP-05", "Drie bewoners missen een watertappunt.", comment="was te algemeen")
    s.approve("SP-07")                                 # flagged weak -> raises NeedsOverride
    s.approve("SP-07", confirm_override=True)          # recorded as override_approve (FR10)
    s.reject("SP-09", comment="dubbel")
    s.is_reviewed()                                    # every claim resolved?
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from drafting.models import DraftClaim, SectionDraft, now_iso

RESOLVED = ("approved", "edited", "rejected")
ACTIONS = ("start", "approve", "override_approve", "edit", "reject", "comment", "reopen", "add_claim")


class NeedsOverride(Exception):
    """Approving a weak/unsupported claim needs confirm_override=True (FR10)."""


class ReviewError(Exception):
    pass


@dataclass
class ReviewEvent:
    seq: int
    ts: str
    actor: str
    action: str
    claim_id: Optional[str] = None
    before: Optional[str] = None
    after: Optional[str] = None
    comment: Optional[str] = None
    verification_status: Optional[str] = None   # the system's flag at the moment of the decision
    evidence_ids: Optional[list[str]] = None
    recheck: Optional[dict] = None               # verifier result for an edited text
    prev_hash: str = ""
    hash: str = ""

    def compute_hash(self) -> str:
        d = asdict(self)
        d.pop("hash")
        return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass
class ClaimState:
    claim_id: str
    original_text: str
    text: str
    claim_type: str
    evidence_ids: list[str]
    status: str = "draft"                 # draft | approved | edited | rejected
    verification: Optional[dict] = None
    override: bool = False
    comments: list[str] = field(default_factory=list)
    last_actor: Optional[str] = None
    last_ts: Optional[str] = None
    origin: str = "model"
    paragraph: int = 1


@dataclass
class ReviewSession:
    draft: SectionDraft
    events: list[ReviewEvent] = field(default_factory=list)

    # ----------------------------------------------------------- lifecycle
    @classmethod
    def start(cls, draft: SectionDraft, reviewer: str) -> "ReviewSession":
        s = cls(draft=draft)
        s._append(reviewer, "start", comment=f"review of draft {draft.draft_id} ({draft.pipeline_id})")
        return s

    def _append(self, actor: str, action: str, **kw) -> ReviewEvent:
        if action not in ACTIONS:
            raise ReviewError(f"unknown action {action!r}")
        if not actor or not actor.strip():
            raise ReviewError("every review action needs a named actor (FR9)")
        prev = self.events[-1].hash if self.events else ""
        ev = ReviewEvent(seq=len(self.events), ts=now_iso(), actor=actor.strip(), action=action, prev_hash=prev, **kw)
        ev.hash = ev.compute_hash()
        self.events.append(ev)
        return ev

    def verify_chain(self) -> list[str]:
        problems, prev = [], ""
        for ev in self.events:
            if ev.prev_hash != prev:
                problems.append(f"event {ev.seq}: prev_hash does not match")
            if ev.compute_hash() != ev.hash:
                problems.append(f"event {ev.seq}: content changed after it was recorded")
            prev = ev.hash
        return problems

    # ----------------------------------------------------------- derived state
    def states(self) -> dict[str, ClaimState]:
        out: dict[str, ClaimState] = {}
        for c in self.draft.claims:
            out[c.claim_id] = ClaimState(c.claim_id, c.text, c.text, c.claim_type, list(c.evidence_ids),
                                         verification=c.verification, origin=c.origin, paragraph=c.paragraph)
        for ev in self.events:
            st = out.get(ev.claim_id) if ev.claim_id else None
            if ev.action == "add_claim":
                data = json.loads(ev.after)
                out[ev.claim_id] = ClaimState(ev.claim_id, "", data["text"], data["claim_type"], data["evidence_ids"],
                                              status="edited", origin="human", paragraph=data.get("paragraph", 1),
                                              last_actor=ev.actor, last_ts=ev.ts)
                continue
            if st is None:
                continue
            if ev.action == "approve":
                st.status = "approved"
            elif ev.action == "override_approve":
                st.status, st.override = "approved", True
            elif ev.action == "edit":
                st.status, st.text = "edited", ev.after or st.text
                if ev.evidence_ids is not None:
                    st.evidence_ids = list(ev.evidence_ids)
                if ev.recheck is not None:
                    st.verification = ev.recheck
            elif ev.action == "reject":
                st.status = "rejected"
            elif ev.action == "reopen":
                st.status, st.override = "draft", False
            if ev.comment and ev.action != "start":
                st.comments.append(f"{ev.actor}: {ev.comment}")
            st.last_actor, st.last_ts = ev.actor, ev.ts
        return out

    def _claim(self, claim_id: str) -> DraftClaim:
        for c in self.draft.claims:
            if c.claim_id == claim_id:
                return c
        raise ReviewError(f"unknown claim {claim_id!r}")

    def _status_of(self, claim_id: str) -> Optional[str]:
        st = self.states().get(claim_id)
        return (st.verification or {}).get("status") if st else None

    # ----------------------------------------------------------- actions
    def approve(self, claim_id: str, actor: str = "", confirm_override: bool = False, comment: Optional[str] = None) -> None:
        actor = actor or self._default_actor()
        if claim_id not in self.states():
            raise ReviewError(f"unknown claim {claim_id!r}")
        vstat = self._status_of(claim_id)
        if vstat in ("weak", "unsupported"):
            if not confirm_override:
                raise NeedsOverride(f"{claim_id} is flagged '{vstat}' by the verifier; approving it needs an explicit override.")
            self._append(actor, "override_approve", claim_id=claim_id, comment=comment, verification_status=vstat)
            return
        self._append(actor, "approve", claim_id=claim_id, comment=comment, verification_status=vstat)

    def edit(self, claim_id: str, new_text: str, actor: str = "", comment: Optional[str] = None,
             evidence_ids: Optional[list[str]] = None, reverify: bool = True) -> None:
        """Edits keep the original AI text (FR9). If `reverify`, the edited
        text is re-checked against its evidence and the result is stored on
        the event, so an edit cannot silently introduce an unsupported number."""
        actor = actor or self._default_actor()
        st = self.states().get(claim_id)
        if st is None:
            raise ReviewError(f"unknown claim {claim_id!r}")
        if not new_text.strip():
            raise ReviewError("edited text is empty; use reject() to remove a claim")
        recheck = None
        if reverify and self._index is not None:
            from verification.checks import verify_claim
            probe = DraftClaim(claim_id, new_text, st.claim_type, evidence_ids if evidence_ids is not None else st.evidence_ids)
            recheck = verify_claim(probe, self.draft.section, self._index)
        self._append(actor, "edit", claim_id=claim_id, before=st.text, after=new_text.strip(), comment=comment,
                     evidence_ids=evidence_ids, verification_status=(recheck or {}).get("status"), recheck=recheck)

    def reject(self, claim_id: str, actor: str = "", comment: Optional[str] = None) -> None:
        actor = actor or self._default_actor()
        if claim_id not in self.states():
            raise ReviewError(f"unknown claim {claim_id!r}")
        self._append(actor, "reject", claim_id=claim_id, comment=comment, verification_status=self._status_of(claim_id))

    def comment(self, claim_id: str, text: str, actor: str = "") -> None:
        self._append(actor or self._default_actor(), "comment", claim_id=claim_id, comment=text)

    def reopen(self, claim_id: str, actor: str = "", comment: Optional[str] = None) -> None:
        self._append(actor or self._default_actor(), "reopen", claim_id=claim_id, comment=comment)

    def add_claim(self, text: str, claim_type: str, evidence_ids: list[str], actor: str = "", paragraph: int = 1) -> str:
        """A researcher-written claim (NFR4 ownership): recorded as human origin."""
        n = sum(1 for e in self.events if e.action == "add_claim") + 1
        cid = f"H-{n:02d}"
        self._append(actor or self._default_actor(), "add_claim", claim_id=cid,
                     after=json.dumps({"text": text, "claim_type": claim_type, "evidence_ids": evidence_ids,
                                       "paragraph": paragraph}, ensure_ascii=False))
        return cid

    # ----------------------------------------------------------- queries
    def pending(self) -> list[str]:
        return [cid for cid, st in self.states().items() if st.status not in RESOLVED]

    def is_reviewed(self) -> bool:
        return not self.pending()

    def final_claims(self) -> list[ClaimState]:
        return [st for st in self.states().values() if st.status in ("approved", "edited")]

    def stats(self) -> dict:
        sts = list(self.states().values())
        ev = [e for e in self.events if e.claim_id]
        return {
            "claims": len(sts),
            "approved": sum(s.status == "approved" for s in sts),
            "edited": sum(s.status == "edited" for s in sts),
            "rejected": sum(s.status == "rejected" for s in sts),
            "pending": sum(s.status not in RESOLVED for s in sts),
            "overrides": sum(s.override for s in sts),
            "human_added": sum(s.origin == "human" for s in sts),
            "actions": len(ev),
            "chain_ok": not self.verify_chain(),
        }

    def _default_actor(self) -> str:
        return self.events[0].actor if self.events else "onbekend"

    _index = None   # optional evidence index for re-verifying edits

    def attach_index(self, index: dict) -> "ReviewSession":
        self._index = index
        return self

    # ----------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {"draft": self.draft.to_dict(), "events": [asdict(e) for e in self.events]}

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "ReviewSession":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(draft=SectionDraft.from_dict(d["draft"]), events=[ReviewEvent(**e) for e in d["events"]])
