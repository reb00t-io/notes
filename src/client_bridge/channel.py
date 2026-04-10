"""Client bridge: routes tool calls from the agent to the user's open tab.

Architecture:
- The frontend opens a WebSocket to /v1/bridge?session_id=X and calls
  `register(session_id, send, recv)`.
- When the agent wants to call a dom_* / get_client_logs tool, the backend
  dispatches via `call_tool(session_id, name, args)` which:
  1. Assigns a unique request id
  2. Sends `{type: "call", id, name, args}` to the client
  3. Awaits a `{type: "result", id, result}` reply (with a timeout)
- If no tab is connected, calls fall back to `{error: "tab_unavailable"}`.

This module is kept framework-agnostic so it's easy to test without Quart.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
MAX_LOG_BUFFER = 500


@dataclass
class BridgeClient:
    session_id: str
    send: "asyncio.Queue[dict]" = field(default_factory=asyncio.Queue)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    connected_at: float = field(default_factory=time.time)
    log_buffer: list[dict] = field(default_factory=list)


class BridgeRegistry:
    """In-process registry of active bridge clients, keyed by session_id."""

    def __init__(self) -> None:
        self._clients: dict[str, BridgeClient] = {}
        self._lock = asyncio.Lock()

    async def register(self, session_id: str) -> BridgeClient:
        async with self._lock:
            existing = self._clients.get(session_id)
            if existing is not None:
                # Disconnect the old one by failing its pending futures
                for fut in existing.pending.values():
                    if not fut.done():
                        fut.set_result({"error": "tab_replaced"})
            client = BridgeClient(session_id=session_id)
            self._clients[session_id] = client
            return client

    async def unregister(self, session_id: str) -> None:
        async with self._lock:
            client = self._clients.pop(session_id, None)
        if client is not None:
            for fut in client.pending.values():
                if not fut.done():
                    fut.set_result({"error": "tab_disconnected"})

    def get(self, session_id: str) -> BridgeClient | None:
        return self._clients.get(session_id)

    def is_connected(self, session_id: str) -> bool:
        return session_id in self._clients

    # ── messages from client ──────────────────────────────────────────

    def ingest(self, session_id: str, message: dict) -> None:
        client = self._clients.get(session_id)
        if client is None:
            return
        mtype = message.get("type")
        if mtype == "result":
            req_id = str(message.get("id") or "")
            fut = client.pending.pop(req_id, None)
            if fut and not fut.done():
                fut.set_result(message.get("result") or {})
        elif mtype == "log":
            entry = message.get("entry") or {}
            client.log_buffer.append(entry)
            if len(client.log_buffer) > MAX_LOG_BUFFER:
                client.log_buffer = client.log_buffer[-MAX_LOG_BUFFER:]

    # ── calls from backend ────────────────────────────────────────────

    async def call_tool(
        self,
        session_id: str,
        name: str,
        args: dict[str, Any],
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        client = self._clients.get(session_id)
        if client is None:
            return {"error": "tab_unavailable"}

        req_id = uuid.uuid4().hex[:10]
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        client.pending[req_id] = fut
        await client.send.put(
            {"type": "call", "id": req_id, "name": name, "args": args}
        )
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            client.pending.pop(req_id, None)
            return {"error": "tab_timeout"}

    def get_logs(
        self, session_id: str, *, limit: int = 50, level: str | None = None
    ) -> dict[str, Any]:
        client = self._clients.get(session_id)
        if client is None:
            return {"error": "tab_unavailable", "logs": []}
        logs = client.log_buffer
        if level:
            logs = [l for l in logs if l.get("level") == level]
        return {"logs": logs[-limit:]}


# Module-level singleton used by the Quart route + tool handler
_registry: BridgeRegistry | None = None


def get_registry() -> BridgeRegistry:
    global _registry
    if _registry is None:
        _registry = BridgeRegistry()
    return _registry


def reset_registry() -> None:
    """Test helper."""
    global _registry
    _registry = None
