"""Qdrant indexer: parse → diff → embed → upsert.

Each section of a page becomes one Qdrant point with both `dense` (Qwen3
embedding) and `bm25` (sparse) vectors, so a single query can do hybrid
search with RRF fusion (see search.py).
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Protocol

from qdrant_client import QdrantClient, models

try:
    from .bm25 import BM25Encoder
    from .embeddings import EMBEDDING_DIMENSIONS, embed_texts
    from .parser import Section
    from .store import PageRecord, PageStore
except ImportError:  # pragma: no cover
    from bm25 import BM25Encoder  # type: ignore
    from embeddings import EMBEDDING_DIMENSIONS, embed_texts  # type: ignore
    from parser import Section  # type: ignore
    from store import PageRecord, PageStore  # type: ignore

logger = logging.getLogger(__name__)

COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION", "notes_sections")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")


class EmbedFn(Protocol):
    async def __call__(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class IndexedSection:
    page_id: str
    section_id: str
    heading: str
    text: str


def _point_id(page_id: str, section_id: str) -> str:
    """Deterministic UUID5 for a (page, section) tuple."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"notes://{page_id}/{section_id}"))


def _section_hash(section: Section) -> str:
    return hashlib.blake2s(section.text.encode("utf-8"), digest_size=8).hexdigest()


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)


class PageIndex:
    """Index-layer façade. Owns the Qdrant collection for section points."""

    def __init__(
        self,
        store: PageStore,
        *,
        client: QdrantClient | None = None,
        bm25: BM25Encoder | None = None,
        embed_fn: EmbedFn | None = None,
        collection: str = COLLECTION_NAME,
    ):
        self.store = store
        self.client = client
        self.bm25 = bm25 or BM25Encoder()
        self.bm25.load()
        self.collection = collection
        self._embed_fn = embed_fn or self._default_embed
        self._ensured = False

    async def _default_embed(self, texts: list[str]) -> list[list[float]]:
        return await embed_texts(texts)

    # ── collection setup ─────────────────────────────────────────────

    def ensure_collection(self) -> None:
        if self._ensured or self.client is None:
            return
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection not in existing:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config={
                    "dense": models.VectorParams(
                        size=EMBEDDING_DIMENSIONS,
                        distance=models.Distance.COSINE,
                    ),
                },
                sparse_vectors_config={
                    "bm25": models.SparseVectorParams(modifier=models.Modifier.IDF),
                },
            )
            for field in ("page_id", "tags"):
                try:
                    self.client.create_payload_index(
                        collection_name=self.collection,
                        field_name=field,
                        field_schema=models.PayloadSchemaType.KEYWORD,
                    )
                except Exception:  # pragma: no cover
                    pass
            logger.info("created qdrant collection %s", self.collection)
        self._ensured = True

    # ── indexing ─────────────────────────────────────────────────────

    async def index_page(self, record: PageRecord) -> int:
        """Upsert all sections of a page. Returns number of points upserted.

        Deletes any stale points for the page first (sections that no longer
        exist). Simple and correct; optimising the diff is a later concern.
        """
        if self.client is None:
            return 0
        self.ensure_collection()

        # Fit BM25 on the new sections (idempotent on vocab, grows num_docs;
        # for small corpora this is fine. For larger ones we'd want a proper
        # re-fit but we are nowhere near that scale.)
        for section in record.parsed.sections:
            self.bm25.fit_document(section.text)
        self.bm25.save()

        # Delete existing points for this page
        try:
            self.client.delete(
                collection_name=self.collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="page_id",
                                match=models.MatchValue(value=record.id),
                            )
                        ]
                    )
                ),
            )
        except Exception:  # pragma: no cover
            logger.exception("delete-by-filter failed for page %s", record.id)

        if not record.parsed.sections:
            return 0

        texts = [s.text or s.heading for s in record.parsed.sections]
        dense_vectors = await self._embed_fn(texts)

        points: list[models.PointStruct] = []
        for section, text, dense in zip(record.parsed.sections, texts, dense_vectors):
            sparse_indices, sparse_values = self.bm25.encode_document(text)
            vector: dict[str, object] = {"dense": dense}
            if sparse_indices:
                vector["bm25"] = models.SparseVector(
                    indices=sparse_indices, values=sparse_values
                )
            payload = {
                "page_id": record.id,
                "page_title": record.title,
                "section_id": section.id,
                "heading": section.heading,
                "text": text[:3000],
                "tags": record.tags,
                "updated": record.updated,
                "ordinal": section.ordinal,
                "direct_edit": section.direct_edit,
                "derived": section.derived,
            }
            points.append(
                models.PointStruct(
                    id=_point_id(record.id, section.id),
                    vector=vector,
                    payload=payload,
                )
            )
        self.client.upsert(collection_name=self.collection, points=points)
        return len(points)

    def delete_page(self, page_id: str) -> None:
        if self.client is None:
            return
        try:
            self.client.delete(
                collection_name=self.collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="page_id",
                                match=models.MatchValue(value=page_id),
                            )
                        ]
                    )
                ),
            )
        except Exception:  # pragma: no cover
            logger.exception("delete failed for page %s", page_id)

    async def reindex_all(self) -> int:
        total = 0
        for meta in self.store.list_pages():
            record = self.store.read(meta["id"])
            total += await self.index_page(record)
        return total
