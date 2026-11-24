"""E4 (staged + checker step) and E1R (single prompt on raw records).
Decisions D1-D6 and R1-R6: docs/experiments/e4-e1r-design-decisions.md. Synthetic data only."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from buurtscan_fixtures import has_docx, write_case_folder  # noqa: E402
from app.services import Workspace  # noqa: E402
from drafting.models import SectionDraft  # noqa: E402
from drafting.pipelines import (EXPERIMENT_PIPELINES, NO_ANSWER, PIPELINES, REVISION_OUTCOMES, WITHDRAWN,  # noqa: E402
                                PipelineConfig, revision_schema, run_pipeline, verification_view)
from drafting.prompts import PROMPTS_DIR, system_prompt  # noqa: E402
from drafting.raw_records import ID_COLUMN, markdown_tables, raw_pack, record_row, records_behind  # noqa: E402
from evidence.models import CONCLUSIES, GENERAL, Evidence, EvidencePack  # noqa: E402
from experiments.runner import experiment_definition, run_experiment  # noqa: E402
from ingestion.models import THEMES, SourceRecord  # noqa: E402
from ingestion.store import SourceStore  # noqa: E402
from llm.base import Capabilities, ModelInfo  # noqa: E402
from llm.providers import ReplayProvider  # noqa: E402
from reports.runs import load_run, revision_totals  # noqa: E402
from verification.checks import verify_section  # noqa: E402

CASE = "testwijk-teststad"
E4 = dict(pipeline_id="staged_checked", prompt_version="v4")
RAW = dict(pipeline_id="naive_raw", prompt_version="v4")


class _Replay(ReplayProvider):
    """Replays outputs in order and keeps the requests."""

    def __init__(self, outputs, location="local"):
        super().__init__(ModelInfo("replay", "replay", "replay", location, Capabilities(structured_output=True)),
                         [json.dumps(o) if not isinstance(o, str) else o for o in outputs])
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return super().generate(request)


def _pack(section: str = "Sporten") -> EvidencePack:
    return EvidencePack("x", section, [
        Evidence("X-SP-01", "x", ["Sporten"], "expert", "expert_statement", "Experts geven 2,4 voor sportplekken.", {}, ["r1"]),
        Evidence("B-SP-01", "x", ["Sporten"], "bewoners", "rating", "Bewoners (65+) geven 6,1.", {}, ["r2"]),
    ])


def _claim(text, ids, ctype="bevinding"):
    return {"text": text, "type": ctype, "evidence_ids": ids}


GOOD = _claim("Experts geven 2,4 voor sportplekken.", ["X-SP-01"])
WEAK = _claim("Bewoners geven 2,4 voor sportplekken.", ["X-SP-01"])          # names residents, cites experts
BAD_NUMBER = _claim("Bewoners (65+) geven 9,9.", ["B-SP-01"])
BAD_ID = _claim("Bewoners zijn kritisch.", ["B-SP-77"])
NO_ID = _claim("Het gebied is mooi.", [], "conclusie")


def _rev(cid, outcome, text="", ids=(), ctype="bevinding"):
    return {"claim_id": cid, "uitkomst": outcome, "text": text, "type": ctype, "evidence_ids": list(ids)}


def _e4(revisions, findings=(GOOD, WEAK), residents=(BAD_NUMBER, BAD_ID), synthesis=(NO_ID,), pack=None, index=None):
    prov = _Replay([{"findings": list(findings)}, {"findings": list(residents)}, {"claims": list(synthesis)},
                    *([{"claims": revisions}] if isinstance(revisions, list) else [revisions])])
    pack = pack or _pack()
    d = run_pipeline(pack, PipelineConfig(**E4), prov, index)
    verify_section(d, pack, index or pack.by_id())
    return d, prov


class TestPromptsV4(unittest.TestCase):
    def test_v4_keeps_every_v3_prompt_unchanged(self):
        for p in (PROMPTS_DIR / "v3").glob("*.txt"):
            self.assertEqual(p.read_text(encoding="utf-8"), (PROMPTS_DIR / "v4" / p.name).read_text(encoding="utf-8"))
        added = {p.name for p in (PROMPTS_DIR / "v4").glob("*.txt")} - {p.name for p in (PROMPTS_DIR / "v3").glob("*.txt")}
        self.assertEqual(added, {"revise.txt", "system_raw.txt", "naive_raw.txt"})

    def test_raw_prompts_differ_only_in_the_data_name_and_rule_3(self):            # R3
        def lines(name):
            return (PROMPTS_DIR / "v4" / name).read_text(encoding="utf-8").splitlines()
        for base, raw, changed in (("system.txt", "system_raw.txt", ("1.", "2.", "3.", "6.", "- bevinding")),
                                   ("naive_section.txt", "naive_raw.txt", ("Sla een", "Bewijsstukken:"))):
            a, b = lines(base), lines(raw)
            self.assertEqual(len(a), len(b))
            diff = [x for x, y in zip(a, b) if x != y]
            self.assertEqual(len(diff), len(changed))
            for x, start in zip(diff, changed):
                self.assertTrue(x.startswith(start), x)
            self.assertNotIn("bewijsstuk", "\n".join(b).lower())
        self.assertIn("Als je rekent, zeg dan waaruit je het hebt berekend.", system_prompt("v4", "system_raw"))
        self.assertNotIn("Reken niet zelf", system_prompt("v4", "system_raw"))

    def test_experiments_are_registered(self):
        e4, e1r = experiment_definition("E4"), experiment_definition("E1R")
        self.assertEqual((e4["pipeline"], e4["prompt_version"], e4["unit"]), ("staged_checked", "v4", "report"))
        self.assertEqual((e1r["pipeline"], e1r["prompt_version"]), ("naive_raw", "v4"))
        self.assertEqual(e1r["report_sections"], list(THEMES))                    # R6
        for base, other in (("E2", e4), ("E1", e1r)):                              # one factor changes
            b = experiment_definition(base)
            for k in ("model", "temperature", "seeds", "selection", "cases"):
                self.assertEqual(b[k], other[k], k)
        self.assertEqual(PIPELINES, ("template", "naive", "staged"))               # the review UI is unchanged
        self.assertLess(set(PIPELINES), set(EXPERIMENT_PIPELINES))


class TestE4(unittest.TestCase):
    FIX = [_rev("SP-03", "getal_hersteld", "Bewoners (65+) geven 6,1.", ["B-SP-01"]),
           _rev("SP-04", "geherformuleerd", "Bewoners (65+) geven 7,7.", ["B-SP-01"]),
           _rev("SP-05", WITHDRAWN)]

    def test_grounded_and_weak_claims_are_never_touched(self):                    # D2
        # the model also answers about claims it was not asked about: ignored
        extra = [_rev("SP-01", WITHDRAWN), _rev("SP-02", "geherformuleerd", "Iets anders.", ["X-SP-01"])]
        d, prov = _e4(extra + self.FIX)
        by_id = {c.claim_id: c for c in d.claims}
        self.assertEqual((by_id["SP-01"].text, by_id["SP-01"].evidence_ids), (GOOD["text"], GOOD["evidence_ids"]))
        self.assertEqual((by_id["SP-02"].text, by_id["SP-02"].evidence_ids), (WEAK["text"], WEAK["evidence_ids"]))
        self.assertEqual(by_id["SP-01"].verification["status"], "grounded")
        self.assertEqual(by_id["SP-02"].verification["status"], "weak")
        self.assertNotIn("claim_id: SP-01", prov.requests[-1].messages[1].content)
        self.assertNotIn("claim_id: SP-02", prov.requests[-1].messages[1].content)
        self.assertEqual({r["claim_id"] for r in d.revision["claims"]}, {"SP-03", "SP-04", "SP-05"})

    def test_repeated_claim_ids_cannot_reach_a_grounded_claim(self):              # D2
        # the empty finding shifts the numbering: GOOD and BAD_NUMBER both become SP-02
        prov = _Replay([{"findings": [_claim("", []), GOOD]}, {"findings": [BAD_NUMBER]}, {"claims": []},
                        {"claims": [_rev("SP-02-pos2", WITHDRAWN)]}])
        d = run_pipeline(_pack(), PipelineConfig(**E4), prov)
        self.assertEqual([c.text for c in d.claims], [GOOD["text"]])
        self.assertEqual(d.revision["claims"][0]["original"]["text"], BAD_NUMBER["text"])

    def test_record_keeps_original_revision_and_flags(self):                      # D3
        d, _ = _e4(self.FIX)
        rec = {r["claim_id"]: r for r in d.revision["claims"]}
        self.assertEqual(rec["SP-03"]["original"]["text"], BAD_NUMBER["text"])
        self.assertEqual(rec["SP-03"]["revised"]["text"], "Bewoners (65+) geven 6,1.")
        self.assertEqual(rec["SP-03"]["verification_before"]["status"], "unsupported")
        self.assertEqual(rec["SP-03"]["verification_after"]["status"], "grounded")
        self.assertIn("unsupported_number", [f["code"] for f in rec["SP-03"]["verification_before"]["flags"]])
        self.assertIsNone(rec["SP-05"]["revised"])
        self.assertEqual(rec["SP-05"]["outcome"], WITHDRAWN)
        again = SectionDraft.from_dict(json.loads(json.dumps(d.to_dict())))
        self.assertEqual(again.revision, d.revision)

    def test_still_flagged_revision_is_kept_and_counted(self):                    # D3
        d, _ = _e4(self.FIX)
        kept = {c.claim_id: c for c in d.claims}
        self.assertEqual(kept["SP-04"].text, "Bewoners (65+) geven 7,7.")
        self.assertEqual(kept["SP-04"].verification["status"], "unsupported")
        self.assertEqual(d.revision["summary"]["still_unsupported"], 1)

    def test_withdrawn_claims_are_counted_and_leave_the_draft(self):              # D4
        d, _ = _e4(self.FIX)
        self.assertNotIn("SP-05", [c.claim_id for c in d.claims])
        s = d.revision["summary"]
        self.assertEqual((s["claims_before"], s["sent_back"], s["withdrawn"], s["revised"]), (5, 3, 1, 2))
        self.assertEqual(len(d.claims), s["claims_before"] - s["withdrawn"])
        self.assertEqual(sum(s["outcomes"].values()), s["sent_back"])
        t = revision_totals([d])
        self.assertEqual((t["withdrawal_rate"], t["unsupported_rate_before"]), (0.2, 0.6))

    def test_one_call_with_a_factored_context(self):                              # D3, D5
        d, prov = _e4(self.FIX)
        self.assertEqual([r.task for r in prov.requests],
                         ["staged_findings_expert", "staged_findings_bewoners", "staged_synthesis", "revise"])
        msg = prov.requests[-1].messages[1].content
        self.assertIn("[X-SP-01]", msg)                                           # the section's full evidence list
        self.assertIn("[B-SP-01]", msg)
        self.assertNotIn(WEAK["text"], msg)                                       # not the rest of the draft
        self.assertIn("9,9 staan niet in", msg)                                   # the verifier's message
        self.assertIn("B-SP-77", msg)
        a, b = prov.requests[0], prov.requests[-1]
        self.assertEqual((a.seed, a.temperature, a.messages[0].content), (b.seed, b.temperature, b.messages[0].content))
        self.assertEqual(d.revision["summary"]["calls"], 1)

    def test_schema_keeps_the_claims_schema_and_the_cap(self):                    # D5
        item = revision_schema("v4", ["SP-03", "SP-05"])["properties"]["claims"]["items"]
        self.assertEqual(item["properties"]["evidence_ids"]["maxItems"], 6)
        self.assertEqual(item["properties"]["claim_id"]["enum"], ["SP-03", "SP-05"])
        self.assertEqual(item["properties"]["uitkomst"]["enum"], list(REVISION_OUTCOMES))
        self.assertEqual(len(REVISION_OUTCOMES), 4)

    def test_no_call_when_nothing_is_unsupported(self):                           # D6
        d, prov = _e4([], findings=(GOOD,), residents=(), synthesis=(WEAK,))
        self.assertEqual(len(prov.requests), 3)
        self.assertEqual(d.revision["summary"]["calls"], 0)
        self.assertEqual(len(d.claims), 2)

    def test_unanswered_and_unusable_answers_keep_the_original(self):
        d, _ = _e4([_rev("SP-03", "geherformuleerd", "", ["B-SP-01"]), _rev("SP-04", "verwijderd", "Tekst.", ["B-SP-01"])])
        self.assertEqual([c.text for c in d.claims[2:]], [BAD_NUMBER["text"], BAD_ID["text"], NO_ID["text"]])
        self.assertEqual(d.revision["summary"]["outcomes"][NO_ANSWER], 3)
        self.assertEqual(d.revision["summary"]["withdrawn"], 0)

    def test_failed_revision_call_is_visible_and_keeps_the_draft(self):
        d, _ = _e4("geen json")
        self.assertTrue(any("revise" in e for e in d.errors))
        self.assertEqual(len(d.claims), 5)
        self.assertEqual(d.revision["summary"]["calls"], 1)

    def test_type_and_paragraph_stay(self):
        d, _ = _e4([_rev("SP-05", "geherformuleerd", "Experts geven 2,4 voor sportplekken.", ["X-SP-01"], "bevinding")])
        c = next(c for c in d.claims if c.claim_id == "SP-05")
        self.assertEqual((c.claim_type, c.paragraph, c.stage), ("conclusie", 4, "revise"))

    def test_feedback_uses_the_same_index_as_the_final_check(self):               # D1
        pack = _pack()
        index = {**pack.by_id(), "B-SP-77": Evidence("B-SP-77", "x", [], "bewoners", "rating", "Bewoners.", {}, ["r9"])}
        d, prov = _e4([], residents=(BAD_ID,), synthesis=(GOOD,), pack=pack, index=index)
        self.assertEqual(len(prov.requests), 3)                                   # known in the case: not rejected
        self.assertEqual(d.revision["summary"]["unsupported_before"], 0)

    def test_conclusions_are_checked_too(self):
        prov = _Replay([{"claims": [NO_ID, GOOD]}, {"claims": [_rev("CO-01", WITHDRAWN)]}])
        d = run_pipeline(_pack(CONCLUSIES), PipelineConfig(**E4), prov)
        self.assertEqual([r.task for r in prov.requests], ["conclusions", "revise"])
        self.assertEqual([c.claim_id for c in d.claims], ["CO-02"])
        self.assertEqual(prov.requests[1].max_output_tokens, prov.requests[0].max_output_tokens)

    def test_older_prompt_version_fails_visibly(self):
        prov = _Replay([{"findings": [NO_ID]}, {"findings": []}, {"claims": []}])
        d = run_pipeline(_pack(), PipelineConfig(pipeline_id="staged_checked", prompt_version="v3"), prov)
        self.assertTrue(any("revise" in e for e in d.errors))


# ------------------------------------------------------------------ E1R

def _rec(i, kind, perspective="expert", file="expert.xlsx", sheet="Score Testbuurt", **kw):
    extra = {"record_kind": kind, **kw.pop("extra", {})}
    return SourceRecord(id=f"{i:012x}", perspective=perspective, source_file=file, sheet=sheet, locator=f"row {i}",
                        extra=extra, case_id="x", **kw)


def _store() -> SourceStore:
    s = SourceStore()
    s.add_many([
        _rec(1, "expert_statement", theme="Sporten", neighbourhood="Testbuurt", value=2.6666667,
             extra={"statement_number": 4, "statement": "Er zijn sportplekken", "remark": "Twee | velden",
                    "evaluator_scores": {"A": 2, "E": 3}, "crosscheck_avg_score_per_indicator": 9.87}),
        _rec(2, "expert_section_remark", theme="Sporten", text="Geen sportplekken", extra={"section": "Sportplekken"}),
        _rec(3, "survey_tally", "bewoners", "vragenlijst.xlsx", "Resultaten vragenlijst", text="Hoe vaak sport u?",
             extra={"group": "65+", "counts": {"Nooit": 4, "Vaak": 14}}),
        _rec(4, "coded_response", "bewoners", "gecodeerd.xlsx", "Gecategoriseerde data", theme="Sporten",
             text="Jeu de boules", value=3.0, extra={"group": "Stakeholders", "response_type": "Behoefte"}),
        _rec(5, "image_measurement", "data", "kaart.png", None,
             extra={"title": "Testkaart", "measure": "afstand", "measure_kind": "coverage", "area": "onderzoeksgebied",
                    "coverage": {"homes_pct": 53, "area_pct": 41}, "rules": ["R1"], "params": {"pixels": 777}}),
        _rec(6, "survey_answer", "bewoners", "antwoorden.xlsx", "Antwoorden", subtheme=None, text="Vaak | geheim",
             extra={"respondent": "r1", "question_key": "Wat mist u?", "selected": ["Vaak"], "open_text": ["OPEN ANTWOORD"]}),
        _rec(7, "expert_statement", theme="Ontmoeten", value=1.0, extra={"statement_number": 9, "statement": "Ander thema"}),
    ])
    return s


def _raw_source_pack(section="Sporten") -> EvidencePack:
    ids = [f"{i:012x}" for i in range(1, 7)]
    ev = [Evidence("X-SP-01", "x", ["Sporten"], "expert", "theme_average", "Gemiddeld 2,7.", {}, ids[:2]),
          Evidence("C-SP-01", "x", ["Sporten"], "cross", "comparison", "Verschil 0,4.", {}, [ids[0], ids[2]]),
          Evidence("B-SP-01", "x", ["Sporten"], "bewoners", "coded", "Behoefte.", {}, [ids[3]], contains_free_text=True),
          Evidence("D-SP-01", "x", ["Sporten"], "data", "map_measurement", "53%.", {}, [ids[4]]),
          Evidence("B-SP-02", "x", ["Sporten"], "bewoners", "choice", "Vaak 1 van 1.", {}, [ids[5]])]
    return EvidencePack("x", section, ev, unavailable=["kaart.png: niet gebruikt"])


class TestE1R(unittest.TestCase):
    def test_records_are_the_union_behind_the_evidence(self):                     # R1
        ids = [r.id for r in records_behind(_raw_source_pack(), _store())]
        self.assertEqual(ids, [f"{i:012x}" for i in range(1, 7)])                 # each once, sheet order, not record 7

    def test_missing_record_is_an_error(self):
        p = _raw_source_pack()
        p.items[0].source_record_ids.append("ffffffffffff")
        with self.assertRaises(ValueError):
            records_behind(p, _store())

    def test_one_table_per_sheet_with_the_record_id_first(self):                  # R2
        md = markdown_tables(records_behind(_raw_source_pack(), _store()))
        self.assertEqual(md.count("### "), 5)                                     # 5 sheets/files
        self.assertIn("### expert.xlsx – Score Testbuurt", md)
        tables = [b.splitlines() for b in md.split("\n\n")]
        seen = []
        for head, columns, sep, *rows in tables:
            self.assertTrue(columns.startswith(f"| {ID_COLUMN} |"))
            width = columns.replace("\\|", "").count("|")
            self.assertEqual(sep.count("|"), width)
            for row in rows:
                self.assertEqual(row.replace("\\|", "").count("|"), width)
                seen.append(row.split("|")[1].strip())
        self.assertEqual(seen, [f"{i:012x}" for i in range(1, 7)])

    def test_original_headers_and_values(self):                                   # R2
        s = _store()
        row = record_row(s.get(f"{1:012x}"))
        self.assertEqual((row["Vraag"], row["A"], row["E"], row["Gemiddelde"]), ("4", "2", "3", "2,6667"))
        self.assertEqual(row["Opmerkingen"], "Twee \\| velden")
        self.assertEqual(record_row(s.get(f"{3:012x}"))["Hoe vaak sport u? › Vaak"], "14")
        self.assertEqual(record_row(s.get(f"{4:012x}"))["Behoefte: Aantal"], "3")
        self.assertEqual(record_row(s.get(f"{5:012x}"))["Toetst RIVM-vuistregel"], "R1")

    def test_no_computed_evidence_or_tool_internals(self):
        md = markdown_tables(records_behind(_raw_source_pack(), _store()))
        for leaked in ("X-SP-01", "Verschil 0,4", "Gemiddeld 2,7", "9,87", "777"):
            self.assertNotIn(leaked, md)

    def test_open_answers_follow_the_quote_setting(self):                         # R1: same quotes setting as E1
        recs = records_behind(_raw_source_pack(), _store())
        self.assertIn("OPEN ANTWOORD", markdown_tables(recs, True))
        self.assertNotIn("OPEN ANTWOORD", markdown_tables(recs, False))

    def test_request_differs_from_e1_only_in_the_data(self):                      # R3
        prov = _Replay([{"claims": []}])
        run_pipeline(_raw_source_pack(), PipelineConfig(**RAW, case_label="Testwijk"), prov, None, _store())
        req = prov.requests[0]
        self.assertIn("Brongegevens:\n### expert.xlsx", req.messages[1].content)
        self.assertIn("kaart.png: niet gebruikt", req.messages[1].content)        # same 'not available' list
        self.assertEqual(req.messages[0].content, system_prompt("v4", "system_raw"))
        self.assertEqual(req.json_schema["properties"]["claims"]["items"]["properties"]["evidence_ids"]["maxItems"], 6)

    def test_claims_cite_record_ids_and_are_verified_on_the_row(self):            # R5
        rid = f"{1:012x}"
        prov = _Replay([{"claims": [_claim("Stelling 4 krijgt van A een 2.", [rid]),
                                    _claim("Het aandeel is 77,8%.", [f"{3:012x}"]),       # 14 of 18: computed
                                    _claim("Onbekend record.", ["X-SP-01"])]}])
        pack, cfg = _raw_source_pack(), PipelineConfig(**RAW)
        d = run_pipeline(pack, cfg, prov, None, _store())
        verify_section(d, *verification_view(pack, cfg, pack.by_id(), _store()))
        self.assertEqual([c.verification["status"] for c in d.claims], ["grounded", "unsupported", "unsupported"])
        self.assertEqual(d.evidence_ids_available, [f"{i:012x}" for i in range(1, 7)])

    def test_resident_text_still_never_goes_to_a_hosted_model(self):
        prov = _Replay([{"claims": []}], location="hosted")
        d = run_pipeline(_raw_source_pack(), PipelineConfig(**RAW), prov, None, _store())
        self.assertIn("resident free text", d.errors[0])
        self.assertEqual(prov.requests, [])
        free = {e.evidence_id for e in raw_pack(_raw_source_pack(), _store()).items if e.contains_free_text}
        self.assertEqual(free, {f"{4:012x}", f"{6:012x}"})

    def test_only_theme_sections(self):                                           # R6
        for section in (GENERAL, CONCLUSIES):
            with self.assertRaises(ValueError):
                run_pipeline(_raw_source_pack(section), PipelineConfig(**RAW), _Replay([]), None, _store())
        with self.assertRaises(ValueError):
            run_pipeline(_raw_source_pack(), PipelineConfig(**RAW), _Replay([]))  # no store


class TestRuns(unittest.TestCase):
    """Both conditions end to end with the fake provider, on a synthetic case folder."""

    @classmethod
    def setUpClass(cls):
        root = write_case_folder(Path(tempfile.mkdtemp()) / "data", with_docx=has_docx())
        cls.ws = Workspace(Path(tempfile.mkdtemp()) / "ws")
        cls.ws.ingest(str(root))

    def _run(self, exp, sections=None):
        with redirect_stdout(io.StringIO()):
            rec = run_experiment(self.ws, exp, cases=[CASE], sections=sections, model="fake", seeds=[42], evaluate=False,
                                 tag="test")
        self.assertEqual(rec["results"][0]["errors"], [])
        run, drafts = load_run(self.ws, CASE, rec["results"][0]["report_run_id"])
        return rec, run, drafts

    def test_e1r_drafts_only_the_six_themes(self):
        # even when asked for every section
        _, run, drafts = self._run("E1R", [*THEMES, GENERAL, CONCLUSIES])
        self.assertLessEqual(set(run["sections"]), set(THEMES))
        self.assertTrue(drafts)
        self.assertEqual(run["totals"]["failed_sections"], [])
        for d in drafts.values():
            cited = [i for c in d.claims for i in c.evidence_ids]
            self.assertTrue(cited)
            self.assertTrue(all(self.ws.store().get(i) is not None for i in cited))
            self.assertTrue(any(c.verification["status"] == "grounded" for c in d.claims))

    def test_e4_totals_reach_the_run_record(self):
        rec, run, drafts = self._run("E4")
        t = run["totals"]
        self.assertGreater(t["revision_sent_back"], 0)
        self.assertGreater(t["revision_withdrawn"], 0)
        self.assertIn("revision_minutes", t)
        self.assertEqual(t["n_claims"], t["revision_claims_before"] - t["revision_withdrawn"])
        self.assertEqual(sum(len(d.revision["claims"]) for d in drafts.values()), t["revision_sent_back"])
        for m in ("unsupported_rate_before", "withdrawal_rate", "revision_calls", "revision_minutes"):
            self.assertIn(m, rec["summary"])
        self.assertTrue(all("revision" in v for v in run["sections"].values() if "n_claims" in v))

    def test_e2_has_no_revision_fields(self):
        rec, run, drafts = self._run("E2")
        self.assertNotIn("revision_calls", run["totals"])
        self.assertNotIn("revision_calls", rec["summary"])
        self.assertTrue(all(d.revision is None for d in drafts.values()))


if __name__ == "__main__":
    unittest.main()
