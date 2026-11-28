"""Traceability check of an exported report (FR: every claim traces back to a cell, row or map).

After export, every claim in the Word report must
  1. appear in the evidence map (evidence_map_<case>.csv) with at least one evidence id,
  2. cite evidence ids that exist in the case's evidence items, and
  3. lead, through each evidence item, to at least one source record with a file and a location
     (cell, row, map region).
Also: every evidence id printed in the Word report must be in the evidence map, and every claim of
the map must be found in the Word text. Read-only: it changes nothing.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

IDS_RUN = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def docx_text_and_cited_ids(path: str | Path) -> tuple[str, set[str]]:
    """All text of the Word report, and the evidence ids printed after claims (the superscript runs)."""
    from docx import Document
    doc = Document(str(path))
    paras = list(doc.paragraphs)
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                paras.extend(cell.paragraphs)
    text, cited = [], set()
    for p in paras:
        text.append(p.text)
        for r in p.runs:
            m = IDS_RUN.match(r.text or "")
            if m and r.font.superscript:
                cited |= {x.strip() for x in m.group(1).split(",") if x.strip()}
    return _norm(" ".join(text)), cited


def check_export(files: dict, evidence: dict, store, required_sections: Optional[list[str]] = None) -> dict:
    """files: what Workspace.export returned (needs 'docx' and 'evidence_map'). evidence: id -> Evidence.
    store: the SourceStore. Returns {'ok', 'n_claims', 'n_evidence_ids', 'n_source_records', 'problems'}."""
    word, cited = docx_text_and_cited_ids(files["docx"])
    with open(files["evidence_map"], encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    claims: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if r["status"] == "rejected" or (required_sections and r["section"] not in required_sections):
            continue
        claims[(r["section"], r["claim_id"])].append(r)
    problems: list[str] = []
    ids_seen, recs_seen = set(), set()
    for (sec, cid), rs in claims.items():
        where = f"{sec} › {cid}"
        ids = [r["evidence_id"] for r in rs if r["evidence_id"] and r["evidence_id"] != "(geen)"]
        if not ids:
            problems.append(f"{where}: geen bewijsstuk")
        for eid in ids:
            ids_seen.add(eid)
            e = evidence.get(eid)
            if e is None:
                problems.append(f"{where}: bewijsstuk {eid} bestaat niet")
                continue
            recs = [store.get(rid) for rid in e.source_record_ids]
            good = [x for x in recs if x is not None and x.source_file and x.locator]
            if not good:
                problems.append(f"{where}: bewijsstuk {eid} leidt naar geen bronbestand met cel, rij of locatie")
            recs_seen |= {x.id for x in good}
        if _norm(rs[0]["claim_text"]) not in word:
            problems.append(f"{where}: bewering niet gevonden in het Word-rapport")
    in_map = {r["evidence_id"] for r in rows}
    for eid in sorted(cited - in_map):
        problems.append(f"Word-rapport noemt {eid}, maar dat staat niet in de evidence map")
    for eid in sorted(cited - set(evidence)):
        problems.append(f"Word-rapport noemt {eid}, maar dat bewijsstuk bestaat niet")
    return {"ok": not problems and bool(claims), "n_claims": len(claims), "n_evidence_ids": len(ids_seen),
            "n_source_records": len(recs_seen), "n_cited_in_word": len(cited), "problems": problems}
