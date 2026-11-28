"""The demo flow for researchers: full report as a background job (with resume), adopting a report for
review, and the traceability check of the exported Word report. Synthetic data and the fake model only."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from buurtscan_fixtures import has_docx, write_case_folder  # noqa: E402
from app import jobs  # noqa: E402
from app.services import Workspace  # noqa: E402

CASE = "testwijk-teststad"
SECS = ["Sporten", "Actieve mobiliteit", "Conclusies"]


def _ws() -> Workspace:
    root = write_case_folder(Path(tempfile.mkdtemp()) / "data", with_docx=has_docx())
    ws = Workspace(Path(tempfile.mkdtemp()) / "ws")
    ws.ingest(str(root))
    return ws


def _approve_all(ws, reviewer="tester", reject_uncited=False):
    for sec, s in ws.sessions(CASE).items():
        for cid, st in s.states().items():
            if reject_uncited and not st.evidence_ids:
                s.reject(cid, actor=reviewer)
                continue
            flagged = (st.verification or {}).get("status") in ("weak", "unsupported")
            s.approve(cid, actor=reviewer, confirm_override=flagged)
        ws.save_session(s)


def _drafts(ws, run_id):
    from reports.runs import load_run
    _, d = load_run(ws, CASE, run_id)
    return {s: [(c.text, c.evidence_ids) for c in x.claims] for s, x in d.items()}


class TestJobs(unittest.TestCase):
    def test_job_drafts_the_same_report_as_a_direct_run(self):
        from reports.runs import generate_report_run
        ws = _ws()
        job = jobs.start_job(ws, CASE, "fake", "staged", "v3", SECS, launch=False)
        done = jobs.run_job(ws, job["job_id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(set(done["sections"].values()), {"done"})
        direct = generate_report_run(ws, CASE, "fake", pipeline="staged", sections=SECS)
        self.assertEqual(_drafts(ws, done["run_id"]), _drafts(ws, direct["run_id"]))
        self.assertIn(done["run_id"], {r["run_id"] for r in ws.report_runs(CASE)})

    def test_one_job_at_a_time(self):
        ws = _ws()
        job = jobs.start_job(ws, CASE, "fake", "staged", "v3", SECS, launch=False)
        job["pid"] = jobs.os.getpid()                    # this process: alive
        jobs._write(ws, job)
        with self.assertRaises(jobs.JobRunning):
            jobs.start_job(ws, CASE, "fake", "staged", "v3", SECS, launch=False)

    def test_dead_process_is_interrupted_and_resume_skips_finished_sections(self):
        ws = _ws()
        first = jobs.start_job(ws, CASE, "fake", "staged", "v3", SECS, launch=False)
        first = jobs.run_job(ws, first["job_id"])          # finish, then pretend the last section never finished
        from reports.runs import _slug, run_path
        rd = run_path(ws, CASE, first["run_id"])
        (rd / "run.json").unlink()
        (rd / "drafts" / f"{_slug('Conclusies')}.json").unlink()
        first.update(status="running", pid=999999, heartbeat=time.time() - 3600)
        jobs._write(ws, first)
        st = jobs.status(ws, first["job_id"])
        self.assertEqual(st["status"], "interrupted")
        self.assertEqual(jobs.finished_sections(ws, st), {"Sporten", "Actieve mobiliteit"})
        second = jobs.resume_job(ws, first["job_id"], launch=False)
        self.assertEqual(second["todo"], ["Conclusies"])
        second = jobs.run_job(ws, second["job_id"])
        self.assertEqual(second["status"], "done", second.get("error"))
        from reports.runs import load_run
        run, drafts = load_run(ws, CASE, second["run_id"])
        self.assertEqual(set(drafts), set(SECS))
        self.assertEqual(run["resumed_from_runs"], [first["run_id"]])
        self.assertEqual(run["totals"]["n_claims"], sum(len(d.claims) for d in drafts.values()))
        self.assertTrue((run_path(ws, CASE, second["run_id"]) / run["files"]["docx"]).is_file())
        self.assertEqual(jobs.load(ws, first["job_id"])["status"], "resumed")

    def test_a_job_runs_in_its_own_process(self):
        ws = _ws()
        job = jobs.start_job(ws, CASE, "fake", "staged", "v3", ["Sporten"])
        for _ in range(300):
            j = jobs.status(ws, job["job_id"])
            if j["status"] not in jobs.ACTIVE:
                break
            time.sleep(0.2)
        self.assertEqual(j["status"], "done", (jobs.jobs_dir(ws) / f"{job['job_id']}.log").read_text(encoding="utf-8"))
        self.assertNotEqual(j["pid"], jobs.os.getpid())


    def test_a_job_that_notices_a_sleep_stops_as_interrupted(self):
        import os
        ws = _ws()
        os.environ.update(INNO_JOB_SLEEP_GAP_S="0", INNO_JOB_HEARTBEAT_S="0.01")   # every beat looks like a wake-up
        try:
            job = jobs.start_job(ws, CASE, "fake", "staged", "v3", SECS)
        finally:
            del os.environ["INNO_JOB_SLEEP_GAP_S"], os.environ["INNO_JOB_HEARTBEAT_S"]
        for _ in range(150):
            j = jobs.status(ws, job["job_id"])
            if j["status"] not in jobs.ACTIVE:
                break
            time.sleep(0.2)
        if j["status"] == "done":
            self.skipTest("the fake report finished before the first heartbeat")
        self.assertEqual(j["status"], "interrupted")
        self.assertIn("sliep", j["interrupted_reason"])
        self.assertTrue(j["sleep_gaps"])


class TestAdoptAndTrace(unittest.TestCase):
    def test_adopt_a_run_from_experiments_cli(self):
        from experiments.runner import run_experiment
        ws = _ws()
        rec = run_experiment(ws, "E2", cases=[CASE], sections=SECS, model="fake", seeds=[42], verbose=False,
                             evaluate=False, tag="test")
        rid = rec["results"][0]["report_run_id"]
        secs = ws.adopt_report_run(CASE, rid, "tester")
        self.assertEqual(set(secs), set(SECS))
        self.assertEqual(set(ws.sessions(CASE)), set(SECS))

    @unittest.skipUnless(has_docx(), "python-docx needed")
    def test_job_report_adopted_reviewed_exported_and_traceable(self):
        ws = _ws()
        job = jobs.run_job(ws, jobs.start_job(ws, CASE, "fake", "staged", "v3", SECS, launch=False)["job_id"])
        ws.adopt_report_run(CASE, job["run_id"], "tester")
        self.assertTrue(ws.export_blockers(CASE, SECS))               # nothing reviewed yet: blocked
        _approve_all(ws)                                             # overrides, also of claims without a source
        files = ws.export(CASE, required_sections=SECS)
        uncited = ws.trace_check(CASE, files, SECS)
        self.assertFalse(uncited["ok"])
        self.assertTrue(all(p.endswith("geen bewijsstuk") for p in uncited["problems"]), uncited["problems"])
        ws.adopt_report_run(CASE, job["run_id"], "tester")           # again, as a researcher would review it
        _approve_all(ws, reject_uncited=True)
        self.assertEqual(ws.export_blockers(CASE, SECS), [])
        files = ws.export(CASE, required_sections=SECS)
        res = ws.trace_check(CASE, files, SECS)
        self.assertTrue(res["ok"], res["problems"])
        self.assertGreater(res["n_claims"], 0)
        self.assertGreater(res["n_source_records"], 0)

    @unittest.skipUnless(has_docx(), "python-docx needed")
    def test_trace_check_finds_problems(self):
        ws = _ws()
        ws.adopt_report_run(CASE, jobs.run_job(ws, jobs.start_job(ws, CASE, "template", "template", "v3", SECS,
                                                                    launch=False)["job_id"])["run_id"], "tester")
        s = ws.session(CASE, "Sporten")
        s.add_claim("Een eigen zin zonder bron.", "finding", [], actor="tester", paragraph=1)
        ws.save_session(s)
        _approve_all(ws)
        files = ws.export(CASE, required_sections=SECS)
        index = dict(ws.evidence_index(CASE))
        some = next(iter(json.loads(json.dumps(sorted(index)))))
        index.pop(some)                                              # an evidence item that no longer resolves
        from app.trace import check_export
        res = check_export(files, index, ws.store(), SECS)
        self.assertFalse(res["ok"])
        self.assertTrue(any("geen bewijsstuk" in p for p in res["problems"]))
        clean = ws.trace_check(CASE, files, SECS)
        self.assertEqual([p for p in clean["problems"] if "geen bewijsstuk" not in p], [])


if __name__ == "__main__":
    unittest.main()
