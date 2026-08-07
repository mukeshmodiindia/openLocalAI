"""Qdrant wrapper for indexing/retrieving Confluence pages and cached vendor
documentation. Embeddings are generated locally via Ollama's embedding
endpoint (nomic-embed-text by default) — no external embedding API."""
from __future__ import annotations

import uuid
import requests
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from src.config import get_config


class VectorStore:
    def __init__(self):
        vs = get_config().raw["vector_store"]
        llm = get_config().raw["llm"]
        self.collection = vs["collection"]
        self.embedding_model = vs["embedding_model"]
        self.ollama_host = llm["host"]
        self.client = QdrantClient(url=vs["host"])
        self._ensure_collection()

    def _ensure_collection(self, vector_size: int = 768):
        existing = [c.name for c in self.client.get_collections().collections]
        if self.collection not in existing:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )

    def _embed(self, text: str) -> list[float]:
        resp = requests.post(
            f"{self.ollama_host}/api/embeddings",
            json={"model": self.embedding_model, "prompt": text},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    def upsert_document(self, doc_id: str, text: str, metadata: dict):
        vector = self._embed(text)
        self.client.upsert(
            collection_name=self.collection,
            points=[PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, doc_id)),
                vector=vector,
                payload={"text": text, **metadata},
            )],
        )

    def search(self, query: str, limit: int = 5, source_filter: str | None = None) -> list[dict]:
        vector = self._embed(query)
        query_filter = None
        if source_filter:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            query_filter = Filter(
                must=[FieldCondition(key="source", match=MatchValue(value=source_filter))]
            )
        hits = self.client.search(
            collection_name=self.collection, query_vector=vector,
            limit=limit, query_filter=query_filter,
        )
        return [{"text": h.payload.get("text"), "score": h.score, **h.payload} for h in hits]
