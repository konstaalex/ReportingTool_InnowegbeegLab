"""Provider-agnostic model interface (brief section 11, FR13, NFR8).

Pipelines import ONLY this module. Vendor specifics live in providers.py.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, Protocol


@dataclass
class Capabilities:
    structured_output: bool = False      # honours a JSON schema
    json_mode: bool = True               # can at least be asked for JSON
    context_tokens: int = 8192
    reliable_token_usage: bool = False
    streaming: bool = False
    vision: bool = False


@dataclass
class ModelInfo:
    model_id: str                        # our id from config/models.toml
    provider: str                        # ollama | openai_compatible | fake | replay
    model: str                           # provider's model name
    location: str                        # local | hosted
    capabilities: Capabilities = field(default_factory=Capabilities)
    ram_gb_estimate: Optional[float] = None
    eur_per_1k_input: float = 0.0
    eur_per_1k_output: float = 0.0
    data_residency: Optional[str] = None  # e.g. "EU" for hosted

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Message:
    role: str                            # system | user | assistant
    content: str


@dataclass
class GenerationRequest:
    task: str                            # pipeline stage name, for logs
    messages: list[Message]
    temperature: float = 0.2
    max_output_tokens: int = 1500
    json_schema: Optional[dict] = None
    seed: Optional[int] = 42
    contains_resident_free_text: bool = False   # set by the pipeline from the evidence it sends
    images: list[bytes] = field(default_factory=list)   # PNG/JPEG bytes attached to the last user message

    def prompt_sha(self) -> str:
        raw = json.dumps([asdict(m) for m in self.messages], ensure_ascii=False, sort_keys=True)
        h = hashlib.sha256(raw.encode("utf-8"))
        for img in self.images:
            h.update(hashlib.sha256(img).digest())
        return h.hexdigest()[:12]


@dataclass
class GenerationResult:
    text: str
    parsed: Optional[Any] = None
    latency_s: float = 0.0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    provider: str = ""
    model: str = ""
    retries: int = 0
    cost_eur: float = 0.0
    error: Optional[str] = None
    timings: Optional[dict] = None       # provider-reported seconds, e.g. Ollama: load_s, prompt_eval_s, eval_s


class LLMProvider(Protocol):
    @property
    def model_info(self) -> ModelInfo: ...

    def generate(self, request: GenerationRequest) -> GenerationResult: ...


class ProviderError(RuntimeError):
    pass


class PrivacyViolation(RuntimeError):
    """Raised before any network call when a request would send resident
    free text to a hosted provider without an explicit override (FR15)."""
