"""The review screen runs without errors on a synthetic workspace; the expert switch hides the research tabs."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from buurtscan_fixtures import has_docx, write_case_folder  # noqa: E402
from app import jobs  # noqa: E402
from app.services import Workspace  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "src" / "ui" / "streamlit_app.py")
CASE = "testwijk-teststad"

try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # pragma: no cover
    AppTest = None


@unittest.skipIf(AppTest is None, "streamlit not installed")
class TestApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = write_case_folder(Path(tempfile.mkdtemp()) / "data", with_docx=has_docx())
        cls.ws_dir = Path(tempfile.mkdtemp()) / "ws"
        ws = Workspace(cls.ws_dir)
        ws.ingest(str(root))
        job = jobs.start_job(ws, CASE, None, "template", "v3", ["Sporten", "Conclusies"], launch=False)
        jobs.run_job(ws, job["job_id"])

    def app(self, expert=False):
        os.environ["INNO_WORKSPACE"] = str(self.ws_dir)
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        if expert:
            at.sidebar.checkbox[0].check().run()
        return at

    def test_normal_flow_has_four_tabs_and_no_errors(self):
        at = self.app()
        self.assertFalse(at.exception, at.exception)
        labels = [t.label for t in at.tabs]                     # top-level and nested tabs, in page order
        top = [x for x in labels if x[:1].isdigit()]
        self.assertEqual(top, ["1 · Data", "2 · Concept maken", "3 · Beoordelen", "4 · Exporteren"])
        self.assertFalse(any(t.label.startswith(("5 ·", "6 ·", "1b ·")) for t in at.tabs))
        self.assertTrue(any("Rapport klaar" in s.value for s in at.success))          # the finished job, tab 2

    def test_expert_mode_shows_research_tabs(self):
        at = self.app(expert=True)
        self.assertFalse(at.exception, at.exception)
        labels = [t.label for t in at.tabs]
        for t in ("1b · Afbeeldingen", "5 · Rapport per model", "6 · Experimenten"):
            self.assertIn(t, labels)

    def test_staged_is_the_default_and_e4_is_offered(self):
        at = self.app()
        radio = next(r for r in at.radio if r.key == "job_pipe")
        self.assertEqual(radio.value, "staged")
        self.assertEqual(radio.options, ["Per perspectief + synthese (aanbevolen)", "Per perspectief + controle",
                                         "Eén opdracht per onderdeel", "Sjabloon zonder taalmodel"])


if __name__ == "__main__":
    unittest.main()
