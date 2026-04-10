"""Tests for src/client_bridge/channel.py."""
from __future__ import annotations

import asyncio

import pytest

from src.client_bridge.channel import BridgeRegistry, reset_registry


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry()


async def test_unknown_session_returns_tab_unavailable():
    reg = BridgeRegistry()
    result = await reg.call_tool("nobody", "dom_query", {}, timeout=0.1)
    assert result == {"error": "tab_unavailable"}


async def test_register_then_call_round_trip():
    reg = BridgeRegistry()
    client = await reg.register("s1")

    async def run_call():
        return await reg.call_tool("s1", "dom_query", {"selector": "#x"}, timeout=1.0)

    call_task = asyncio.create_task(run_call())
    # Backend sent a call message — consume it
    msg = await asyncio.wait_for(client.send.get(), timeout=1.0)
    assert msg["type"] == "call"
    assert msg["name"] == "dom_query"
    # Simulate the client sending back a result
    reg.ingest("s1", {"type": "result", "id": msg["id"], "result": {"matches": []}})
    result = await call_task
    assert result == {"matches": []}


async def test_call_timeout_returns_tab_timeout():
    reg = BridgeRegistry()
    await reg.register("s1")
    result = await reg.call_tool("s1", "dom_query", {"selector": "x"}, timeout=0.05)
    assert result == {"error": "tab_timeout"}


async def test_unregister_cancels_pending():
    reg = BridgeRegistry()
    await reg.register("s1")

    async def run_call():
        return await reg.call_tool("s1", "dom_query", {"selector": "x"}, timeout=2.0)

    task = asyncio.create_task(run_call())
    await asyncio.sleep(0.05)
    await reg.unregister("s1")
    result = await task
    assert result == {"error": "tab_disconnected"}


async def test_log_ingest_and_fetch():
    reg = BridgeRegistry()
    await reg.register("s1")
    reg.ingest("s1", {"type": "log", "entry": {"level": "error", "message": "boom"}})
    reg.ingest("s1", {"type": "log", "entry": {"level": "info", "message": "hello"}})
    logs = reg.get_logs("s1", limit=10)
    assert len(logs["logs"]) == 2
    only_errors = reg.get_logs("s1", level="error")
    assert len(only_errors["logs"]) == 1
    assert only_errors["logs"][0]["message"] == "boom"


async def test_register_replaces_previous_client():
    reg = BridgeRegistry()
    first = await reg.register("s1")

    async def make_call():
        return await reg.call_tool("s1", "dom_query", {"selector": "x"}, timeout=2.0)

    task = asyncio.create_task(make_call())
    await asyncio.sleep(0.05)
    # Drain the outgoing call so the pending future is captured on `first`
    await first.send.get()

    # Re-register: the old pending call should resolve with tab_replaced
    second = await reg.register("s1")
    result = await task
    assert result == {"error": "tab_replaced"}
    assert reg.get("s1") is second
