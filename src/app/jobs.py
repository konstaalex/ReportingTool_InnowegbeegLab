"""Full reports as background jobs: the report keeps running when the browser tab is closed.

A job is one full report (all sections, one model, one pipeline) drafted in its own process by the
unchanged reports.runs.generate_report_run, so a job produces exactly what an experiment run does.
The job writes its state to <workspace>/jobs/<job_id>.json:

    status      running | done | failed | interrupted | resumed
    sections    {section: pending | running | done | failed | skipped}
    heartbeat   updated every few seconds while the process lives

One job at a time per workspace. A job is "interrupted" when its process is gone (laptop shut down,
process killed) or when it notices the laptop slept (a gap in its own heartbeat): then it stops
itself, because a model call that spanned a sleep cannot be trusted. Resume starts a new job for the
sections without a finished draft and merges both parts into one report.

    python -m app.jobs run <workspace> <job_id>          # what start_job() launches
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

HEARTBEAT_S = float(os.environ.get("INNO_JOB_HEARTBEAT_S", 5))
STALE_S = 60.0              # no heartbeat for this long and the process is gone: interrupted
SLEEP_GAP_S = float(os.environ.get("INNO_JOB_SLEEP_GAP_S", 120))   # the clock jumped this far between beats: slept
ACTIVE = ("starting", "running")


class JobRunning(Exception):
    pass


def jobs_dir(ws) -> Path:
    d = ws.p("jobs")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(ws, job_id: str) -> Path:
    return jobs_dir(ws) / f"{job_id}.json"


def _write(ws, job: dict) -> None:
    p = _path(ws, job["job_id"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def load(ws, job_id: str) -> Optional[dict]:
    p = _path(ws, job_id)
    if not p.exists():
        return None
    for _ in range(5):                       # the job process may be replacing the file right now
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, PermissionError):
            time.sleep(0.1)
    return None


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259                                             # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def status(ws, job_id: str, now: Optional[float] = None) -> Optional[dict]:
    """The job as stored, with an active job whose process is gone shown as interrupted."""
    job = load(ws, job_id)
    if job is None:
        return None
    now = time.time() if now is None else now
    if job["status"] in ACTIVE and now - job.get("heartbeat", job["created"]) > STALE_S and not pid_alive(job.get("pid")):
        job["status"] = "interrupted"
        job["interrupted_reason"] = job.get("interrupted_reason") or "het proces draait niet meer (laptop uit of gestopt)"
        _write(ws, job)
    return job


def all_jobs(ws) -> list[dict]:
    out = [status(ws, p.stem) for p in jobs_dir(ws).glob("*.json")]
    return sorted((j for j in out if j), key=lambda j: (j.get("created", 0), j["job_id"]))


def active_job(ws) -> Optional[dict]:
    return next((j for j in all_jobs(ws) if j["status"] in ACTIVE), None)


def latest_job(ws, case_id: Optional[str] = None) -> Optional[dict]:
    js = [j for j in all_jobs(ws) if case_id is None or j["case_id"] == case_id]
    return js[-1] if js else None


def _launch(ws, job: dict) -> None:
    src = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(src), "PYTHONUTF8": "1"}
    log = open(jobs_dir(ws) / f"{job['job_id']}.log", "a", encoding="utf-8")
    flags = 0
    if os.name == "nt":                     # own process group, no console: survives the browser and the UI
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    p = subprocess.Popen([sys.executable, "-m", "app.jobs", "run", str(ws.root), job["job_id"]],
                         cwd=str(src.parent), env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True)
    job["pid"] = p.pid
    _write(ws, job)


def new_job(ws, case_id: str, model_id: Optional[str], pipeline: str, prompt_version: str,
            sections: list[str], reviewer: str = "", resumed_from: Optional[str] = None) -> dict:
    now = time.time()
    return {"job_id": time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4], "case_id": case_id,
            "model_id": model_id, "pipeline": pipeline, "prompt_version": prompt_version, "reviewer": reviewer,
            "status": "starting", "created": now, "heartbeat": now, "sections": {s: "pending" for s in sections},
            "current": None, "run_id": None, "resumed_from": resumed_from, "error": None, "pid": None,
            "sleep_gaps": []}


def start_job(ws, case_id: str, model_id: Optional[str], pipeline: str = "staged", prompt_version: str = "v3",
              sections: Optional[list[str]] = None, reviewer: str = "", launch: bool = True) -> dict:
    """Start a full report in its own process. Refuses while another job is active (one at a time)."""
    from reports.runs import REPORT_SECTIONS
    running = active_job(ws)
    if running:
        raise JobRunning(f"er loopt al een rapport ({running['job_id']}, {running['case_id']})")
    job = new_job(ws, case_id, None if pipeline == "template" else model_id, pipeline, prompt_version,
                  list(sections or REPORT_SECTIONS), reviewer)
    _write(ws, job)
    if launch:
        _launch(ws, job)
    return job


def finished_sections(ws, job: dict) -> set[str]:
    """Sections of this job (and the jobs it resumed) with a finished draft on disk."""
    from reports.runs import _slug, run_path
    done = set()
    j = job
    while j:
        if j.get("run_id"):
            d = run_path(ws, j["case_id"], j["run_id"]) / "drafts"
            done |= {s for s in j["sections"] if (d / f"{_slug(s)}.json").exists()}
        j = load(ws, j["resumed_from"]) if j.get("resumed_from") else None
    return done


def resume_job(ws, job_id: str, reviewer: str = "", launch: bool = True) -> dict:
    """A new job for the sections of an interrupted job that have no finished draft yet."""
    old = status(ws, job_id)
    if old is None or old["status"] != "interrupted":
        raise ValueError("alleen een onderbroken rapport kan worden hervat")
    if active_job(ws):
        raise JobRunning("er loopt al een rapport")
    todo = [s for s in old["sections"] if s not in finished_sections(ws, old)]
    job = new_job(ws, old["case_id"], old["model_id"], old["pipeline"], old["prompt_version"],
                  list(old["sections"]), reviewer or old.get("reviewer", ""), resumed_from=job_id)
    for s in old["sections"]:
        job["sections"][s] = "pending" if s in todo else "done"
    job["todo"] = todo
    _write(ws, job)
    old["status"] = "resumed"
    old["resumed_by"] = job["job_id"]
    _write(ws, old)
    if launch:
        _launch(ws, job)
    return job


# ------------------------------------------------------------------ the job process

def _keep_awake() -> None:
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)          # ES_CONTINUOUS | ES_SYSTEM_REQUIRED


def _merge_with_earlier(ws, job: dict, run: dict) -> dict:
    """Put the finished drafts of the interrupted part(s) into this run, recompute the totals and render
    the Word report again. Each section is drafted independently, so the merged report holds the same
    drafts as one uninterrupted run would."""
    import shutil
    from drafting.models import SectionDraft
    from reports.runs import _slug, run_path, REPORT_SECTIONS
    out = run_path(ws, job["case_id"], run["run_id"])
    parts, j = [], load(ws, job["resumed_from"])
    while j:
        if j.get("run_id"):
            parts.append(j)
        j = load(ws, j["resumed_from"]) if j.get("resumed_from") else None
    wall = run["totals"]["wall_s"]
    for part in parts:
        src = run_path(ws, part["case_id"], part["run_id"]) / "drafts"
        same_dir = src.resolve() == (out / "drafts").resolve()    # both parts started in the same second
        for sec in part["sections"]:
            f = src / f"{_slug(sec)}.json"
            if sec in run["sections"] or not f.exists():
                continue
            if not same_dir:
                shutil.copyfile(f, out / "drafts" / f.name)
            d = SectionDraft.from_dict(json.loads(f.read_text(encoding="utf-8")))
            run["sections"][sec] = {"draft_id": d.draft_id, "n_claims": len(d.claims), "errors": d.errors,
                                    "from_run": part["run_id"]}
        wall += part.get("wall_s") or max(0.0, part.get("heartbeat", 0) - part.get("started", part.get("heartbeat", 0)))
    run["sections"] = {s: run["sections"][s] for s in REPORT_SECTIONS if s in run["sections"]}
    drafts = {}
    for sec in run["sections"]:
        f = out / "drafts" / f"{_slug(sec)}.json"
        if f.exists():
            drafts[sec] = SectionDraft.from_dict(json.loads(f.read_text(encoding="utf-8")))
    claims = [c for d in drafts.values() for c in d.claims]
    st = [(c.verification or {}).get("status") for c in claims]
    t = run["totals"]
    t.update({"sections_drafted": len(drafts), "n_claims": len(claims), "wall_s": round(wall, 1),
              "grounded_rate": round(st.count("grounded") / len(claims), 3) if claims else None,
              "weak_rate": round(st.count("weak") / len(claims), 3) if claims else None,
              "unsupported_rate": round(st.count("unsupported") / len(claims), 3) if claims else None,
              "failed_sections": [s for s, v in run["sections"].items() if v.get("error") or v.get("errors")]})
    run["usable"] = bool(claims)
    run["resumed_from_runs"] = [p["run_id"] for p in parts]
    if drafts:
        from export.content import from_drafts
        from export.docx_report import render_docx
        label = run.get("model_id") or "template"
        content = from_drafts(ws, job["case_id"], drafts, {
            "label": f"{label} · {run['pipeline']}", "model": (run.get("model") or {}).get("model", "geen (template)"),
            "pipeline": run["pipeline"], "prompt_version": run["prompt_version"], "run_id": run["run_id"],
            "source": "onbeoordeeld concept van één model (hervat rapport)"})
        docx, figures = render_docx(content, out, filename=f"{content.report_kind.lower()}_{job['case_id']}_{label}.docx")
        run["files"] = {"docx": docx.name}
        run["figures"] = [f.__dict__ for f in figures]
    (out / "run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")
    return run


def run_job(ws, job_id: str, stop_on_sleep: bool = True) -> dict:
    """Body of the job process: draft the report and keep the job file up to date."""
    from reports.runs import _slug, generate_report_run, runs_dir
    job = load(ws, job_id)
    job.update(status="running", pid=os.getpid(), started=time.time(), heartbeat=time.time())
    _write(ws, job)
    _keep_awake()
    lock = threading.Lock()
    stop = threading.Event()

    def beat():
        last = time.time()
        while not stop.wait(HEARTBEAT_S):
            now = time.time()
            with lock:
                job["heartbeat"] = now
                if now - last > SLEEP_GAP_S:
                    job["sleep_gaps"].append({"at": now, "gap_s": round(now - last)})
                    if stop_on_sleep:
                        job.update(status="interrupted",
                                   interrupted_reason=f"de laptop sliep {round((now - last) / 60)} minuten")
                        _write(ws, job)
                        os._exit(3)
                _write(ws, job)
            last = now

    threading.Thread(target=beat, daemon=True).start()
    before = {p.name for p in runs_dir(ws, job["case_id"]).glob("*")} if runs_dir(ws, job["case_id"]).exists() else set()

    def progress(msg: str) -> None:
        sec = msg.split(": ", 1)[-1]
        with lock:
            if not job["run_id"]:
                new = [p.name for p in runs_dir(ws, job["case_id"]).glob("*") if p.name not in before and p.is_dir()]
                job["run_id"] = sorted(new)[-1] if new else None
            prev = job.get("current")
            if prev and job["run_id"]:
                d = runs_dir(ws, job["case_id"]) / job["run_id"] / "drafts" / f"{_slug(prev)}.json"
                job["sections"][prev] = "done" if d.exists() else "failed"
            job["current"] = sec
            job["sections"][sec] = "running"
            _write(ws, job)

    todo = job.get("todo") or list(job["sections"])
    try:
        run = generate_report_run(ws, job["case_id"], job["model_id"], pipeline=job["pipeline"],
                                  prompt_version=job["prompt_version"], sections=todo, progress=progress)
        if job.get("resumed_from"):
            run = _merge_with_earlier(ws, job, run)
        with lock:
            job["run_id"] = run["run_id"]
            for s in todo:
                info = run["sections"].get(s, {})
                job["sections"][s] = ("skipped" if info.get("skipped") else
                                      "failed" if info.get("error") or info.get("errors") or s not in run["sections"]
                                      else "done")
            job.update(status="done", current=None, finished=time.time(), wall_s=run["totals"]["wall_s"],
                       n_claims=run["totals"]["n_claims"], failed_sections=run["totals"]["failed_sections"])
            _write(ws, job)
    except Exception as exc:  # noqa: BLE001 - recorded in the job file for the UI
        with lock:
            job.update(status="failed", error=f"{type(exc).__name__}: {exc}", finished=time.time())
            if job.get("current"):
                job["sections"][job["current"]] = "failed"
            _write(ws, job)
    finally:
        stop.set()
    return job


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or argv[0] != "run":
        print(__doc__)
        return 2
    from app.services import Workspace
    job = run_job(Workspace(Path(argv[1])), argv[2])
    return 0 if job["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
