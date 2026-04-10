"""Tests for src/agent_runtime/tools.py — notes tool handlers."""
from __future__ import annotations

import base64
import os

import pytest

os.environ.setdefault("NOTES_DISABLE_QDRANT", "1")
os.environ.setdefault("NOTES_EDITOR", "mock")

from src.agent_runtime.tools import NotesToolContext, make_notes_tool_handler
from src.pages.claude_editor import ClaudeEditor
from src.pages.data_store import DataStore
from src.pages.store import PageStore


@pytest.fixture
def tool_ctx(tmp_pages):
    store = PageStore(pages_dir=tmp_pages)
    data_store = DataStore(store)
    editor = ClaudeEditor(store, data_store, mode="mock")
    store.create(title="Welcome", body_html="<h1>hello</h1><p>intro</p>", tags=["welcome"])
    store.create(title="Projects", body_html="<h1>projects</h1><p>stuff</p>", tags=["work"])
    ctx = NotesToolContext(store=store, data_store=data_store, editor=editor, index=None)
    return ctx, make_notes_tool_handler(ctx)


async def test_list_pages_returns_all(tool_ctx):
    _, handle = tool_ctx
    result = await handle("list_pages", {})
    assert result["total"] == 2
    titles = [p["title"] for p in result["pages"]]
    assert "Welcome" in titles and "Projects" in titles


async def test_list_pages_filters_by_query(tool_ctx):
    _, handle = tool_ctx
    result = await handle("list_pages", {"query": "proj"})
    assert result["total"] == 1
    assert result["pages"][0]["title"] == "Projects"


async def test_list_pages_filters_by_tag(tool_ctx):
    _, handle = tool_ctx
    result = await handle("list_pages", {"tag": "work"})
    assert result["total"] == 1
    assert result["pages"][0]["id"] == "projects"


async def test_read_page_returns_html_and_sections(tool_ctx):
    _, handle = tool_ctx
    result = await handle("read_page", {"page_id": "welcome"})
    assert result["title"] == "Welcome"
    assert "hello" in result["html"].lower()
    assert result["sections"][0]["heading"] == "hello"


async def test_read_page_unknown(tool_ctx):
    _, handle = tool_ctx
    result = await handle("read_page", {"page_id": "nope"})
    assert "error" in result


async def test_create_page_via_editor(tool_ctx):
    ctx, handle = tool_ctx
    result = await handle("create_page", {
        "title": "Reading list",
        "instruction": "list the three books I mentioned",
    })
    assert result["ok"]
    assert result["page_id"] == "reading-list"
    assert ctx.store.exists("reading-list")


async def test_edit_page_via_editor(tool_ctx):
    ctx, handle = tool_ctx
    result = await handle("edit_page", {
        "page_id": "welcome",
        "instruction": "add a reminder about postgres",
    })
    assert result["ok"], result.get("error")
    rec = ctx.store.read("welcome")
    # mock editor appends a derived section with the instruction
    texts = [s.text for s in rec.parsed.sections]
    assert any("postgres" in t for t in texts)


async def test_delete_page(tool_ctx):
    ctx, handle = tool_ctx
    result = await handle("delete_page", {"page_id": "projects"})
    assert result["ok"]
    assert not ctx.store.exists("projects")


async def test_write_and_read_data(tool_ctx):
    ctx, handle = tool_ctx
    write = await handle("write_data", {
        "page_id": "welcome",
        "file": "sales.csv",
        "content": "q,v\nQ1,100\n",
    })
    assert write["ok"]
    read = await handle("read_data", {"page_id": "welcome", "file": "sales.csv"})
    assert "Q1,100" in read["text"]


async def test_write_binary_base64(tool_ctx):
    _, handle = tool_ctx
    payload = b"\x89PNG\r\n\x1a\nhello"
    result = await handle("write_data", {
        "page_id": "welcome",
        "file": "icon.png",
        "content_base64": base64.b64encode(payload).decode("ascii"),
    })
    assert result["ok"]
    read = await handle("read_data", {"page_id": "welcome", "file": "icon.png"})
    assert "base64" in read
    assert base64.b64decode(read["base64"]) == payload


async def test_list_data(tool_ctx):
    _, handle = tool_ctx
    await handle("write_data", {"page_id": "welcome", "file": "a.csv", "content": "a\n"})
    await handle("write_data", {"page_id": "welcome", "file": "b.json", "content": "{}"})
    result = await handle("list_data", {"page_id": "welcome"})
    names = sorted(f["name"] for f in result["files"])
    assert names == ["a.csv", "b.json"]


async def test_delete_data(tool_ctx):
    _, handle = tool_ctx
    await handle("write_data", {"page_id": "welcome", "file": "a.csv", "content": "a\n"})
    result = await handle("delete_data", {"page_id": "welcome", "file": "a.csv"})
    assert result["ok"]
    listing = await handle("list_data", {"page_id": "welcome"})
    assert listing["files"] == []


async def test_search_unavailable_without_index(tool_ctx):
    _, handle = tool_ctx
    result = await handle("search", {"query": "anything"})
    assert "error" in result
    assert result["total"] == 0


async def test_unknown_tool_returns_error(tool_ctx):
    _, handle = tool_ctx
    result = await handle("made_up", {})
    assert "error" in result


async def test_recent_edits_lists_commits(tool_ctx):
    _, handle = tool_ctx
    result = await handle("recent_edits", {"limit": 5})
    assert "commits" in result
    assert len(result["commits"]) >= 2  # at least the seed + creates
