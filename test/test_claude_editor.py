"""Tests for src/pages/claude_editor.py.

The real Claude Code subprocess is never invoked here:
- Most tests use the injection seam (set_editor_fn) to install a fake.
- The mock-mode test exercises the deterministic test stub.
- test_claude_mode_invokes_claude_agent monkey-patches ClaudeAgent.run
  to verify the dispatch path without spawning the real CLI.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.pages.claude_editor import (
    CLAUDE_ALLOWED_TOOLS,
    ClaudeEditor,
    _build_prompt,
    set_editor_fn,
)
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


# ─── injection seam ─────────────────────────────────────────────────


async def test_edit_via_injected_fn_updates_page(editor):
    ed, store, _ = editor

    def fake_edit(page_path, instruction, ctx):
        html = page_path.read_text()
        html = html.replace(
            "</body>",
            f"<section><h2>Injected</h2><p>{instruction}</p></section></body>",
        )
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


async def test_create_page_uses_editor(editor):
    ed, store, _ = editor

    def fake_edit(page_path, instruction, ctx):
        html = page_path.read_text()
        html = html.replace(
            '<em>Building this page from instruction…</em>',
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


# ─── mock mode ──────────────────────────────────────────────────────


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


# ─── claude mode (ClaudeAgent integration, mocked subprocess) ───────


async def test_claude_mode_invokes_claude_agent(tmp_pages, monkeypatch):
    """In claude mode, the editor should call ClaudeAgent.run() with
    the pages dir as cwd and a focused prompt naming the target file."""
    set_editor_fn(None)
    captured: dict = {}

    def fake_run(self, repo_dir, prompt):
        captured["repo_dir"] = Path(repo_dir)
        captured["prompt"] = prompt
        captured["allowed_tools"] = self.allowed_tools
        # Apply a minimal change so validation passes downstream
        target = Path(repo_dir) / "diary.html"
        html = target.read_text()
        html = html.replace(
            "</body>",
            "<section><h2>Edited</h2><p>by fake claude</p></section></body>",
        )
        target.write_text(html)

    from agent_scripts.agent import ClaudeAgent
    monkeypatch.setattr(ClaudeAgent, "run", fake_run)

    store = PageStore(pages_dir=tmp_pages)
    data_store = DataStore(store)
    store.create(title="Diary", body_html="<h1>Day 1</h1><p>old</p>")
    editor = ClaudeEditor(store, data_store, mode="claude")

    result = await editor.edit_page("diary", "add a section called Edited")
    assert result.ok, result.error
    assert captured["repo_dir"] == tmp_pages
    assert "diary.html" in captured["prompt"]
    assert "add a section called Edited" in captured["prompt"]
    assert captured["allowed_tools"] == CLAUDE_ALLOWED_TOOLS
    rec = store.read("diary")
    assert any(s.heading == "Edited" for s in rec.parsed.sections)


async def test_claude_mode_rolls_back_on_subprocess_error(tmp_pages, monkeypatch):
    """If ClaudeAgent.run raises CalledProcessError the page is restored."""
    set_editor_fn(None)
    import subprocess

    def fake_run(self, repo_dir, prompt):
        raise subprocess.CalledProcessError(returncode=2, cmd=["claude"])

    from agent_scripts.agent import ClaudeAgent
    monkeypatch.setattr(ClaudeAgent, "run", fake_run)

    store = PageStore(pages_dir=tmp_pages)
    data_store = DataStore(store)
    store.create(title="Diary", body_html="<h1>Day 1</h1><p>untouched</p>")
    editor = ClaudeEditor(store, data_store, mode="claude")

    result = await editor.edit_page("diary", "do something")
    assert not result.ok
    assert "claude exited" in (result.error or "")
    # Original page must still be intact (snapshot restored)
    rec = store.read("diary")
    assert "untouched" in rec.path.read_text()


async def test_claude_mode_handles_missing_binary(tmp_pages, monkeypatch):
    set_editor_fn(None)

    def fake_run(self, repo_dir, prompt):
        raise FileNotFoundError("claude")

    from agent_scripts.agent import ClaudeAgent
    monkeypatch.setattr(ClaudeAgent, "run", fake_run)

    store = PageStore(pages_dir=tmp_pages)
    data_store = DataStore(store)
    store.create(title="Diary", body_html="<p>x</p>")
    editor = ClaudeEditor(store, data_store, mode="claude")

    result = await editor.edit_page("diary", "do something")
    assert not result.ok
    assert "binary not found" in (result.error or "")


# ─── prompt builder ─────────────────────────────────────────────────


def test_build_prompt_includes_target_filename_and_sections(tmp_pages):
    store = PageStore(pages_dir=tmp_pages)
    rec = store.create(title="X", body_html="<h1>Alpha</h1><h2>Beta</h2>")
    ctx = {
        "page_index": rec.parsed.section_index(),
        "data_files": [{"name": "sales.csv", "size": 42}],
    }
    prompt = _build_prompt(rec, "add a footer", ctx)
    assert "x.html" in prompt
    assert "add a footer" in prompt
    assert "Alpha" in prompt
    assert "x.data/sales.csv" in prompt
    assert "/v1/pages/x/data/" in prompt
