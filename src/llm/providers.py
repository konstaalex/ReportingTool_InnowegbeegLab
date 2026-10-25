"""Concrete providers. Stdlib HTTP only (urllib) - no vendor SDKs (NFR12).

- OllamaProvider          local models via Ollama's /api/chat (JSON-schema `format`)
- OpenAICompatibleProvider any /v1/chat/completions endpoint: hosted APIs
                          (Azure OpenAI, Mistral, ...), LM Studio, llama.cpp server
- FakeProvider            deterministic, offline: builds a valid response from the
                          evidence ids in the prompt. For tests and UI demos only;
                          its output is labelled as fake everywhere.
- ReplayProvider          replays raw outputs recorded in an earlier run record,
                          so a run can be re-verified/re-scored without a model.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from .base import GenerationRequest, GenerationResult, ModelInfo, ProviderError


def extract_json(text: str) -> Optional[Any]:
    """Tolerant JSON extraction: strips code fences / leading prose, then
    parses the first {...} or [...] block. Returns None if nothing parses."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = t.find(open_c)
        end = t.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(t[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise ProviderError(f"HTTP {exc.code} from {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"cannot reach {url}: {exc.reason}") from exc


class _Base:
    def __init__(self, info: ModelInfo, max_retries: int = 1, timeout_s: float = 600.0):
        self._info = info
        self.max_retries = max_retries
        self.timeout_s = timeout_s

    @property
    def model_info(self) -> ModelInfo:
        return self._info

    def _cost(self, tin: Optional[int], tout: Optional[int]) -> float:
        return ((tin or 0) / 1000 * self._info.eur_per_1k_input) + ((tout or 0) / 1000 * self._info.eur_per_1k_output)

    def _with_retries(self, request: GenerationRequest, call: Callable[[], GenerationResult]) -> GenerationResult:
        last_err = None
        for attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                res = call()
            except ProviderError as exc:
                last_err = str(exc)
                continue
            res.latency_s = round(time.perf_counter() - t0, 3)
            res.retries = attempt
            if request.json_schema is not None:
                res.parsed = extract_json(res.text)
                if res.parsed is None and attempt < self.max_retries:
                    last_err = "output was not valid JSON"
                    continue
                if res.parsed is None:
                    res.error = "output was not valid JSON"
            return res
        return GenerationResult(text="", provider=self._info.provider, model=self._info.model,
                                retries=self.max_retries, error=last_err)


class OllamaProvider(_Base):
    def __init__(self, info: ModelInfo, base_url: str = "http://localhost:11434", num_ctx: Optional[int] = None,
                 think: Optional[bool] = None, **kw):
        super().__init__(info, **kw)
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx or info.capabilities.context_tokens
        self.think = think   # None = model default; False switches off "thinking" models (qwen3, deepseek-r1)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        def call() -> GenerationResult:
            messages = [{"role": m.role, "content": m.content} for m in request.messages]
            if request.images:
                messages[-1]["images"] = [base64.b64encode(b).decode("ascii") for b in request.images]
            payload = {
                "model": self._info.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": request.temperature, "num_predict": request.max_output_tokens,
                            "num_ctx": self.num_ctx, **({"seed": request.seed} if request.seed is not None else {})},
            }
            if self.think is not None:
                payload["think"] = self.think
            if request.json_schema is not None:
                payload["format"] = request.json_schema if self._info.capabilities.structured_output else "json"
            data = _post_json(f"{self.base_url}/api/chat", payload, {}, self.timeout_s)
            tin, tout = data.get("prompt_eval_count"), data.get("eval_count")
            return GenerationResult(text=data.get("message", {}).get("content", ""), input_tokens=tin,
                                    output_tokens=tout, provider="ollama", model=self._info.model,
                                    cost_eur=self._cost(tin, tout))
        return self._with_retries(request, call)

    def available_models(self) -> list[str]:
        return ollama_pulled_models(self.base_url)


def ollama_pulled_models(base_url: str = "http://localhost:11434") -> list[str]:
    """Names of models already downloaded in the local Ollama (raises ProviderError if Ollama is not running)."""
    req = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return [m["name"] for m in json.loads(resp.read().decode()).get("models", [])]
    except urllib.error.URLError as exc:
        raise ProviderError(f"Ollama is not reachable at {base_url} ({exc.reason}). Is the Ollama app running?") from exc


class OpenAICompatibleProvider(_Base):
    def __init__(self, info: ModelInfo, base_url: str, api_key_env: Optional[str] = None,
                 extra_headers: Optional[dict] = None, **kw):
        super().__init__(info, **kw)
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.extra_headers = extra_headers or {}

    def generate(self, request: GenerationRequest) -> GenerationResult:
        def call() -> GenerationResult:
            headers = dict(self.extra_headers)
            if self.api_key_env:
                key = os.environ.get(self.api_key_env)
                if not key:
                    raise ProviderError(f"environment variable {self.api_key_env} is not set")
                headers["Authorization"] = f"Bearer {key}"
            messages: list[dict] = [{"role": m.role, "content": m.content} for m in request.messages]
            if request.images:
                parts: list[dict] = [{"type": "text", "text": messages[-1]["content"]}]
                for b in request.images:
                    mime = "image/png" if b[:4] == b"\x89PNG" else "image/jpeg"
                    parts.append({"type": "image_url",
                                  "image_url": {"url": f"data:{mime};base64,{base64.b64encode(b).decode('ascii')}"}})
                messages[-1]["content"] = parts
            payload: dict = {
                "model": self._info.model,
                "messages": messages,
                "temperature": request.temperature,
                "max_tokens": request.max_output_tokens,
            }
            if request.seed is not None:
                payload["seed"] = request.seed
            if request.json_schema is not None:
                if self._info.capabilities.structured_output:
                    payload["response_format"] = {"type": "json_schema",
                                                  "json_schema": {"name": request.task.replace(" ", "_"),
                                                                  "schema": request.json_schema}}
                else:
                    payload["response_format"] = {"type": "json_object"}
            data = _post_json(f"{self.base_url}/chat/completions", payload, headers, self.timeout_s)
            usage = data.get("usage") or {}
            tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
            return GenerationResult(text=text, input_tokens=tin, output_tokens=tout, provider="openai_compatible",
                                    model=self._info.model, cost_eur=self._cost(tin, tout))
        return self._with_retries(request, call)


_ID_RE = re.compile(r"\[([XBDNC]-[A-Z]{2}-\d{2})\]\s*\([^)]*\)\s*(.+)")


class FakeProvider(_Base):
    """Deterministic stand-in. Reads the evidence lines in the user prompt and
    returns claims that copy evidence statements verbatim, plus one
    deliberately uncited interpretation so the review UI always has
    something to flag. Never use its output as research data."""

    def generate(self, request: GenerationRequest) -> GenerationResult:
        prompt = request.messages[-1].content
        if request.images:
            if request.task == "read_infographic":
                payload = {"values": [{"chart": "[FAKE] voorbeeld", "area": "Nederland", "label": "testwaarde",
                                       "value": 50, "unit": "%", "as_printed": "50%"}]}
            else:
                payload = {"observations": [{"text": "[FAKE] voorbeeldbeschrijving van de kaart", "area": "",
                                             "legend_item": ""}]}
            text = json.dumps(payload, ensure_ascii=False)
            return GenerationResult(text=text, parsed=payload, provider="fake", model="fake-vision")
        if request.task.startswith("judge_"):
            payload = {"label": "partial", "zinnen": [1], "conflict": False, "reden": "[FAKE] testoordeel"}
            text = json.dumps(payload, ensure_ascii=False)
            return GenerationResult(text=text, parsed=payload, provider="fake", model="fake")
        lines = [m for m in (_ID_RE.search(l) for l in prompt.splitlines()) if m]
        claims = []
        for m in lines[:4]:
            eid, stmt = m.group(1), m.group(2).strip()
            claims.append({"text": stmt.split(". Let op")[0].rstrip(".") + ".", "type": "bevinding",
                           "evidence_ids": [eid]})
        if lines:
            claims.append({"text": "[FAKE] Dit is een niet-onderbouwde interpretatie om de review te testen.",
                           "type": "interpretatie", "evidence_ids": []})
        payload = {"claims": claims}
        if "findings" in request.task:
            payload = {"findings": claims}
        text = json.dumps(payload, ensure_ascii=False)
        return GenerationResult(text=text, parsed=payload, provider="fake", model="fake",
                                input_tokens=len(prompt) // 4, output_tokens=len(text) // 4)


class ReplayProvider(_Base):
    """Returns recorded raw outputs in order (from a run record's StageLogs)."""

    def __init__(self, info: ModelInfo, outputs: list[str], **kw):
        super().__init__(info, **kw)
        self._outputs = list(outputs)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not self._outputs:
            return GenerationResult(text="", provider="replay", model=self._info.model, error="replay exhausted")
        text = self._outputs.pop(0)
        return GenerationResult(text=text, parsed=extract_json(text), provider="replay", model=self._info.model)
