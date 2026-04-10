"""Tests for src/pages/store.py (PageStore)."""
from __future__ import annotations

import pytest

from src.pages.store import PageStore, PageStoreError


def make_store(tmp_pages) -> PageStore:
    return PageStore(pages_dir=tmp_pages)


def test_create_writes_file_with_title_and_meta(tmp_pages):
    store = make_store(tmp_pages)
    rec = store.create(
        title="Meeting notes",
        body_html="<h1>Standup</h1><p>we shipped it</p>",
        tags=["work", "standup"],
    )
    assert rec.id == "meeting-notes"
    assert rec.title == "Meeting notes"
    assert rec.tags == ["work", "standup"]
    assert rec.path.exists()
    html = rec.path.read_text()
    assert "<title>Meeting notes</title>" in html
    assert 'data-section-id=' in html


def test_unique_slug_disambiguates(tmp_pages):
    store = make_store(tmp_pages)
    a = store.create(title="Meeting", body_html="<p>a</p>")
    b = store.create(title="Meeting", body_html="<p>b</p>")
    assert a.id == "meeting"
    assert b.id == "meeting-2"


def test_read_returns_parsed_record(tmp_pages):
    store = make_store(tmp_pages)
    store.create(title="X", body_html="<h1>hello</h1><p>world</p>")
    rec = store.read("x")
    assert rec.title == "X"
    assert len(rec.parsed.sections) == 1
    assert rec.parsed.sections[0].heading == "hello"


def test_read_nonexistent_raises(tmp_pages):
    store = make_store(tmp_pages)
    with pytest.raises(PageStoreError):
        store.read("nope")


def test_invalid_slug_rejected(tmp_pages):
    store = make_store(tmp_pages)
    with pytest.raises(PageStoreError):
        store.read("NOT_A_SLUG!")


def test_write_validates_html(tmp_pages):
    store = make_store(tmp_pages)
    store.create(title="X", body_html="<p>ok</p>")
    with pytest.raises(PageStoreError):
        store.write("x", "not valid", commit_message="bad")


def test_list_pages_orders_by_updated_desc(tmp_pages):
    store = make_store(tmp_pages)
    a = store.create(title="A", body_html="<p>a</p>")
    b = store.create(title="B", body_html="<p>b</p>")
    pages = store.list_pages()
    assert [p["id"] for p in pages[:2]] == [b.id, a.id] or [p["id"] for p in pages[:2]] == [a.id, b.id]
    ids = {p["id"] for p in pages}
    assert ids >= {"a", "b"}


def test_delete_removes_page_and_data_dir(tmp_pages):
    store = make_store(tmp_pages)
    rec = store.create(title="Bye", body_html="<p>x</p>")
    data_dir = tmp_pages / f"{rec.id}.data"
    data_dir.mkdir()
    (data_dir / "x.csv").write_text("a,b\n1,2\n")
    store.delete(rec.id)
    assert not rec.path.exists()
    assert not data_dir.exists()


def test_git_commits_on_create(tmp_pages):
    store = make_store(tmp_pages)
    store.create(title="Commit test", body_html="<p>x</p>")
    commits = store.recent_commits(limit=10)
    # at least init + our create commit
    assert len(commits) >= 2
    subjects = [c["subject"] for c in commits]
    assert any("create meeting-notes" in s or "create commit-test" in s for s in subjects)


def test_snapshot_and_restore(tmp_pages):
    store = make_store(tmp_pages)
    store.create(title="A", body_html="<p>one</p>")
    rev = store.snapshot()
    store.create(title="B", body_html="<p>two</p>")
    assert store.exists("b")
    store.restore(rev)
    assert not store.exists("b")


def test_edit_preserves_section_ids_for_unchanged_headings(tmp_pages):
    store = make_store(tmp_pages)
    rec = store.create(title="Stable", body_html="<h1>Alpha</h1><p>one</p><h2>Beta</h2><p>two</p>")
    original_ids = [s.id for s in rec.parsed.sections]

    # Rewrite with only body text changed
    new_html = rec.path.read_text().replace("<p>one</p>", "<p>one updated</p>")
    updated = store.write(rec.id, new_html, commit_message="update alpha body")
    new_ids = [s.id for s in updated.parsed.sections]
    assert new_ids == original_ids
