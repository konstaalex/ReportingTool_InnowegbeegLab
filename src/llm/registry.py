"""Builds providers from config/models.toml and validates them (UC5, FR13, FR14).

    provider = get_provider("local_qwen7b")
    problems = validate_model_config("local_gemma12b", target_ram_gb=16)
"""
from __future__ import annotations

from typing import Optional

from ingestion.config import load_config

from .base import Capabilities, ModelInfo
from .providers import FakeProvider, OllamaProvider, OpenAICompatibleProvider


def _info(model_id: str, cfg: dict) -> ModelInfo:
    caps = Capabilities(**cfg.get("capabilities", {}))
    return ModelInfo(
        model_id=model_id, provider=cfg["provider"], model=cfg.get("model", model_id),
        location=cfg.get("location", "local"), capabilities=caps,
        ram_gb_estimate=cfg.get("ram_gb_estimate"),
        eur_per_1k_input=cfg.get("eur_per_1k_input", 0.0), eur_per_1k_output=cfg.get("eur_per_1k_output", 0.0),
        data_residency=cfg.get("data_residency"),
    )


def list_models() -> dict[str, dict]:
    return dict(load_config("models.toml").get("model", {}))


def selected_models(kind: str = "drafting") -> list[str]:
    """The models compared in SQ3 ([selection] in models.toml), in order; only ids that exist."""
    cfg = load_config("models.toml")
    return [m for m in cfg.get("selection", {}).get(kind, []) if m in cfg.get("model", {})]


def validate_model_config(model_id: str, target_ram_gb: Optional[float] = None) -> list[str]:
    """Problems that should stop a run BEFORE it starts (FR14, NFR5)."""
    cfg_all = load_config("models.toml")
    models = cfg_all.get("model", {})
    if model_id not in models:
        return [f"unknown model id {model_id!r}; known: {', '.join(models)}"]
    cfg = models[model_id]
    problems = []
    ram = target_ram_gb or cfg_all.get("hardware", {}).get("target_ram_gb", 16)
    headroom = cfg_all.get("hardware", {}).get("max_model_share_of_ram", 0.6)
    est = cfg.get("ram_gb_estimate")
    if cfg.get("location", "local") == "local" and est is not None and est > ram * headroom:
        problems.append(f"{model_id}: needs ~{est} GB, more than {headroom:.0%} of the {ram} GB target laptop")
    if cfg.get("location") == "hosted" and not cfg.get("data_residency"):
        problems.append(f"{model_id}: hosted provider without declared data_residency (NFR7)")
    if cfg["provider"] not in ("ollama", "openai_compatible", "fake"):
        problems.append(f"{model_id}: unsupported provider {cfg['provider']!r}")
    return problems


def get_provider(model_id: str):
    cfg = load_config("models.toml")["model"][model_id]
    info = _info(model_id, cfg)
    kw = {"max_retries": cfg.get("max_retries", 1), "timeout_s": cfg.get("timeout_s", 600.0)}
    if cfg["provider"] == "ollama":
        return OllamaProvider(info, base_url=cfg.get("base_url", "http://localhost:11434"),
                              num_ctx=cfg.get("num_ctx"), think=cfg.get("think"), **kw)
    if cfg["provider"] == "openai_compatible":
        return OpenAICompatibleProvider(info, base_url=cfg["base_url"], api_key_env=cfg.get("api_key_env"), **kw)
    if cfg["provider"] == "fake":
        return FakeProvider(info, **kw)
    raise ValueError(f"unsupported provider {cfg['provider']!r}")
