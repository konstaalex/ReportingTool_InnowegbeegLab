"""What a run costs the machine: memory of the model server, not only of Python (NFR5, SQ4).

Python's own peak (resource.getrusage) misses the model completely, because Ollama runs
the model in its own processes. While a run drafts, MemoryMonitor samples once a second:

  - the resident memory (RSS) of all Ollama processes (server + model runner);
  - what Ollama itself reports per loaded model (GET /api/ps: size, size_vram);
  - Python's own resident memory;
  - the memory in use on the whole machine (needs psutil), against a baseline taken
    just before the run.

model_peak_mb = the larger of the Ollama RSS peak and the peak Ollama reports; it is
compared with the limit in config/models.toml [hardware]: max_model_share_of_ram x
target_ram_gb = 0.6 x 16 GB = 9.6 GB.

Caveat for Apple silicon: with the GPU (Metal) in use, part of the model sits in GPU
buffers that the process RSS may not show in full; the size Ollama reports is then the
better number. On the CPU-only target laptop both should agree. size_vram = 0 in
/api/ps means the model ran fully on the CPU.

All memory values are MB = 1024 x 1024 bytes.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

try:  # optional: system memory and a cheaper process scan; falls back to `ps` without it
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - depends on the environment
    psutil = None

MB = 1024 * 1024


# ------------------------------------------------------------------ Ollama API

def _get_json(url: str, timeout: float = 2.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def ollama_loaded(base_url: str) -> Optional[list[dict]]:
    """Models Ollama has in memory now (GET /api/ps), or None when Ollama does not answer."""
    data = _get_json(f"{base_url.rstrip('/')}/api/ps")
    if data is None:
        return None
    out = []
    for m in data.get("models", []):
        size, vram = m.get("size") or 0, m.get("size_vram") or 0
        out.append({"name": m.get("name") or m.get("model"), "size_mb": round(size / MB, 1),
                    "size_vram_mb": round(vram / MB, 1),
                    "processor": "CPU" if not vram else ("GPU" if vram >= size else f"{round(100 * vram / size)}% GPU"),
                    "context_length": m.get("context_length")})
    return out


def ollama_version(base_url: str) -> Optional[str]:
    data = _get_json(f"{base_url.rstrip('/')}/api/version")
    return data.get("version") if data else None


def unload_ollama_models(base_url: str) -> list[str]:
    """Unload every model Ollama keeps in memory (keep_alive 0), so the next run starts cold:
    its time then includes loading the model, and its memory is its own."""
    names = [m["name"] for m in (ollama_loaded(base_url) or []) if m.get("name")]
    for name in names:
        body = json.dumps({"model": name, "keep_alive": 0}).encode("utf-8")
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
        except (urllib.error.URLError, OSError):
            pass
    # unloading is asynchronous: wait until /api/ps no longer lists them (max ~10 s)
    for _ in range(20):
        if not [m for m in (ollama_loaded(base_url) or []) if m.get("name") in names]:
            break
        time.sleep(0.5)
    return names


# ------------------------------------------------------------------ processes

def _ollama_rss_mb() -> tuple[Optional[float], int]:
    """Summed RSS of all processes whose name or executable contains 'ollama' (server, runner,
    and on macOS the small menu-bar app). Returns (MB or None if not measurable, n processes)."""
    me = os.getpid()
    if psutil is not None:
        total, n = 0, 0
        for p in psutil.process_iter(["pid", "name", "exe", "memory_info"]):
            try:
                info = p.info
                label = f"{info.get('name') or ''} {info.get('exe') or ''}".lower()
                if info["pid"] != me and "ollama" in label and info.get("memory_info"):
                    total += info["memory_info"].rss
                    n += 1
            except (psutil.Error, KeyError, TypeError):  # process ended or access denied
                continue
        return round(total / MB, 1), n
    try:  # macOS and Linux both have ps
        out = subprocess.run(["ps", "-A", "-o", "pid=,rss=,comm="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None, 0
    total_kb, n = 0, 0
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and int(parts[0]) != me and "ollama" in parts[2].lower():
            total_kb += int(parts[1]) if parts[1].isdigit() else 0
            n += 1
    return round(total_kb * 1024 / MB, 1), n


def _python_rss_mb() -> float:
    if psutil is not None:
        return round(psutil.Process().memory_info().rss / MB, 1)
    import resource
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss          # peak, bytes on macOS, KB on Linux
    return round(r / MB if sys.platform == "darwin" else r * 1024 / MB, 1)


def _system_used_mb() -> Optional[float]:
    if psutil is None:
        return None
    vm = psutil.virtual_memory()
    return round((vm.total - vm.available) / MB, 1)


def memory_limit_mb() -> tuple[float, str]:
    """The model memory limit from config/models.toml [hardware] (NFR5)."""
    from ingestion.config import load_config
    hw = load_config("models.toml").get("hardware", {})
    ram, share = hw.get("target_ram_gb", 16), hw.get("max_model_share_of_ram", 0.6)
    return round(ram * share * 1024, 1), f"{share:.0%} of {ram} GB (config/models.toml [hardware])"


def machine_info() -> dict:
    """Enough to tell runs on different machines apart (Mac with GPU vs. the 16 GB CPU-only laptop)."""
    info = {"platform": platform.platform(), "machine": platform.machine(), "processor": platform.processor(),
            "python": platform.python_version(), "cpu_count_logical": os.cpu_count()}
    if psutil is not None:
        info["cpu_count_physical"] = psutil.cpu_count(logical=False)
        info["ram_total_mb"] = round(psutil.virtual_memory().total / MB, 1)
    elif sys.platform == "darwin":
        try:
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
            info["ram_total_mb"] = round(int(out.strip()) / MB, 1)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True,
                                 timeout=5).stdout.strip()
            if out:
                info["cpu"] = out
        except (OSError, subprocess.SubprocessError):
            pass
    return info


# ------------------------------------------------------------------ monitor

class MemoryMonitor:
    """Samples memory in a background thread while a run drafts.

        with MemoryMonitor(base_url="http://localhost:11434") as mon:
            ...                                # the run
        record["resources"] = mon.summary()

    base_url=None (template baseline, fake model): only Python and the system are sampled.
    """

    def __init__(self, base_url: Optional[str] = None, interval_s: float = 1.0, api_every: int = 3):
        self.base_url = base_url
        self.interval_s = interval_s
        self.api_every = max(1, api_every)
        self.samples = 0
        self.python_peak = 0.0
        self.ollama_rss_peak: Optional[float] = None
        self.ollama_processes = 0
        self.ollama_reported_peak: Optional[float] = None
        self.models_seen: dict[str, dict] = {}
        self.system_baseline: Optional[float] = None
        self.system_peak: Optional[float] = None
        self.ollama_rss_baseline: Optional[float] = None
        self.started = self.ended = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def __enter__(self) -> "MemoryMonitor":
        self.started = time.time()
        self.system_baseline = _system_used_mb()
        self.ollama_rss_baseline = _ollama_rss_mb()[0] if self.base_url else None
        self.sample(with_api=True)
        self._thread = threading.Thread(target=self._loop, name="memory-monitor", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.sample(with_api=True)
        self.ended = time.time()

    def _loop(self) -> None:
        i = 0
        while not self._stop.wait(self.interval_s):
            i += 1
            try:
                self.sample(with_api=(i % self.api_every == 0))
            except Exception:  # noqa: BLE001 - measuring must never break a run
                pass

    @staticmethod
    def _max(a: Optional[float], b: Optional[float]) -> Optional[float]:
        if a is None:
            return b
        return a if b is None else max(a, b)

    def sample(self, with_api: bool = True) -> None:
        py = _python_rss_mb()
        sysused = _system_used_mb()
        rss, n = _ollama_rss_mb() if self.base_url else (None, 0)
        loaded = ollama_loaded(self.base_url) if (self.base_url and with_api) else None
        with self._lock:
            self.samples += 1
            self.python_peak = max(self.python_peak, py)
            self.system_peak = self._max(self.system_peak, sysused)
            if rss is not None:
                self.ollama_rss_peak = self._max(self.ollama_rss_peak, rss)
                self.ollama_processes = max(self.ollama_processes, n)
            if loaded is not None:
                total = sum(m["size_mb"] for m in loaded)
                self.ollama_reported_peak = self._max(self.ollama_reported_peak, total)
                for m in loaded:
                    prev = self.models_seen.get(m["name"])
                    if prev is None or m["size_mb"] > prev["size_mb"]:
                        self.models_seen[m["name"]] = m

    def summary(self) -> dict:
        limit, basis = memory_limit_mb()
        model_peak = self._max(self.ollama_rss_peak, self.ollama_reported_peak)
        out = {
            "method": ("sampled every %.1f s: RSS of Ollama processes, /api/ps, Python RSS, system memory in use"
                       % self.interval_s) + ("" if psutil is not None else " (psutil missing: no system memory)"),
            "samples": self.samples,
            "duration_s": round((self.ended or time.time()) - self.started, 1),
            "python_peak_mb": round(self.python_peak, 1),
            "ollama_rss_baseline_mb": self.ollama_rss_baseline,
            "ollama_rss_peak_mb": self.ollama_rss_peak,
            "ollama_processes": self.ollama_processes,
            "ollama_reported_peak_mb": self.ollama_reported_peak,
            "models_loaded": list(self.models_seen.values()),
            "system_used_baseline_mb": self.system_baseline,
            "system_used_peak_mb": self.system_peak,
            "system_used_increase_mb": (round(self.system_peak - self.system_baseline, 1)
                                        if self.system_peak is not None and self.system_baseline is not None else None),
            "model_peak_mb": model_peak,
            "limit_mb": limit,
            "limit_basis": basis,
            "within_limit": None if model_peak is None else model_peak <= limit,
        }
        others = [m["name"] for m in self.models_seen.values()]
        if len(others) > 1:
            out["warning"] = ("more than one model was loaded during the run (" + ", ".join(others) +
                              "): the Ollama memory includes all of them; start runs cold (--cold)")
        return out
