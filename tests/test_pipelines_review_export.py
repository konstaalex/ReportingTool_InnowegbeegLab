"""UC2 pipelines (with replayed model output), privacy guard, review trail, export gate."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from buurtscan_fixtures import has_docx, write_case_folder  # noqa: E402
from app.services import Workspace  # noqa: E402
from drafting.pipelines import PipelineConfig, run_pipeline  # noqa: E402
from evidence.models import EvidencePack  # noqa: E402
from export.report import ExportBlocked  # noqa: E402
from llm.base import Capabilities, ModelInfo  # noqa: E402
from llm.privacy import pseudonymise  # noqa: E402
from llm.providers import ReplayProvider, extract_json  # noqa: E402
from llm.registry import validate_model_config  # noqa: E402
from review.session import NeedsOverride, ReviewSession  # noqa: E402

CASE = "testwijk-teststad"


def _ws() -> Workspace:
    root = write_case_folder(Path(tempfile.mkdtemp()) / "data", with_docx=has_docx())
    ws = Workspace(Path(tempfile.mkdtemp()) / "ws")
    ws.ingest(str(root))
    return ws


def _replay(outputs, location="local") -> ReplayProvider:
    return ReplayProvider(ModelInfo("replay", "replay", "replay", location, Capabilities(structured_output=True)), outputs)


class TestPipelines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = _ws()
        cls.pack = cls.ws.evidence(CASE)["Sporten"]

    def test_template_is_deterministic_and_grounded(self):
        a = run_pipeline(self.pack, PipelineConfig(pipeline_id="template"))
        b = run_pipeline(self.pack, PipelineConfig(pipeline_id="template"))
        self.assertEqual([c.text for c in a.claims], [c.text for c in b.claims])
        self.assertTrue(all(c.evidence_ids for c in a.claims))

    def test_naive_normalises_model_output(self):
        out = json.dumps({"claims": [
            {"text": "De experts  geven sporten gemiddeld een 2,3.", "type": "finding", "evidence_ids": ["[X-SP-01]"]},
            {"text": "Bewoners zijn kritisch.", "type": "interpretatie", "evidence_ids": "B-SP-01, B-SP-02"},
            {"text": "", "type": "bevinding", "evidence_ids": []},
        ]})
        d = run_pipeline(self.pack, PipelineConfig(pipeline_id="naive"), _replay(["```json\n" + out + "\n```"]))
        self.assertEqual(len(d.claims), 2)
        self.assertEqual(d.claims[0].claim_type, "bevinding")
        self.assertEqual(d.claims[0].evidence_ids, ["X-SP-01"])
        self.assertEqual(d.claims[0].text, "De experts geven sporten gemiddeld een 2,3.")
        self.assertEqual(d.claims[1].evidence_ids, ["B-SP-01", "B-SP-02"])
        self.assertTrue(d.stages[0].raw_output)

    def test_invalid_json_is_recorded_not_crashed(self):
        d = run_pipeline(self.pack, PipelineConfig(pipeline_id="naive"), _replay(["Sorry, ik kan dat niet."]))
        self.assertEqual(d.claims, [])
        self.assertTrue(d.errors)
        self.assertFalse(d.stages[0].parsed_ok)

    def test_privacy_blocks_hosted_resident_text(self):
        d = run_pipeline(self.pack, PipelineConfig(pipeline_id="naive"), _replay(["{}"], location="hosted"))
        self.assertIn("resident free text", d.errors[0])
        self.assertIsNone(d.stages[0].raw_output)          # no call was made

    def test_privacy_override_is_logged(self):
        prov = _replay([json.dumps({"claims": []})], location="hosted")
        d = run_pipeline(self.pack, PipelineConfig(pipeline_id="naive", allow_resident_text_to_hosted=True), prov)
        self.assertTrue(d.stages[0].privacy.startswith("OVERRIDE"))

    def test_hosted_without_quotes_is_allowed(self):
        prov = _replay([json.dumps({"claims": []})], location="hosted")
        d = run_pipeline(self.pack, PipelineConfig(pipeline_id="naive", include_quotes=False), prov)
        self.assertNotIn("OVERRIDE", d.stages[0].privacy)
        self.assertFalse(d.errors)

    def test_staged_runs_one_call_per_perspective_plus_synthesis(self):
        n_groups = len({e.perspective for e in self.pack.items} & {"expert", "bewoners", "data"})
        outs = [json.dumps({"findings": [{"text": "Experts geven gemiddeld een 2,3.", "type": "bevinding", "evidence_ids": ["X-SP-01"]}]})] * n_groups
        outs.append(json.dumps({"claims": [{"text": "Sporten scoort matig.", "type": "conclusie", "evidence_ids": ["X-SP-01"]}]}))
        d = run_pipeline(self.pack, PipelineConfig(pipeline_id="staged"), _replay(outs))
        self.assertEqual(len(d.stages), n_groups + 1)
        self.assertEqual(d.claims[-1].claim_type, "conclusie")

    def test_empty_pack_does_not_draft_from_nothing(self):
        d = run_pipeline(EvidencePack(CASE, "Sporten", []), PipelineConfig(pipeline_id="naive"), _replay([]))
        self.assertTrue(d.errors and not d.claims)

    def test_extract_json(self):
        self.assertEqual(extract_json('Hier: {"a": 1} klaar'), {"a": 1})
        self.assertIsNone(extract_json("geen json"))

    def test_pseudonymise(self):
        t = pseudonymise("Mijn naam is Jan Jansen, mail jan@x.nl, 06 12345678, Dorpsstraat 12, 1234 AB")
        for leaked in ("Jan Jansen", "jan@x.nl", "12345678", "Dorpsstraat 12", "1234 AB"):
            self.assertNotIn(leaked, t)

    def test_model_ram_validation(self):
        self.assertEqual(validate_model_config("local_qwen7b"), [])
        self.assertTrue(validate_model_config("local_gemma12b"))      # > 60% of 16 GB
        self.assertTrue(validate_model_config("local_qwen7b", target_ram_gb=8))


class TestReviewAndExport(unittest.TestCase):
    def setUp(self):
        self.ws = _ws()
        out = json.dumps({"claims": [
            {"text": "De experts geven sporten gemiddeld een 2,3.", "type": "bevinding", "evidence_ids": ["X-SP-01"], "paragraph": 1},
            {"text": "Bewoners willen een beweegtuin.", "type": "interpretatie", "evidence_ids": [], "paragraph": 2},
        ]})
        import llm.registry as reg
        self._orig = reg.get_provider
        import app.services as svc
        svc.get_provider = lambda mid: _replay([out])
        self.draft = self.ws.generate(CASE, "Sporten", PipelineConfig(pipeline_id="naive"), "local_qwen7b", "anouk")
        svc.get_provider = self._orig
        self.s = self.ws.session(CASE, "Sporten")

    def test_flagged_claim_needs_override(self):
        with self.assertRaises(NeedsOverride):
            self.s.approve("SP-02", actor="mika")
        self.s.approve("SP-02", actor="mika", confirm_override=True)
        self.assertTrue(self.s.states()["SP-02"].override)
        self.assertEqual(self.s.events[-1].action, "override_approve")

    def test_edit_keeps_original_and_rechecks(self):
        self.s.edit("SP-02", "Twee bewoners noemen een beweegtuin.", actor="mika", evidence_ids=["B-SP-99"])
        st = self.s.states()["SP-02"]
        self.assertEqual(st.original_text, "Bewoners willen een beweegtuin.")
        self.assertEqual(st.status, "edited")
        self.assertEqual(st.verification["status"], "unsupported")     # B-SP-99 does not exist
        self.assertEqual(self.s.events[-1].before, "Bewoners willen een beweegtuin.")

    def test_tampering_is_detected(self):
        self.s.approve("SP-01", actor="mika")
        self.assertEqual(self.s.verify_chain(), [])
        self.s.events[1].actor = "iemand anders"
        self.assertTrue(self.s.verify_chain())

    def test_persistence_roundtrip(self):
        self.s.approve("SP-01", actor="mika")
        self.ws.save_session(self.s)
        again = self.ws.session(CASE, "Sporten")
        self.assertEqual(again.states()["SP-01"].status, "approved")
        self.assertEqual(again.verify_chain(), [])

    def test_export_blocked_until_reviewed(self):
        with self.assertRaises(ExportBlocked) as cm:
            self.ws.export(CASE, required_sections=["Sporten", "Ontmoeten"])
        reasons = " ".join(cm.exception.reasons)
        self.assertIn("'Sporten': 2 bewering", reasons)
        self.assertIn("'Ontmoeten' heeft nog geen concept", reasons)
        preview = self.ws.export(CASE, preview=True, required_sections=["Sporten"])
        self.assertIn("NIET BEOORDEELD", preview["markdown"].read_text())
        self.s.approve("SP-01", actor="mika")
        self.s.reject("SP-02", actor="mika", comment="niet onderbouwd")
        self.ws.save_session(self.s)
        files = self.ws.export(CASE, required_sections=["Sporten"])
        md = files["markdown"].read_text()
        self.assertIn("De experts geven sporten gemiddeld een 2,3. [X-SP-01]", md)
        self.assertNotIn("beweegtuin", md)
        emap = files["evidence_map"].read_text()
        self.assertIn("Score per indicator", emap)
        self.assertIn("rij 5", emap)

    def test_regenerate_archives_previous_review(self):
        self.s.approve("SP-01", actor="mika")
        self.ws.save_session(self.s)
        self.ws.generate(CASE, "Sporten", PipelineConfig(pipeline_id="template"), None, "anouk")
        archived = self.ws.archived_sessions(CASE)
        self.assertEqual(len(archived), 1)
        old = ReviewSession.load(archived[0])
        self.assertEqual(old.states()["SP-01"].status, "approved")


if __name__ == "__main__":
    unittest.main()
