"""Prompt tokens per E1R section against the context window (decision R4). No model call.

    python scripts/count_e1r_tokens.py --workspace workspace --case euterpepark-oss

Counts with the tokenizer stored in the Ollama model file (GGUF header only: the weights are not
read), in Ollama's chat format. The same counter is run on the E1 prompts, and compared with the
prompt tokens Ollama itself recorded in the latest E1 run, so its accuracy is visible.
Prints section names and numbers only, no research data.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.services import Workspace  # noqa: E402
from drafting.pipelines import PipelineConfig, run_pipeline  # noqa: E402
from ingestion.config import load_config  # noqa: E402
from ingestion.models import THEMES  # noqa: E402
from llm.base import Capabilities, GenerationResult, ModelInfo  # noqa: E402
from llm.providers import MAX_CHARS_PER_TOKEN  # noqa: E402

# qwen2 pre-tokenizer; [^\W\d_] stands for \p{L}, \d for \p{N}
SPLIT = re.compile(r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|(?:(?![\r\n])[\W_])?[^\W\d_]+|\d|"
                   r" ?(?:(?!\s)[\W_])+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+")


def gguf_path(model: str) -> Path:
    name, _, tag = model.partition(":")
    home = Path.home() / ".ollama" / "models"
    manifest = json.loads((home / "manifests/registry.ollama.ai/library" / name / (tag or "latest")).read_text())
    digest = next(l["digest"] for l in manifest["layers"] if l["mediaType"].endswith("image.model"))
    return home / "blobs" / digest.replace(":", "-")


def read_merges(path: Path) -> dict[tuple[str, str], int]:
    """The BPE merges from the GGUF metadata (byte-level: the merges alone decide the token count)."""
    fixed = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}
    with path.open("rb") as f:
        def num(fmt):
            return struct.unpack("<" + fmt, f.read(struct.calcsize("<" + fmt)))[0]

        def string():
            return f.read(num("Q")).decode("utf-8", "replace")

        def value(t):
            if t == 8:
                return string()
            if t == 9:
                et, n = num("I"), num("Q")
                return [value(et) for _ in range(n)]
            return num(fixed[t])

        if f.read(4) != b"GGUF":
            raise ValueError("not a GGUF file")
        num("I"), num("Q")
        meta = {}
        for _ in range(num("Q")):
            key = string()
            meta[key] = value(num("I"))
            if "tokenizer.ggml.merges" in meta:
                break
    return {tuple(m.split(" ", 1)): i for i, m in enumerate(meta["tokenizer.ggml.merges"])}


def byte_alphabet() -> dict[int, str]:
    keep = [*range(33, 127), *range(161, 173), *range(174, 256)]
    table, n = {b: chr(b) for b in keep}, 0
    for b in range(256):
        if b not in table:
            table[b] = chr(256 + n)
            n += 1
    return table


class Counter:
    def __init__(self, model: str):
        self.merges = read_merges(gguf_path(model))
        self.bytes = byte_alphabet()

    @lru_cache(maxsize=None)                                # noqa: B019 - one short-lived counter
    def _word(self, word: str) -> int:
        parts = [self.bytes[b] for b in word.encode("utf-8")]
        while len(parts) > 1:
            rank, at = min(((self.merges.get((a, b), 1 << 30), i) for i, (a, b) in enumerate(zip(parts, parts[1:]))))
            if rank == 1 << 30:
                break
            parts[at:at + 2] = [parts[at] + parts[at + 1]]
        return len(parts)

    def text(self, text: str) -> int:
        return sum(self._word(w) for w in SPLIT.findall(text))

    def chat(self, messages) -> int:
        """ChatML: <|im_start|>role\\n ... <|im_end|>\\n per message, plus the opened assistant turn."""
        n = sum(1 + self.text(f"{m.role}\n{m.content}") + 1 + 1 for m in messages)
        return n + 1 + self.text("assistant\n")


class Capture:
    """Stands in for the model: keeps the request and answers nothing."""

    def __init__(self):
        self.model_info = ModelInfo("count", "count", "count", "local", Capabilities(structured_output=True))
        self.request = None

    def generate(self, request):
        self.request = request
        return GenerationResult(text='{"claims": []}', parsed={"claims": []})


def recorded_e1_tokens(ws, case: str) -> tuple[str, dict[str, int]]:
    for p in sorted((ws.p("reports", case)).glob("*-naive-*/run.json"), reverse=True):
        run = json.loads(p.read_text(encoding="utf-8"))
        toks = {s: v["tokens_in"] for s, v in run["sections"].items() if v.get("tokens_in") and v.get("model_calls") == 1}
        if set(THEMES) <= set(toks):
            return run["run_id"], toks
    return "", {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default="workspace")
    ap.add_argument("--case", default="euterpepark-oss")
    ap.add_argument("--model", default="local_qwen7b")
    ap.add_argument("--prompt-version", default="v4")
    args = ap.parse_args()
    ws = Workspace(args.workspace)
    cfg_model = load_config("models.toml")["model"][args.model]
    window = cfg_model["num_ctx"]
    counter = Counter(cfg_model["model"])
    packs, store = ws.evidence(args.case), ws.store()
    run_id, recorded = recorded_e1_tokens(ws, args.case)
    print(f"model {cfg_model['model']}, window {window}, answer up to {PipelineConfig().max_output_tokens} tokens")
    print(f"E1 tokens recorded by Ollama: {run_id or 'no complete E1 run found'}\n")
    print(f"{'section':20s} {'records':>7s} {'E1 count':>8s} {'E1 rec.':>8s} {'E1R':>7s} {'% window':>8s} "
          f"{'+answer':>8s} {'fits':>5s} {'chars/tok':>9s}")
    for sec in THEMES:
        counts = {}
        for pipeline in ("naive", "naive_raw"):
            cap = Capture()
            cfg = PipelineConfig(pipeline_id=pipeline, prompt_version=args.prompt_version, case_label=ws.case_label(args.case))
            draft = run_pipeline(packs[sec], cfg, cap, None, store)
            chars = sum(len(m.content) for m in cap.request.messages)
            counts[pipeline] = (counter.chat(cap.request.messages), chars, len(draft.evidence_ids_available))
        e1r, chars, n = counts["naive_raw"]
        total = e1r + PipelineConfig().max_output_tokens
        fits = "no" if e1r > window else ("tight" if total > window else "yes")
        ratio = chars / e1r
        print(f"{sec:20s} {n:7d} {counts['naive'][0]:8d} {recorded.get(sec, 0):8d} {e1r:7d} {100 * e1r / window:7.0f}% "
              f"{total:8d} {fits:>5s} {ratio:9.2f}" + ("  above the cut-off check!" if ratio > MAX_CHARS_PER_TOKEN else ""))
    print("\nfits: yes = prompt and a full-length answer fit; tight = the prompt fits, a full-length answer may not; "
          "no = Ollama drops part of the prompt (the section fails, R4).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
