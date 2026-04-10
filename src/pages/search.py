"""Hybrid search over page sections.

Mirrors the pattern from /Users/marko/dev_p/gmail/src/search/search.py:
BM25 + dense + RRF fusion in a single Qdrant query, with smart score
thresholding and snippet extraction.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from qdrant_client import QdrantClient, models

try:
    from .bm25 import BM25Encoder
    from .embeddings import embed_query
    from .index import COLLECTION_NAME
except ImportError:  # pragma: no cover
    from bm25 import BM25Encoder  # type: ignore
    from embeddings import embed_query  # type: ignore
    from index import COLLECTION_NAME  # type: ignore

logger = logging.getLogger(__name__)

MIN_RESULTS = 6
RELATIVE_SCORE_RATIO = 0.35
GAP_DROP_RATIO = 0.60


@dataclass
class SearchHit:
    page_id: str
    page_title: str
    section_id: str
    heading: str
    snippet: str
    score: float
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "page_id": self.page_id,
            "page_title": self.page_title,
            "section_id": self.section_id,
            "heading": self.heading,
            "snippet": self.snippet,
            "score": self.score,
            "tags": self.tags,
        }


# ── thresholding & snippets ──────────────────────────────────────────


def _apply_score_threshold(points: list) -> list:
    if len(points) <= MIN_RESULTS:
        return points
    top_score = points[0].score or 0
    if top_score <= 0:
        return points[:MIN_RESULTS]
    score_floor = top_score * RELATIVE_SCORE_RATIO
    cutoff = len(points)
    for i in range(MIN_RESULTS, len(points)):
        s = points[i].score or 0
        prev = points[i - 1].score or 0
        if s < score_floor:
            cutoff = i
            break
        if prev > 0 and s < prev * (1 - GAP_DROP_RATIO):
            cutoff = i
            break
    return points[:cutoff]


def _extract_snippet(text: str, query: str, max_length: int = 260) -> str:
    query_terms = set(re.findall(r"\b\w{2,}\b", query.lower()))
    if not query_terms or not text:
        return text[:max_length]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    scored = []
    for sent in sentences:
        words = set(re.findall(r"\b\w{2,}\b", sent.lower()))
        overlap = len(query_terms & words)
        scored.append((overlap, sent))
    scored.sort(key=lambda x: x[0], reverse=True)

    parts: list[str] = []
    total = 0
    for overlap, sent in scored:
        if overlap == 0 and parts:
            break
        if total + len(sent) > max_length:
            if not parts:
                parts.append(sent[:max_length])
            break
        parts.append(sent)
        total += len(sent)

    snippet = " … ".join(parts) if parts else text[:max_length]
    for term in query_terms:
        snippet = re.sub(
            rf"\b({re.escape(term)})\b",
            r"**\1**",
            snippet,
            count=5,
            flags=re.IGNORECASE,
        )
    return snippet[: max_length + 100]


# ── filter helpers ───────────────────────────────────────────────────


def _build_filter(
    page_id: str | None,
    tags: list[str] | None,
) -> models.Filter | None:
    conditions: list[models.FieldCondition] = []
    if page_id:
        conditions.append(
            models.FieldCondition(key="page_id", match=models.MatchValue(value=page_id))
        )
    if tags:
        conditions.append(
            models.FieldCondition(key="tags", match=models.MatchAny(any=tags))
        )
    if not conditions:
        return None
    return models.Filter(must=conditions)


# ── search backends ──────────────────────────────────────────────────


async def _search_hybrid(
    client: QdrantClient,
    bm25: BM25Encoder,
    query: str,
    *,
    limit: int,
    query_filter: models.Filter | None,
    collection: str,
) -> list[models.ScoredPoint]:
    query_vec = await embed_query(query)
    sparse_indices, sparse_values = bm25.encode_query(query)

    prefetch: list[models.Prefetch] = []
    if sparse_indices:
        prefetch.append(
            models.Prefetch(
                query=models.SparseVector(indices=sparse_indices, values=sparse_values),
                using="bm25",
                limit=limit,
                filter=query_filter,
            )
        )
    if query_vec:
        prefetch.append(
            models.Prefetch(
                query=query_vec,
                using="dense",
                limit=limit,
                filter=query_filter,
            )
        )
    if not prefetch:
        return []

    return client.query_points(
        collection_name=collection,
        prefetch=prefetch,
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=limit,
    ).points


# ── entry point ──────────────────────────────────────────────────────


async def search(
    *,
    client: QdrantClient,
    bm25: BM25Encoder,
    query: str,
    limit: int = 8,
    page_id: str | None = None,
    tags: list[str] | None = None,
    collection: str = COLLECTION_NAME,
) -> dict:
    if not query.strip():
        return {"query": query, "results": [], "total": 0, "took_ms": 0}

    start = time.time()
    query_filter = _build_filter(page_id, tags)
    fetch_limit = max(limit + 12, 32)
    points = await _search_hybrid(
        client,
        bm25,
        query,
        limit=fetch_limit,
        query_filter=query_filter,
        collection=collection,
    )
    points = _apply_score_threshold(points)
    points = points[:limit]

    hits: list[SearchHit] = []
    for p in points:
        payload = p.payload or {}
        text = payload.get("text", "")
        hits.append(
            SearchHit(
                page_id=payload.get("page_id", ""),
                page_title=payload.get("page_title", ""),
                section_id=payload.get("section_id", ""),
                heading=payload.get("heading", ""),
                snippet=_extract_snippet(text, query),
                score=round(p.score or 0, 4),
                tags=payload.get("tags") or [],
            )
        )

    return {
        "query": query,
        "results": [h.to_dict() for h in hits],
        "total": len(hits),
        "took_ms": round((time.time() - start) * 1000),
    }
