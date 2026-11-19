"""Fixes from the week-1 pilot before the freeze (docs/experiments/week1-pilot.md).

P7: the model may cite at most 6 evidence ids per claim (prompt v3 and the JSON schema).
P8: only changes to code, prompts or configuration mark a run record "-dirty".
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from drafting.pipelines import CLAIMS_SCHEMA, PipelineConfig, claims_schema, run_pipeline  # noqa: E402
from drafting.prompts import MAX_EVIDENCE_IDS, evidence_id_cap, system_prompt  # noqa: E402
from evidence.models import Evidence, EvidencePack  # noqa: E402
from experiments import provenance  # noqa: E402
from llm.base import Capabilities, ModelInfo  # noqa: E402
from llm.providers import FakeProvider  # noqa: E402


def _ids_cap(schema: dict, key: str) -> int | None:
    return schema["properties"][key]["items"]["properties"]["evidence_ids"].get("maxItems")


class _Recording(FakeProvider):
    """FakeProvider that keeps every request, so a test can see the schema a pipeline sent."""

    def __init__(self):
        super().__init__(ModelInfo("fake", "fake", "fake", "local", Capabilities(structured_output=True)))
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return super().generate(request)


def _pack() -> EvidencePack:
    ev = [Evidence("X-SP-01", "x", ["Sporten"], "expert", "expert_statement", "Experts geven 2,4 voor sportplekken.",
                   {}, ["r1"]),
          Evidence("B-SP-01", "x", ["Sporten"], "bewoners", "rating", "Bewoners (65+) geven 6,1.", {}, ["r2"]),
          Evidence("D-SP-01", "x", ["Sporten"], "data", "map_measurement", "53% van de woningen binnen 200 m.",
                   {}, ["r3"])]
    return EvidencePack("x", "Sporten", ev)


class TestEvidenceIdCap(unittest.TestCase):              # P7
    def test_cap_per_prompt_version(self):
        self.assertIsNone(evidence_id_cap("v1"))
        self.assertIsNone(evidence_id_cap("v2"))
        self.assertEqual(evidence_id_cap("v3"), MAX_EVIDENCE_IDS)
        self.assertEqual(evidence_id_cap("v4"), MAX_EVIDENCE_IDS)
        self.assertIsNone(evidence_id_cap("experimental"))

    def test_schema_has_max_items_from_v3_only(self):
        self.assertIsNone(_ids_cap(claims_schema("v2"), "claims"))
        self.assertEqual(_ids_cap(claims_schema("v3"), "claims"), 6)
        self.assertEqual(_ids_cap(claims_schema("v3", "findings"), "findings"), 6)
        self.assertIsNone(_ids_cap(CLAIMS_SCHEMA, "claims"), "the shared base schema must not be changed")

    def test_prompt_states_the_cap_and_v2_is_unchanged(self):
        self.assertIn("hoogstens 6", system_prompt("v3"))
        self.assertNotIn("hoogstens", system_prompt("v2"))
        self.assertNotIn("$max_ids", system_prompt("v3"))

    def test_v3_is_the_default(self):
        self.assertEqual(PipelineConfig().prompt_version, "v3")

    def test_every_model_call_sends_the_capped_schema(self):
        for pipeline in ("naive", "staged"):
            prov = _Recording()
            d = run_pipeline(_pack(), PipelineConfig(pipeline_id=pipeline, prompt_version="v3"), prov)
            self.assertFalse(d.errors, d.errors)
            self.assertTrue(prov.requests)
            for req in prov.requests:
                key = "findings" if req.task.startswith("staged_findings") else "claims"
                self.assertEqual(_ids_cap(req.json_schema, key), 6, f"{pipeline}/{req.task}")


class TestDirtyOnlyForRunRelevantChanges(unittest.TestCase):    # P8
    def test_parse_porcelain_z(self):
        status = " M docs/notes.md\0?? prompts/v3/\0R  src/new name.py\0src/old.py\0 M tests/test_x.py\0"
        self.assertEqual(provenance.changed_paths(status),
                         ["docs/notes.md", "prompts/v3/", "src/new name.py", "src/old.py", "tests/test_x.py"])

    def test_which_paths_count(self):
        paths = ["docs/experiments/week1-pilot.md", "README.md", "tests/test_x.py", "scripts/windows_timing.ps1",
                 "src/drafting/pipelines.py", "prompts/v3/system.txt", "config/experiments.toml", "requirements.txt"]
        self.assertEqual(provenance.run_relevant(paths),
                         ["src/drafting/pipelines.py", "prompts/v3/system.txt", "config/experiments.toml",
                          "requirements.txt"])

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_git_commit_in_a_real_repository(self):
        root = Path(tempfile.mkdtemp())

        def git(*args):
            subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

        git("init", "-q")
        for rel in ("src/a.py", "docs/a.md"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x\n", encoding="utf-8")
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        with mock.patch.object(provenance, "REPO_ROOT", root):
            self.assertFalse(provenance.git_commit().endswith("-dirty"))
            (root / "docs/a.md").write_text("edited during a run\n", encoding="utf-8")
            (root / "docs/new.md").write_text("new\n", encoding="utf-8")
            self.assertFalse(provenance.git_commit().endswith("-dirty"), "documentation must not make a run dirty")
            (root / "src/a.py").write_text("changed\n", encoding="utf-8")
            self.assertTrue(provenance.git_commit().endswith("-dirty"), "a code change must make a run dirty")


if __name__ == "__main__":
    unittest.main()
