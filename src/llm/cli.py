"""Model setup helper (UC5).

    python -m llm.cli check              # which configured models are pulled / fit the 16 GB target
    python -m llm.cli smoke local_qwen7b # one tiny Dutch JSON request: does it work, how fast?
"""
from __future__ import annotations

import argparse
import sys

from .base import GenerationRequest, Message
from .providers import ProviderError, ollama_pulled_models
from .registry import get_provider, list_models, validate_model_config


def _is_pulled(tag: str, pulled: list[str]) -> bool:
    return tag in pulled or f"{tag}:latest" in pulled or (":" not in tag and any(p.startswith(tag + ":") for p in pulled))


def check() -> int:
    models = list_models()
    base_urls = {cfg.get("base_url", "http://localhost:11434") for cfg in models.values() if cfg["provider"] == "ollama"}
    pulled: dict[str, list[str] | None] = {}
    for url in base_urls:
        try:
            pulled[url] = ollama_pulled_models(url)
            print(f"Ollama at {url}: running, {len(pulled[url])} model(s) downloaded: {', '.join(pulled[url]) or '-'}")
        except ProviderError as exc:
            pulled[url] = None
            print(f"!! {exc}")
    print()
    missing = []
    for mid, cfg in models.items():
        problems = validate_model_config(mid)
        line = f"{mid:22s} {cfg['provider']:18s} {cfg.get('model', ''):22s} {cfg.get('location', 'local'):7s}"
        if cfg["provider"] == "ollama":
            have = pulled.get(cfg.get("base_url", "http://localhost:11434"))
            if have is None:
                state = "? (Ollama not running)"
            elif _is_pulled(cfg["model"], have):
                state = "downloaded"
            else:
                state = "NOT downloaded"
                missing.append(cfg["model"])
            line += f" {state}"
        elif cfg["provider"] == "openai_compatible":
            line += f" needs env {cfg.get('api_key_env')}"
        print(line + (f"   WARNING: {'; '.join(problems)}" if problems else ""))
    if missing:
        print("\nTo download the missing local models, run in Terminal:")
        for tag in missing:
            print(f"  ollama pull {tag}")
    return 0


def smoke(model_id: str) -> int:
    problems = validate_model_config(model_id)
    for p in problems:
        print(f"WARNING: {p}")
    provider = get_provider(model_id)
    schema = {"type": "object", "properties": {"zin": {"type": "string"}, "thema": {"type": "string"}},
              "required": ["zin", "thema"]}
    req = GenerationRequest(
        task="smoke",
        messages=[Message("system", "Antwoord uitsluitend met JSON."),
                  Message("user", "Schrijf één korte Nederlandse zin over een speeltuin in een buurt, "
                                  "en noem het thema (Formeel spelen). JSON: {\"zin\": \"...\", \"thema\": \"...\"}")],
        max_output_tokens=200, json_schema=schema)
    print(f"Calling {model_id} ({provider.model_info.model}) … the first call also loads the model into memory.")
    res = provider.generate(req)
    if res.error:
        print(f"FAILED: {res.error}")
        return 1
    tps = (res.output_tokens / res.latency_s) if res.output_tokens and res.latency_s else None
    print(f"OK in {res.latency_s:.1f}s, {res.input_tokens} tokens in / {res.output_tokens} out"
          + (f" (~{tps:.1f} tokens/s incl. load)" if tps else ""))
    print(f"parsed JSON: {res.parsed}")
    if not isinstance(res.parsed, dict) or "zin" not in res.parsed:
        print("WARNING: the model did not return the requested JSON shape.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="llm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    s = sub.add_parser("smoke")
    s.add_argument("model_id")
    args = ap.parse_args(argv)
    if args.cmd == "check":
        return check()
    return smoke(args.model_id)


if __name__ == "__main__":
    sys.exit(main())
