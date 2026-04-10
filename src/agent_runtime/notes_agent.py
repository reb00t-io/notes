"""The single product agent.

Wires together the page store, data store, Claude Code editor, and Qdrant
index; registers the notes tools with the tool executor; owns the system
prompt.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from ..pages.claude_editor import ClaudeEditor
    from ..pages.data_store import DataStore
    from ..pages.index import PageIndex, get_qdrant_client
    from ..pages.store import PageStore
    from ..tool_executor import register_tool_handler
    from .tools import NOTES_TOOL_SCHEMAS, NotesToolContext, make_notes_tool_handler
except ImportError:  # pragma: no cover
    from pages.claude_editor import ClaudeEditor  # type: ignore
    from pages.data_store import DataStore  # type: ignore
    from pages.index import PageIndex, get_qdrant_client  # type: ignore
    from pages.store import PageStore  # type: ignore
    from tool_executor import register_tool_handler  # type: ignore
    from agent_runtime.tools import NOTES_TOOL_SCHEMAS, NotesToolContext, make_notes_tool_handler  # type: ignore

logger = logging.getLogger(__name__)


NOTES_SYSTEM_PROMPT = """\
You are the agent embedded in a personal notes app.

You help the user capture, recall, and reshape knowledge. The user's notes
live as HTML pages you manage with tools. There is no fixed structure: every
page is freeform HTML that you grow over time.

## What you do

- When the user wants to capture something, find the right existing page (via
  `search` or `list_pages`) or create a new one. Prefer adding to an existing
  page if a good match exists.
- When the user asks a question, use `search` first to ground your answer in
  their actual notes. Cite page titles in your reply.
- When the user wants to change a page, use `edit_page` with a clear
  natural-language instruction. Claude Code will perform the actual HTML
  edit — you don't need to write raw HTML in your tool call.
- When the user has structured data (numbers, tables, lists to plot), use
  `write_data` to save the data as a file next to the page, then use
  `edit_page` to add an inline visualisation (canvas, SVG, or a tiny inline
  script fetching the data file).

## How to call tools

- `edit_page(page_id, instruction)` — instruction should be a short, clear
  description of what to change. Do not include raw HTML. Reference section
  headings by name, not by id.
- `create_page(title, instruction)` — instruction describes what the new
  page should contain.
- `write_data(page_id, file, content)` — for CSV/JSON/text. Use
  `content_base64` for binary.

## Output style

- Keep replies short. One or two sentences confirming what you did is usually
  enough. If you answered from search, include a brief synthesized answer
  plus page titles to look at.
- Never paste the full HTML of a page into your reply.
- If an edit fails, say so plainly and suggest what you'd try next.

## Rules

- You may only modify page files and their attached data files. You cannot
  modify the application code, the agent itself, or anything outside
  `pages/`.
- Preserve direct edits (`data-direct-edit="true"`) unless the user
  explicitly asks you to change them.
"""


@dataclass
class NotesAgent:
    store: PageStore
    data_store: DataStore
    editor: ClaudeEditor
    index: PageIndex | None
    tools: list[dict]
    system_prompt: str


def _should_connect_qdrant() -> bool:
    return os.environ.get("NOTES_DISABLE_QDRANT", "").lower() not in {"1", "true", "yes"}


def build_notes_agent(
    *,
    pages_dir: Path | None = None,
    qdrant_client=None,
    editor_mode: str | None = None,
) -> NotesAgent:
    store = PageStore(pages_dir=pages_dir)
    data_store = DataStore(store)

    client = qdrant_client
    if client is None and _should_connect_qdrant():
        try:
            client = get_qdrant_client()
            # Touch it to see if it's actually up
            client.get_collections()
        except Exception as exc:
            logger.warning("qdrant unavailable (%s); search will be disabled", exc)
            client = None

    index: PageIndex | None
    if client is not None:
        index = PageIndex(store, client=client)
        try:
            index.ensure_collection()
        except Exception:  # pragma: no cover
            logger.exception("failed to ensure qdrant collection")
            index = None
    else:
        index = None

    async def reindex(record):
        if index is None:
            return 0
        return await index.index_page(record)

    editor = ClaudeEditor(store, data_store, reindex=reindex, mode=editor_mode)

    ctx = NotesToolContext(store=store, data_store=data_store, editor=editor, index=index)
    handler = make_notes_tool_handler(ctx)
    register_tool_handler(handler)

    return NotesAgent(
        store=store,
        data_store=data_store,
        editor=editor,
        index=index,
        tools=list(NOTES_TOOL_SCHEMAS),
        system_prompt=NOTES_SYSTEM_PROMPT,
    )
