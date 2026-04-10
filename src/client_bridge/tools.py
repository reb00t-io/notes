"""Bridge-routed tools (dom_*, get_client_logs, reload_page).

These tools run against the user's open browser tab via the BridgeRegistry.
They register with the tool_executor the same way the page tools do, but
they need the session_id which isn't part of the tool call — so we rely on
a contextvar set at the top of each tool-call round.
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any

try:
    from .channel import get_registry
except ImportError:  # pragma: no cover
    from client_bridge.channel import get_registry  # type: ignore

logger = logging.getLogger(__name__)


# Set by main.py / streaming at the start of a tool round so bridge tools
# know which open tab to talk to.
current_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_session_id", default=None
)


BRIDGE_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "dom_query",
            "description": "Read elements from the page currently rendered in the user's browser. Returns matching elements with text content, attributes, and computed styles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dom_eval",
            "description": "Run a small JavaScript snippet in the currently open page. Returns the JSON-serialisable result. Do not use this for persistent changes; use edit_page for that.",
            "parameters": {
                "type": "object",
                "properties": {
                    "js": {"type": "string", "description": "JavaScript to evaluate in the page context."},
                },
                "required": ["js"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dom_patch",
            "description": "Apply a transient DOM change as a preview. The change is visible in the user's browser but NOT persisted; follow up with edit_page to make it durable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["set_text", "set_html", "set_attr", "add_class", "remove_class"],
                    },
                    "value": {"type": "string"},
                    "attr": {"type": "string", "description": "Required for set_attr."},
                },
                "required": ["selector", "action", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reload_page",
            "description": "Force the user's browser to re-render the currently open page, so the agent can verify the effect of a recent edit.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_client_logs",
            "description": "Return recent console messages, network failures, and runtime exceptions captured in the user's browser tab.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                    "level": {"type": "string", "enum": ["log", "info", "warn", "error"]},
                },
            },
        },
    },
]


BRIDGE_TOOL_NAMES = {s["function"]["name"] for s in BRIDGE_TOOL_SCHEMAS}


async def handle_bridge_tool(name: str, args: dict[str, Any]) -> dict | None:
    if name not in BRIDGE_TOOL_NAMES:
        return {"error": f"unknown notes tool: {name}"}  # sentinel for registry fall-through

    session_id = current_session_id.get()
    if not session_id:
        return {"error": "no_session_context"}

    registry = get_registry()

    if name == "get_client_logs":
        return registry.get_logs(
            session_id,
            limit=int(args.get("limit") or 50),
            level=args.get("level"),
        )

    if name == "reload_page":
        return await registry.call_tool(session_id, "reload_page", {})

    return await registry.call_tool(session_id, name, args)
