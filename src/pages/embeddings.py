"""Embedding client for the OpenAI-compatible /embeddings endpoint.

Targets the same LLM_BASE_URL as the orchestrator. Default model:
qwen3-embedding-4b (1024 dims). See §4.8 of docs/spec.md.

Adapted from /Users/marko/dev_p/gmail/src/search/embeddings.py.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "qwen3-embedding-4b")
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "1024"))
BATCH_SIZE = 32


def _base_url() -> str:
    return os.environ["LLM_BASE_URL"]


def _api_key() -> str:
    return os.environ.get("LLM_API_KEY", "")


async def embed_texts(
    texts: list[str],
    *,
    client: httpx.AsyncClient | None = None,
) -> list[list[float]]:
    if not texts:
        return []
    all_embeddings: list[list[float]] = [[] for _ in texts]

    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=120)
    try:
        for batch_start in range(0, len(texts), BATCH_SIZE):
            batch = texts[batch_start : batch_start + BATCH_SIZE]
            resp = await client.post(
                f"{_base_url().rstrip('/')}/embeddings",
                headers={
                    "Authorization": f"Bearer {_api_key()}",
                    "Content-Type": "application/json",
                },
                json={
                    "input": batch,
                    "model": EMBEDDING_MODEL,
                    "dimensions": EMBEDDING_DIMENSIONS,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            for item in data["data"]:
                idx = batch_start + item["index"]
                all_embeddings[idx] = item["embedding"]
    finally:
        if own_client:
            await client.aclose()
    return all_embeddings


async def embed_query(text: str, *, client: httpx.AsyncClient | None = None) -> list[float]:
    results = await embed_texts([text], client=client)
    return results[0] if results else []
