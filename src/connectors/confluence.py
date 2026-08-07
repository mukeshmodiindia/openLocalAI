"""Confluence connector using atlassian-python-api. Pulls page content for
indexing into the RAG vector store (see src/rag/vector_store.py)."""
from __future__ import annotations

from atlassian import Confluence

from src.config import get_config
from src.connectors.errors import IntegrationNotConfigured


class ConfluenceConnector:
    def __init__(self):
        conf = get_config().raw.get("confluence", {})
        self.enabled = conf.get("enabled", False)
        if not self.enabled:
            raise IntegrationNotConfigured("Confluence")

        self.spaces = conf.get("spaces", [])

        auth = conf["auth"]
        kwargs = {"url": conf["base_url"]}
        if auth["type"] == "api_token":
            kwargs.update(username=auth["username"], password=auth["api_token"], cloud=True)
        elif auth["type"] == "pat":
            kwargs.update(token=auth["api_token"])
        elif auth["type"] == "basic":
            kwargs.update(username=auth["username"], password=auth["api_token"])
        else:
            raise ValueError(f"Unsupported Confluence auth type: {auth['type']}")

        self.client = Confluence(**kwargs)

    def iter_space_pages(self, space_key: str, batch_size: int = 50):
        """Yields (page_id, title, body_html) for every page in a space —
        used by the periodic reindex job."""
        start = 0
        while True:
            pages = self.client.get_all_pages_from_space(
                space_key, start=start, limit=batch_size,
                expand="body.storage",
            )
            if not pages:
                break
            for page in pages:
                yield (
                    page["id"],
                    page["title"],
                    page.get("body", {}).get("storage", {}).get("value", ""),
                )
            start += batch_size

    def iter_all_configured_pages(self):
        for space in self.spaces:
            yield from self.iter_space_pages(space)

    def search(self, cql: str, limit: int = 10) -> list[dict]:
        return self.client.cql(cql, limit=limit).get("results", [])
