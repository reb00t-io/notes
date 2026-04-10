"""Tests for src/pages/bm25.py."""
from __future__ import annotations

import json

from src.pages.bm25 import BM25Encoder, tokenize


def test_tokenize_lowercases_and_splits():
    assert tokenize("Hello, World!") == ["hello", "world"]
    assert tokenize("a") == []  # two-char minimum
    assert tokenize("") == []


def test_fit_and_encode_document(tmp_path):
    bm25 = BM25Encoder(vocab_path=tmp_path / "v.json")
    bm25.fit_document("postgres locks are tricky")
    bm25.fit_document("postgres is a database")
    bm25.fit_document("locks on files not postgres")
    indices, values = bm25.encode_document("postgres locks")
    assert len(indices) == len(values)
    assert len(indices) >= 2
    assert all(v > 0 for v in values)


def test_encode_query_returns_idf_weighted(tmp_path):
    bm25 = BM25Encoder(vocab_path=tmp_path / "v.json")
    for doc in ["the postgres database", "apple orange banana", "postgres is great"]:
        bm25.fit_document(doc)
    indices, values = bm25.encode_query("postgres banana")
    assert len(indices) == 2
    # banana appears in 1 doc, postgres in 2 → banana should score higher
    by_index = dict(zip(indices, values))
    banana_idx = bm25.vocab["banana"]
    postgres_idx = bm25.vocab["postgres"]
    assert by_index[banana_idx] > by_index[postgres_idx]


def test_save_and_load_vocab_roundtrip(tmp_path):
    path = tmp_path / "vocab.json"
    bm25 = BM25Encoder(vocab_path=path)
    bm25.fit_document("alpha beta gamma")
    bm25.save()
    assert path.exists()
    data = json.loads(path.read_text())
    assert "alpha" in data["vocab"]

    reloaded = BM25Encoder(vocab_path=path)
    assert reloaded.load() is True
    assert reloaded.vocab == bm25.vocab
    assert reloaded.num_docs == 1


def test_unknown_query_terms_are_dropped(tmp_path):
    bm25 = BM25Encoder(vocab_path=tmp_path / "v.json")
    bm25.fit_document("alpha beta")
    indices, _ = bm25.encode_query("gamma delta")
    assert indices == []


def test_empty_text_produces_empty_vectors(tmp_path):
    bm25 = BM25Encoder(vocab_path=tmp_path / "v.json")
    assert bm25.encode_document("") == ([], [])
    assert bm25.encode_query("") == ([], [])
