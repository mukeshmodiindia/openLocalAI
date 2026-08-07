"""Self-hosted SearxNG client, scoped to an allow-list of official vendor
documentation domains — used only for things like 'MongoDB current syntax
for creating a user with role X', not general open browsing."""
from __future__ import annotations

import requests

from src.config import get_config


class WebLookup:
    def __init__(self):
        wl = get_config().raw["web_lookup"]
        self.enabled = wl.get("enabled", False)
        self.searxng_url = wl["searxng_url"]
        self.allowed_domains = wl.get("allowed_domains", [])
        self.max_results = wl.get("max_results_per_query", 5)

    def search(self, query: str) -> list[dict]:
        if not self.enabled:
            return []
        site_filter = " OR ".join(f"site:{d}" for d in self.allowed_domains)
        scoped_query = f"{query} ({site_filter})" if site_filter else query

        resp = requests.get(
            f"{self.searxng_url}/search",
            params={"q": scoped_query, "format": "json"},
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])[: self.max_results]

        # Belt-and-suspenders: drop anything that slipped past the site
        # filter and isn't actually on an allow-listed domain.
        return [
            r for r in results
            if any(d in r.get("url", "") for d in self.allowed_domains)
        ]
