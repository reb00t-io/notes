"""BM25 sparse vector encoder for Qdrant.

Adapted from /Users/marko/dev_p/gmail/src/search/bm25.py. The main difference:
the vocab path is set via the constructor so tests can use tmp_path without
touching module-level state.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

K1 = 1.5
B = 0.75

TOKEN_RE = re.compile(r"\b\w{2,}\b", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _default_vocab_path() -> Path:
    return Path(os.environ.get("DATA_DIR", "data")) / "bm25_vocab.json"


class BM25Encoder:
    """BM25 encoder that builds vocabulary and IDF from documents."""

    def __init__(self, vocab_path: Path | None = None):
        self.vocab_path = Path(vocab_path) if vocab_path else _default_vocab_path()
        self.vocab: dict[str, int] = {}
        self.doc_freq: dict[str, int] = {}
        self.num_docs: int = 0
        self.avg_doc_len: float = 0.0
        self._total_doc_len: int = 0
        self._next_idx: int = 0

    # ── persistence ──────────────────────────────────────────────────

    def save(self) -> None:
        self.vocab_path.parent.mkdir(parents=True, exist_ok=True)
        self.vocab_path.write_text(
            json.dumps(
                {
                    "vocab": self.vocab,
                    "doc_freq": self.doc_freq,
                    "num_docs": self.num_docs,
                    "avg_doc_len": self.avg_doc_len,
                    "total_doc_len": self._total_doc_len,
                    "next_idx": self._next_idx,
                },
                ensure_ascii=False,
            )
        )

    def load(self) -> bool:
        if not self.vocab_path.exists():
            return False
        try:
            data = json.loads(self.vocab_path.read_text())
            self.vocab = data["vocab"]
            self.doc_freq = data["doc_freq"]
            self.num_docs = data["num_docs"]
            self.avg_doc_len = data["avg_doc_len"]
            self._total_doc_len = data["total_doc_len"]
            self._next_idx = data["next_idx"]
            return True
        except Exception:  # pragma: no cover
            logger.exception("failed to load bm25 vocab")
            return False

    # ── fit ──────────────────────────────────────────────────────────

    def _get_or_create_idx(self, token: str) -> int:
        if token not in self.vocab:
            self.vocab[token] = self._next_idx
            self._next_idx += 1
        return self.vocab[token]

    def fit_document(self, text: str) -> None:
        tokens = tokenize(text)
        for token in set(tokens):
            self._get_or_create_idx(token)
            self.doc_freq[token] = self.doc_freq.get(token, 0) + 1
        self.num_docs += 1
        self._total_doc_len += len(tokens)
        self.avg_doc_len = self._total_doc_len / max(self.num_docs, 1)

    # ── encode ───────────────────────────────────────────────────────

    def encode_document(self, text: str) -> tuple[list[int], list[float]]:
        tokens = tokenize(text)
        if not tokens:
            return [], []
        doc_len = len(tokens)
        tf_counts = Counter(tokens)
        indices: list[int] = []
        values: list[float] = []
        for token, tf in tf_counts.items():
            if token not in self.vocab:
                continue
            idx = self.vocab[token]
            df = self.doc_freq.get(token, 0)
            idf = math.log(1 + (self.num_docs - df + 0.5) / (df + 0.5))
            tf_norm = (tf * (K1 + 1)) / (
                tf + K1 * (1 - B + B * doc_len / max(self.avg_doc_len, 1))
            )
            score = idf * tf_norm
            if score > 0:
                indices.append(idx)
                values.append(round(score, 4))
        return indices, values

    def encode_query(self, query: str) -> tuple[list[int], list[float]]:
        tokens = tokenize(query)
        if not tokens:
            return [], []
        indices: list[int] = []
        values: list[float] = []
        seen: set[str] = set()
        for token in tokens:
            if token in seen or token not in self.vocab:
                continue
            seen.add(token)
            idx = self.vocab[token]
            df = self.doc_freq.get(token, 0)
            idf = math.log(1 + (self.num_docs - df + 0.5) / (df + 0.5))
            if idf > 0:
                indices.append(idx)
                values.append(round(idf, 4))
        return indices, values
