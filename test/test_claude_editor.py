"""Tests for src/pages/claude_editor.py (via injection, no real claude CLI)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest

from src.pages.claude_editor import ClaudeEditor, _strip_code_fence, set_editor_fn
from src.pages.data_store import DataStore
from src.pages.store import PageStore


@pytest.fixture
def editor(tmp_pages):
    store = PageStore(pages_dir=tmp_pages)
    data_store = DataStore(store)
    ed = ClaudeEditor(store, data_store)
    store.create(title="Journal", body_html="<h1>Day 1</h1><p>first entry</p>")
    yield ed, store, data_store
    set_editor_fn(None)


async def test_edit_via_injected_fn_updates_page(editor):
    ed, store, _ = editor

    def fake_edit(page_path, instruction, ctx):
        html = page_path.read_text()
        html = html.replace("</body>", f"<section><h2>Injected</h2><p>{instruction}</p></section></body>")
        page_path.write_text(html)

    set_editor_fn(fake_edit)
    result = await ed.edit_page("journal", "add a summary section")
    assert result.ok, result.error
    rec = store.read("journal")
    assert any(s.heading == "Injected" for s in rec.parsed.sections)


async def test_edit_rolls_back_on_invalid_html(editor):
    ed, store, _ = editor

    def broken(page_path, instruction, ctx):
        page_path.write_text("not html at all, no title no body")

    set_editor_fn(broken)
    result = await ed.edit_page("journal", "break things")
    assert not result.ok
    assert result.error is not None
    assert "validation" in result.error or "invalid" in result.error
    # Original page should still be intact
    rec = store.read("journal")
    assert rec.parsed.sections[0].heading == "Day 1"


async def test_edit_unknown_page_returns_error(editor):
    ed, _, _ = editor
    result = await ed.edit_page("does-not-exist", "hi")
    assert not result.ok
    assert result.error


async def test_empty_instruction_rejected(editor):
    ed, _, _ = editor
    result = await ed.edit_page("journal", "   ")
    assert not result.ok
    assert result.error == "empty instruction"


async def test_mock_editor_mode_produces_valid_page(tmp_pages):
    store = PageStore(pages_dir=tmp_pages)
    data_store = DataStore(store)
    store.create(title="M", body_html="<p>x</p>")
    ed = ClaudeEditor(store, data_store, mode="mock")
    result = await ed.edit_page("m", "add a reminder about postgres")
    assert result.ok, result.error
    rec = store.read("m")
    # mock editor appends a <section data-derived>
    assert any(s.derived for s in rec.parsed.sections)


def test_strip_code_fence_removes_markdown_wrapping():
    assert _strip_code_fence("<!doctype html><html></html>") == "<!doctype html><html></html>"
    fenced = "```html\n<!doctype html>\n<html></html>\n```"
    assert _strip_code_fence(fenced) == "<!doctype html>\n<html></html>"
    fenced_no_lang = "```\n<html></html>\n```"
    assert _strip_code_fence(fenced_no_lang) == "<html></html>"


def _fake_llm_client(response_content: str):
    """Build an httpx-shaped fake AsyncClient that returns response_content."""

    @asynccontextmanager
    async def factory(*args, **kwargs):
        client = MagicMock()
        async def post(*a, **kw):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value={
                "choices": [{"message": {"content": response_content}}],
            })
            return resp
        client.post = post
        yield client

    return factory


async def test_llm_editor_writes_returned_html(tmp_pages, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://fake-llm")
    store = PageStore(pages_dir=tmp_pages)
    data_store = DataStore(store)
    rec = store.create(title="Diary", body_html="<h1>Day 1</h1><p>old</p>")

    new_html = (
        "<!doctype html><html><head><title>Diary</title></head>"
        "<body><section><h1>Day 1</h1><p>fresh entry</p></section></body></html>"
    )
    fake = _fake_llm_client(new_html)

    # Patch httpx.AsyncClient inside the module's import scope
    import src.pages.claude_editor as ed_mod
    monkeypatch.setattr(ed_mod, "_run_claude_subprocess", lambda *a, **k: (1, "", "should not be called"))

    # Drive _llm_edit directly with a stub client factory
    from src.pages.claude_editor import _llm_edit
    await _llm_edit(rec.path, "rewrite the entry", {"data_files": [], "page_index": []}, client_factory=fake)
    written = rec.path.read_text()
    assert "fresh entry" in written


async def test_llm_editor_strips_code_fences(tmp_pages, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://fake-llm")
    store = PageStore(pages_dir=tmp_pages)
    rec = store.create(title="X", body_html="<p>x</p>")
    fenced = (
        "```html\n"
        "<!doctype html><html><head><title>X</title></head>"
        "<body><p>fenced</p></body></html>\n"
        "```"
    )
    fake = _fake_llm_client(fenced)
    from src.pages.claude_editor import _llm_edit
    await _llm_edit(rec.path, "set body", {"data_files": [], "page_index": []}, client_factory=fake)
    written = rec.path.read_text()
    assert "fenced" in written
    assert "```" not in written


async def test_llm_editor_rejects_non_html_response(tmp_pages, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://fake-llm")
    store = PageStore(pages_dir=tmp_pages)
    rec = store.create(title="X", body_html="<p>x</p>")
    fake = _fake_llm_client("Sure, I'll do that for you!")
    from src.pages.claude_editor import _llm_edit
    with pytest.raises(RuntimeError, match="not return a full HTML document"):
        await _llm_edit(rec.path, "edit", {"data_files": [], "page_index": []}, client_factory=fake)


async def test_llm_editor_rejects_empty_response(tmp_pages, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://fake-llm")
    store = PageStore(pages_dir=tmp_pages)
    rec = store.create(title="X", body_html="<p>x</p>")
    fake = _fake_llm_client("")
    from src.pages.claude_editor import _llm_edit
    with pytest.raises(RuntimeError, match="empty"):
        await _llm_edit(rec.path, "edit", {"data_files": [], "page_index": []}, client_factory=fake)


async def test_create_page_uses_editor(editor):
    ed, store, _ = editor

    def fake_edit(page_path, instruction, ctx):
        html = page_path.read_text()
        html = html.replace(
            '<em>Creating this page from instruction…</em>',
            f'<ul><li>{instruction}</li></ul>',
        )
        page_path.write_text(html)

    set_editor_fn(fake_edit)
    result = await ed.create_page(
        title="New Ideas",
        initial_instruction="list some product ideas",
    )
    assert result.ok, result.error
    assert result.page_id == "new-ideas"
    rec = store.read("new-ideas")
    assert rec.title == "New Ideas"
