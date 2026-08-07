"""Thin wrapper around Ollama's chat endpoint with fallback-model support."""
from __future__ import annotations

import logging
import requests

from src.config import get_config

log = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, cfg=None):
        self.cfg = cfg or get_config().llm

    def _chat(self, model: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self.cfg.temperature},
        }
        if tools:
            payload["tools"] = tools

        resp = requests.post(
            f"{self.cfg.host}/api/chat",
            json=payload,
            timeout=self.cfg.request_timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Try the primary model; fall back on failure or timeout."""
        try:
            return self._chat(self.cfg.model, messages, tools)
        except requests.RequestException as e:
            if not self.cfg.fallback_model:
                raise
            log.warning(
                "Primary model %s failed (%s); falling back to %s",
                self.cfg.model, e, self.cfg.fallback_model,
            )
            return self._chat(self.cfg.fallback_model, messages, tools)
