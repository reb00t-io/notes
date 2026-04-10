"""Orchestrator tool schemas + handlers for the notes agent.

The tool schemas are the OpenAI function-calling format, which the existing
/v1/responses flow passes straight through to the LLM. Handlers are bound at
startup via `make_handlers(ctx)` and registered with the tool_executor.

Design notes:
- edit_page is the only HTML-mutating tool. It delegates to Claude Code.
- read_data returns text for text files, base64 for binary.
- search wraps the Qdrant hybrid search. If no qdrant client is present the
  tool returns an empty result set with an explanation.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

try:
    from ..pages.claude_editor import ClaudeEditor
    from ..pages.data_store import DataStore, DataStoreError, TEXT_EXTS
    from ..pages.index import PageIndex
    from ..pages.search import search as hybrid_search
    from ..pages.store import PageStore, PageStoreError
except ImportError:  # pragma: no cover
    from pages.claude_editor import ClaudeEditor  # type: ignore
    from pages.data_store import DataStore, DataStoreError, TEXT_EXTS  # type: ignore
    from pages.index import PageIndex  # type: ignore
    from pages.search import search as hybrid_search  # type: ignore
    from pages.store import PageStore, PageStoreError  # type: ignore

logger = logging.getLogger(__name__)


# ── schemas ──────────────────────────────────────────────────────────

def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


NOTES_TOOL_SCHEMAS: list[dict] = [
    _fn(
        "list_pages",
        "List pages in the user's notebook, most recently updated first.",
        {
            "query": {"type": "string", "description": "Optional case-insensitive title substring filter."},
            "tag": {"type": "string", "description": "Optional tag filter."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 30},
        },
        [],
    ),
    _fn(
        "read_page",
        "Read a page by id. Returns title, tags, section index, full HTML, and attached data files.",
        {"page_id": {"type": "string", "description": "The page slug."}},
        ["page_id"],
    ),
    _fn(
        "search",
        "Hybrid BM25+vector search across all pages. Returns the most relevant sections as snippets.",
        {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
            "page_id": {"type": "string", "description": "Optional: restrict search to one page."},
        },
        ["query"],
    ),
    _fn(
        "create_page",
        "Create a new page with a title and a natural-language instruction describing what the page should contain. Claude Code writes the initial HTML.",
        {
            "title": {"type": "string"},
            "instruction": {"type": "string", "description": "What the page should contain."},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        ["title", "instruction"],
    ),
    _fn(
        "edit_page",
        "Edit an existing page with a natural-language instruction. Claude Code performs the actual HTML edit.",
        {
            "page_id": {"type": "string"},
            "instruction": {"type": "string", "description": "What should change on this page."},
        },
        ["page_id", "instruction"],
    ),
    _fn(
        "delete_page",
        "Delete a page and its attached data files.",
        {"page_id": {"type": "string"}},
        ["page_id"],
    ),
    _fn(
        "list_data",
        "List data files attached to a page.",
        {"page_id": {"type": "string"}},
        ["page_id"],
    ),
    _fn(
        "read_data",
        "Read a data file attached to a page. Text files (csv/json/txt/md/svg) return their content; binary files return base64.",
        {
            "page_id": {"type": "string"},
            "file": {"type": "string"},
        },
        ["page_id", "file"],
    ),
    _fn(
        "write_data",
        "Create or overwrite a data file attached to a page. Use `content` for text files and `content_base64` for binary. Max 10 MB.",
        {
            "page_id": {"type": "string"},
            "file": {"type": "string"},
            "content": {"type": "string", "description": "Text content (for text files)."},
            "content_base64": {"type": "string", "description": "Base64-encoded content (for binary files)."},
        },
        ["page_id", "file"],
    ),
    _fn(
        "delete_data",
        "Delete a data file attached to a page.",
        {
            "page_id": {"type": "string"},
            "file": {"type": "string"},
        },
        ["page_id", "file"],
    ),
    _fn(
        "recent_edits",
        "Return recent edit history (git commits on the pages repo).",
        {"limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}},
        [],
    ),
]


# ── context ──────────────────────────────────────────────────────────


@dataclass
class NotesToolContext:
    store: PageStore
    data_store: DataStore
    editor: ClaudeEditor
    index: PageIndex | None = None


# ── handlers ─────────────────────────────────────────────────────────


async def _list_pages(ctx: NotesToolContext, args: dict) -> dict:
    pages = ctx.store.list_pages()
    q = (args.get("query") or "").strip().lower()
    tag = (args.get("tag") or "").strip().lower()
    if q:
        pages = [p for p in pages if q in p["title"].lower()]
    if tag:
        pages = [p for p in pages if tag in [t.lower() for t in p["tags"]]]
    limit = int(args.get("limit") or 30)
    return {"pages": pages[:limit], "total": len(pages)}


async def _read_page(ctx: NotesToolContext, args: dict) -> dict:
    page_id = args.get("page_id", "")
    try:
        rec = ctx.store.read(page_id)
    except PageStoreError as exc:
        return {"error": str(exc)}
    try:
        data_files = [df.__dict__ for df in ctx.data_store.list(page_id)]
    except Exception:  # pragma: no cover
        data_files = []
    return {
        "id": rec.id,
        "title": rec.title,
        "tags": rec.tags,
        "created": rec.created,
        "updated": rec.updated,
        "sections": rec.parsed.section_index(),
        "html": rec.path.read_text(encoding="utf-8"),
        "data_files": data_files,
    }


async def _search(ctx: NotesToolContext, args: dict) -> dict:
    if ctx.index is None or ctx.index.client is None:
        return {
            "query": args.get("query", ""),
            "results": [],
            "total": 0,
            "error": "search index not available",
        }
    return await hybrid_search(
        client=ctx.index.client,
        bm25=ctx.index.bm25,
        query=args.get("query", ""),
        limit=int(args.get("limit") or 8),
        page_id=args.get("page_id") or None,
        collection=ctx.index.collection,
    )


async def _create_page(ctx: NotesToolContext, args: dict) -> dict:
    title = (args.get("title") or "").strip()
    instruction = (args.get("instruction") or "").strip()
    if not title or not instruction:
        return {"error": "title and instruction are required"}
    result = await ctx.editor.create_page(
        title=title,
        initial_instruction=instruction,
        tags=args.get("tags") or [],
    )
    return {
        "ok": result.ok,
        "page_id": result.page_id,
        "summary": result.summary,
        "error": result.error,
    }


async def _edit_page(ctx: NotesToolContext, args: dict) -> dict:
    page_id = (args.get("page_id") or "").strip()
    instruction = (args.get("instruction") or "").strip()
    if not page_id or not instruction:
        return {"error": "page_id and instruction are required"}
    result = await ctx.editor.edit_page(page_id, instruction)
    return {
        "ok": result.ok,
        "page_id": result.page_id,
        "rev": result.rev,
        "summary": result.summary,
        "error": result.error,
        "stdout_tail": result.stdout_tail,
    }


async def _delete_page(ctx: NotesToolContext, args: dict) -> dict:
    page_id = (args.get("page_id") or "").strip()
    try:
        ctx.store.delete(page_id)
    except PageStoreError as exc:
        return {"error": str(exc)}
    if ctx.index is not None:
        ctx.index.delete_page(page_id)
    return {"ok": True, "page_id": page_id}


async def _list_data(ctx: NotesToolContext, args: dict) -> dict:
    try:
        files = ctx.data_store.list(args.get("page_id", ""))
    except (PageStoreError, DataStoreError) as exc:
        return {"error": str(exc)}
    return {"files": [f.__dict__ for f in files]}


async def _read_data(ctx: NotesToolContext, args: dict) -> dict:
    page_id = args.get("page_id", "")
    name = args.get("file", "")
    try:
        raw = ctx.data_store.read_bytes(page_id, name)
    except (PageStoreError, DataStoreError) as exc:
        return {"error": str(exc)}
    if DataStore.is_text(name):
        try:
            return {"file": name, "text": raw.decode("utf-8")}
        except UnicodeDecodeError:
            pass
    return {"file": name, "base64": base64.b64encode(raw).decode("ascii")}


async def _write_data(ctx: NotesToolContext, args: dict) -> dict:
    page_id = args.get("page_id", "")
    name = args.get("file", "")
    try:
        if "content_base64" in args and args["content_base64"] is not None:
            info = ctx.data_store.write_base64(page_id, name, args["content_base64"])
        elif "content" in args and args["content"] is not None:
            info = ctx.data_store.write(page_id, name, args["content"])
        else:
            return {"error": "provide content or content_base64"}
    except (PageStoreError, DataStoreError) as exc:
        return {"error": str(exc)}
    return {"ok": True, "file": info.__dict__}


async def _delete_data(ctx: NotesToolContext, args: dict) -> dict:
    try:
        ctx.data_store.delete(args.get("page_id", ""), args.get("file", ""))
    except (PageStoreError, DataStoreError) as exc:
        return {"error": str(exc)}
    return {"ok": True}


async def _recent_edits(ctx: NotesToolContext, args: dict) -> dict:
    limit = int(args.get("limit") or 10)
    return {"commits": ctx.store.recent_commits(limit=limit)}


# ── dispatcher ───────────────────────────────────────────────────────


HandlerFn = Callable[[NotesToolContext, dict], Awaitable[dict]]


def _handlers() -> dict[str, HandlerFn]:
    return {
        "list_pages": _list_pages,
        "read_page": _read_page,
        "search": _search,
        "create_page": _create_page,
        "edit_page": _edit_page,
        "delete_page": _delete_page,
        "list_data": _list_data,
        "read_data": _read_data,
        "write_data": _write_data,
        "delete_data": _delete_data,
        "recent_edits": _recent_edits,
    }


def make_notes_tool_handler(ctx: NotesToolContext) -> Callable[[str, dict[str, Any]], Awaitable[dict]]:
    """Return a handler that the tool_executor can call by tool name + args."""
    handlers = _handlers()

    async def handle(name: str, args: dict[str, Any]) -> dict:
        fn = handlers.get(name)
        if fn is None:
            return {"error": f"unknown notes tool: {name}"}
        try:
            return await fn(ctx, args)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("notes tool %s raised", name)
            return {"error": f"{type(exc).__name__}: {exc}"}

    return handle
