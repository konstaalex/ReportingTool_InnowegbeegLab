"""Experiment runs on full reports, and what a run costs the machine (time, model memory).
Synthetic data and a stand-in Ollama server only."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from buurtscan_fixtures import has_docx, write_case_folder  # noqa: E402
from app.services import Workspace  # noqa: E402
from llm.base import Capabilities, GenerationRequest, Message, ModelInfo  # noqa: E402

CASE = "testwijk-teststad"
GB = 1024 ** 3


def _ws() -> Workspace:
    root = write_case_folder(Path(tempfile.mkdtemp()) / "data", with_docx=has_docx())
    ws = Workspace(Path(tempfile.mkdtemp()) / "ws")
    ws.ingest(str(root))
    return ws


class FakeOllama:
    """Answers /api/ps, /api/version, /api/generate (unload) and /api/chat like Ollama does."""

    def __init__(self, size_gb: float = 6.0, vram_gb: float = 0.0):
        self.loaded = [{"name": "qwen2.5:7b", "model": "qwen2.5:7b", "size": int(size_gb * GB),
                        "size_vram": int(vram_gb * GB), "context_length": 8192}]
        self.requests: list[tuple[str, dict]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _send(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/ps":
                    self._send({"models": outer.loaded})
                elif self.path == "/api/version":
                    self._send({"version": "0.0-test"})
                else:
                    self._send({})

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n) or b"{}")
                outer.requests.append((self.path, payload))
                if self.path == "/api/generate" and payload.get("keep_alive") == 0:
                    outer.loaded = [m for m in outer.loaded if m["name"] != payload["model"]]
                    self._send({"done": True})
                elif self.path == "/api/chat":
                    self._send({"message": {"content": '{"ok": true}'}, "prompt_eval_count": 1000,
                                "eval_count": 200, "total_duration": 12_000_000_000,
                                "load_duration": 2_000_000_000, "prompt_eval_duration": 4_000_000_000,
                                "eval_duration": 5_000_000_000})
                else:
                    self._send({})

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class TestResources(unittest.TestCase):
    def test_timings_from_ollama_response(self):
        from llm.providers import ollama_timings
        t = ollama_timings({"load_duration": 2e9, "prompt_eval_duration": 4e9, "eval_duration": 5e9,
                            "prompt_eval_count": 1000, "eval_count": 200, "total_duration": 12e9})
        self.assertEqual(t["load_s"], 2.0)
        self.assertEqual(t["prompt_tokens_per_s"], 250.0)
        self.assertEqual(t["output_tokens_per_s"], 40.0)
        self.assertIsNone(ollama_timings({"message": {}}))

    def test_monitor_without_model_server(self):
        from llm.resources import MemoryMonitor
        with MemoryMonitor(base_url=None, interval_s=0.05) as mon:
            _ = [0] * 100000
            import time
            time.sleep(0.2)
        s = mon.summary()
        self.assertGreater(s["samples"], 1)
        self.assertGreater(s["python_peak_mb"], 0)
        self.assertIsNone(s["model_peak_mb"])
        self.assertIsNone(s["within_limit"])
        self.assertAlmostEqual(s["limit_mb"], 0.6 * 16 * 1024, places=1)     # config/models.toml [hardware]

    def test_monitor_reads_model_memory_and_limit(self):
        from llm.resources import MemoryMonitor
        import time
        for size, inside in ((6.0, True), (12.0, False)):            # limit: 0.6 x 16 GB = 9.6 GB
            srv = FakeOllama(size_gb=size)
            try:
                with MemoryMonitor(base_url=srv.url, interval_s=0.05, api_every=1) as mon:
                    time.sleep(0.15)
                s = mon.summary()
            finally:
                srv.close()
            self.assertGreaterEqual(s["ollama_reported_peak_mb"], size * 1024 - 1)
            self.assertGreaterEqual(s["model_peak_mb"], size * 1024 - 1)
            self.assertEqual(s["within_limit"], inside)
            self.assertEqual(s["models_loaded"][0]["processor"], "CPU")          # size_vram 0

    def test_unload_before_a_cold_run(self):
        from llm.resources import ollama_loaded, unload_ollama_models
        srv = FakeOllama()
        try:
            self.assertEqual(unload_ollama_models(srv.url), ["qwen2.5:7b"])
            self.assertEqual(ollama_loaded(srv.url), [])
            self.assertIn(("/api/generate", {"model": "qwen2.5:7b", "keep_alive": 0}), srv.requests)
        finally:
            srv.close()
        self.assertIsNone(ollama_loaded("http://127.0.0.1:9"))                   # nothing listening

    def test_cpu_only_sends_num_gpu_zero_and_keeps_timings(self):
        from llm.providers import OllamaProvider
        srv = FakeOllama()
        try:
            info = ModelInfo("local_qwen7b", "ollama", "qwen2.5:7b", "local", Capabilities(structured_output=True))
            p = OllamaProvider(info, base_url=srv.url, num_ctx=8192)
            req = GenerationRequest(task="t", messages=[Message("user", "hoi")], json_schema={"type": "object"})
            p.generate(req)
            self.assertNotIn("num_gpu", srv.requests[-1][1]["options"])          # default: Ollama decides
            p.num_gpu = 0
            res = p.generate(req)
            self.assertEqual(srv.requests[-1][1]["options"]["num_gpu"], 0)
            self.assertEqual(res.timings["eval_s"], 5.0)
        finally:
            srv.close()


class TestExperimentConfig(unittest.TestCase):
    def test_comparison_runs_full_oss_reports(self):
        from experiments.runner import experiment_definition
        from ingestion.config import load_config
        cfg = load_config("experiments.toml")
        self.assertEqual(cfg["defaults"]["cases"], ["euterpepark-oss"])   # the case with a human-written report
        for exp in cfg["experiment"]:
            d = experiment_definition(exp)
            if exp != "SMOKE":
                self.assertEqual(d["unit"], "report", exp)
            if exp in ("E1", "E2", "E3"):
                self.assertEqual(d["seeds"], [42, 43, 44], exp)          # three repeats per condition


class TestExperimentRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import reports.evaluate as ev
        from experiments.runner import run_experiment
        from test_reports import REFERENCE
        cls.ws = _ws()
        orig = ev._reference
        ev._reference = lambda ws, case_id, pdf=None: REFERENCE                     # synthetic original report
        try:
            with redirect_stdout(io.StringIO()):
                cls.rec = run_experiment(cls.ws, "SMOKE_REPORT", cases=[CASE], tag="pilot-test")
                cls.smoke = run_experiment(cls.ws, "SMOKE", cases=[CASE])
        finally:
            ev._reference = orig

    def test_report_unit_links_scored_report_runs(self):
        rec = self.rec
        self.assertEqual(rec["unit"], "report")
        self.assertEqual(rec["tag"], "pilot-test")
        self.assertEqual(len(rec["results"]), 1)
        r = rec["results"][0]
        self.assertFalse(r["failed"], r["errors"])
        self.assertIn("coverage_of_supportable", r["evaluation"])
        self.assertIn("model_peak_mb", r["totals"])                    # None for the fake model: no model server
        self.assertIn("resources", r)
        run_dir = self.ws.report_run_dir(CASE, r["report_run_id"])
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run["experiment_id"], "SMOKE_REPORT")
        self.assertEqual(run["tag"], "pilot-test")
        self.assertIn("experiments", run["fingerprints"])
        self.assertIn("platform", run["machine"])
        self.assertTrue(r["report_run_id"].endswith("-s42"))
        sm = rec["summary"]
        self.assertEqual(sm["n"], 1)
        self.assertIn("wall_s", sm)
        self.assertIn("coverage_of_supportable", sm)
        self.assertTrue(Path(rec["path"]).is_file())

    def test_section_unit_records_memory(self):
        s = self.smoke
        self.assertEqual(s["unit"], "section")
        self.assertIn("resources", s)
        self.assertGreater(s["peak_rss_mb_python"], 0)
        self.assertIsNone(s["summary"]["within_memory_limit"])

    def test_summary_filters_pilot_runs(self):
        from experiments.cli import main
        from experiments.runner import summarise_runs
        self.assertEqual({r["experiment"] for r in summarise_runs(self.ws, tag="pilot-test")}, {"SMOKE_REPORT"})
        self.assertEqual({r["experiment"] for r in summarise_runs(self.ws, tag="")}, {"SMOKE"})
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["summary", "--workspace", str(self.ws.root)]), 0)
        self.assertIn("SMOKE_REPORT", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
