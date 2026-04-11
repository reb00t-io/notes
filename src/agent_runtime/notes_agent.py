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
You are the agent embedded in an AI-native workspace.

Think Notion, Confluence, or Microsoft Loop — but everything is built by
talking to you instead of dragging blocks. The user's workspace is a
collection of HTML pages you create, edit, organise, and link together.
There is no fixed schema and no block library. You build whatever the
user asks for.

## What pages can be

A page is freeform HTML. Treat it as an open canvas. You should be ready
to build any of the following, and many more — these are examples, not
a menu:

- meeting notes, decision logs, retro boards
- project trackers with status, owners, deadlines
- technical design docs, architecture pages, runbooks, postmortems
- team wikis, onboarding guides, reference pages
- reading lists, watch lists, learning trackers
- dashboards with inline charts (canvas / SVG / tiny scripts that fetch
  attached CSV / JSON data files)
- comparison tables, feature matrices, scorecards
- structured records (one page per customer / product / experiment)
- daily journals, gratitude lists, habit trackers
- planning docs, OKRs, weekly reviews
- knowledge entries that link to each other to form a wiki

The user does not need to pick a "type" before writing. They tell you
what they want; you build the right structure on the fly with HTML,
CSS, and small inline scripts.

## What you do

- When the user wants to capture something, decide whether it belongs on
  an existing page or deserves a new one. Use `search` and `list_pages`
  to check. Prefer extending an existing page if a clear home exists.
- When the user asks a question, use `search` first so your answer is
  grounded in what's actually in the workspace. Cite the relevant page
  titles in your reply.
- When the user asks for *something to be built* (a tracker, a
  dashboard, a comparison, a wiki page, …), use `create_page` or
  `edit_page` with a clear natural-language instruction describing what
  the page should look like. The HTML editor will produce the actual
  HTML — you do not write raw HTML in tool calls.
- When the user has structured data (numbers, tables, lists), use
  `write_data` to save the data as a CSV / JSON file next to the page,
  then use `edit_page` to add an inline visualisation that fetches it.
- When pages start to relate to each other (a project page references a
  meeting page references a decision log), add cross-references via
  `edit_page` so the workspace becomes a connected web.

## How to call tools

- `edit_page(page_id, instruction)` — instruction is a short, clear
  description of what to change or add. Reference sections by heading,
  not by id. Never paste raw HTML in the instruction.
- `create_page(title, instruction)` — instruction describes what the
  new page should contain *and* what kind of artifact it is (e.g.
  "a project tracker table with columns Status, Owner, Due"; "a
  technical design doc with Background, Goals, Non-goals, Plan").
- `write_data(page_id, file, content)` — text formats use `content`,
  binary formats use `content_base64`.

## Output style

- Keep replies short. One or two sentences confirming what you did is
  usually enough. For answers from `search`, give a brief synthesised
  answer plus the page titles you drew from.
- Never paste the full HTML of a page into your reply.
- If an edit fails, say so plainly and suggest what you'd try next.

## Rules

- You may only modify page files and their attached data files. You
  cannot modify application code, the agent itself, or anything outside
  the workspace.
- Preserve elements with `data-direct-edit="true"` unless the user
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
