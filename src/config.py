"""
Loads config.yaml and resolves ${ENV_VAR} placeholders against the process
environment (populated from .env via python-dotenv).

Usage:
    from src.config import get_config
    cfg = get_config()
    cfg.servicenow.instance_url
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from functools import lru_cache

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _resolve_env_vars(value):
    if isinstance(value, str):
        def _sub(match):
            var_name = match.group(1)
            resolved = os.environ.get(var_name, "")
            if resolved == "":
                # Leave a loud placeholder rather than silently using an
                # empty string, so misconfiguration is obvious at runtime.
                return f"__MISSING_ENV_{var_name}__"
            return resolved
        return _ENV_VAR_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


class LLMConfig(BaseModel):
    provider: str = "ollama"
    host: str
    model: str
    fallback_model: str | None = None
    context_window: int = 32768
    temperature: float = 0.2
    request_timeout_seconds: int = 300


class AppConfig(BaseModel):
    """Loose top-level wrapper — individual connectors read their own
    sub-section as a plain dict via cfg.raw["section_name"] so this file
    doesn't need to be updated every time a new integration field is added.
    """
    llm: LLMConfig
    raw: dict = Field(default_factory=dict)

    class Config:
        extra = "allow"


@lru_cache(maxsize=1)
def get_config(config_path: str | None = None) -> AppConfig:
    repo_root = Path(__file__).resolve().parent.parent
    load_dotenv(repo_root / ".env")

    path = Path(config_path) if config_path else repo_root / "config.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config.yaml.example to config.yaml "
            "and fill in your values."
        )

    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    resolved = _resolve_env_vars(raw)

    return AppConfig(llm=LLMConfig(**resolved["llm"]), raw=resolved)


def reload_config(config_path: str | None = None) -> AppConfig:
    """Clears the cached config and re-reads config.yaml from disk. Use this
    (via POST /admin/reload-config) after editing config.yaml to enable a
    new integration — no container restart needed."""
    get_config.cache_clear()
    return get_config(config_path)
