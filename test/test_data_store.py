"""Tests for src/pages/data_store.py."""
from __future__ import annotations

import base64

import pytest

from src.pages.data_store import DataStore, DataStoreError, MAX_FILE_BYTES
from src.pages.store import PageStore, PageStoreError


def make_stores(tmp_pages):
    store = PageStore(pages_dir=tmp_pages)
    return store, DataStore(store)


def test_write_and_read_text_file(tmp_pages):
    store, data_store = make_stores(tmp_pages)
    store.create(title="Sales", body_html="<p>see data</p>")
    info = data_store.write("sales", "q1.csv", "quarter,sales\nQ1,100\n")
    assert info.size > 0
    assert info.name == "q1.csv"
    text = data_store.read_text("sales", "q1.csv")
    assert "Q1,100" in text


def test_list_returns_files(tmp_pages):
    store, data_store = make_stores(tmp_pages)
    store.create(title="X", body_html="<p>x</p>")
    data_store.write("x", "a.csv", "a,b\n1,2\n")
    data_store.write("x", "b.json", '{"k":1}')
    files = data_store.list("x")
    names = sorted(f.name for f in files)
    assert names == ["a.csv", "b.json"]


def test_list_empty_for_page_without_data(tmp_pages):
    store, data_store = make_stores(tmp_pages)
    store.create(title="X", body_html="<p>x</p>")
    assert data_store.list("x") == []


def test_reject_disallowed_extension(tmp_pages):
    store, data_store = make_stores(tmp_pages)
    store.create(title="X", body_html="<p>x</p>")
    with pytest.raises(DataStoreError):
        data_store.write("x", "evil.exe", b"MZ")


def test_reject_path_traversal(tmp_pages):
    store, data_store = make_stores(tmp_pages)
    store.create(title="X", body_html="<p>x</p>")
    with pytest.raises(DataStoreError):
        data_store.write("x", "../escape.csv", "nope")
    with pytest.raises(DataStoreError):
        data_store.write("x", "sub/file.csv", "nope")


def test_reject_file_too_large(tmp_pages):
    store, data_store = make_stores(tmp_pages)
    store.create(title="X", body_html="<p>x</p>")
    big = b"0" * (MAX_FILE_BYTES + 1)
    with pytest.raises(DataStoreError):
        data_store.write("x", "big.csv", big)


def test_write_base64(tmp_pages):
    store, data_store = make_stores(tmp_pages)
    store.create(title="X", body_html="<p>x</p>")
    payload = b"\x89PNG\r\n\x1a\n"
    b64 = base64.b64encode(payload).decode("ascii")
    data_store.write_base64("x", "logo.png", b64)
    assert data_store.read_bytes("x", "logo.png") == payload


def test_delete_removes_file_and_dir_when_empty(tmp_pages):
    store, data_store = make_stores(tmp_pages)
    store.create(title="X", body_html="<p>x</p>")
    data_store.write("x", "a.csv", "a\n")
    data_store.delete("x", "a.csv")
    assert data_store.list("x") == []
    # dir should be gone
    assert not (tmp_pages / "x.data").exists()


def test_write_to_unknown_page_raises(tmp_pages):
    store, data_store = make_stores(tmp_pages)
    with pytest.raises(PageStoreError):
        data_store.write("does-not-exist", "a.csv", "a\n")


def test_is_text_detection(tmp_pages):
    assert DataStore.is_text("a.csv") is True
    assert DataStore.is_text("a.json") is True
    assert DataStore.is_text("a.png") is False
    assert DataStore.is_text("a.jpg") is False
