"""Tests for src/main.py — the notes backend entry point.

Covers:
- /v1/responses streams a response and persists the session
- /v1/sessions/latest returns the most recent session
- Auth gating when API_KEY is set
- The system prompt is loaded from the notes agent
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest

# Required env BEFORE importing the app
os.environ.setdefault("LLM_BASE_URL", "http://fake-llm")
os.environ.setdefault("LLM_API_KEY", "test-llm-key")
os.environ.setdefault("NOTES_DISABLE_QDRANT", "1")
os.environ.setdefault("NOTES_EDITOR", "mock")
os.environ.pop("API_KEY", None)

# Use a scratch pages dir so tests don't touch the real one
import tempfile  # noqa: E402

_tmp_pages = tempfile.mkdtemp(prefix="notes_test_pages_")
os.environ["PAGES_DIR"] = _tmp_pages

import src.main as main_module  # noqa: E402
from src.main import app, sessions  # noqa: E402


# ─── helpers ────────────────────────────────────────────────────────────────


def _sse(*tokens: str) -> list[bytes]:
    chunks = [
        f'data: {json.dumps({"choices": [{"delta": {"content": t}}]})}\n\n'.encode()
        for t in tokens
    ]
    chunks.append(b"data: [DONE]\n\n")
    return chunks


def mock_llm(chunks: list[bytes] | None = None):
    if chunks is None:
        chunks = _sse("Hi", " there")

    async def aiter_raw():
        for chunk in chunks:
            yield chunk

    @asynccontextmanager
    async def _stream(*args, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.aiter_raw = aiter_raw
        yield resp

    @asynccontextmanager
    async def _client(*args, **kwargs):
        c = MagicMock()
        c.stream = _stream
        yield c

    return patch("src.main.httpx.AsyncClient", _client)


@pytest.fixture
def client():
    sessions.clear()
    main_module.last_session_id = None
    return app.test_client()


@pytest.fixture
def authed_client():
    """Client with API_KEY set.

    Sets both the module-level value (used by main's own /v1/responses route)
    and the env var (used by the pages blueprint's _is_authorized).
    """
    original = main_module.API_KEY
    original_env = os.environ.get("API_KEY")
    main_module.API_KEY = "secret"
    os.environ["API_KEY"] = "secret"
    sessions.clear()
    try:
        yield app.test_client()
    finally:
        main_module.API_KEY = original
        if original_env is None:
            os.environ.pop("API_KEY", None)
        else:
            os.environ["API_KEY"] = original_env


async def _read_sse(resp) -> str:
    body = b""
    async for chunk in resp.response:
        body += chunk
    return body.decode("utf-8")


# ─── tests ──────────────────────────────────────────────────────────────────


async def test_index_renders(client):
    resp = await client.get("/")
    assert resp.status_code == 200


async def test_responses_streams_reply(client):
    with mock_llm(_sse("hel", "lo ", "wor", "ld")):
        resp = await client.post("/v1/responses", json={"prompt": "hi"})
    assert resp.status_code == 200
    body = await _read_sse(resp)
    # Stream pacer splits into 3-char chunks but token boundaries here align
    assert "hel" in body
    assert "wor" in body
    assert "[DONE]" in body


async def test_responses_persists_session(client):
    with mock_llm(_sse("ok")):
        resp = await client.post("/v1/responses", json={"prompt": "hello"})
    await _read_sse(resp)
    sid = resp.headers.get("X-Session-Id")
    assert sid in sessions
    roles = [m["role"] for m in sessions[sid]]
    assert "system" in roles
    assert "user" in roles
    assert "assistant" in roles


async def test_sessions_latest_returns_last(client):
    with mock_llm(_sse("one")):
        r1 = await client.post("/v1/responses", json={"prompt": "hi"})
    await _read_sse(r1)

    latest = await (await client.get("/v1/sessions/latest")).get_json()
    assert latest["session_id"] == r1.headers.get("X-Session-Id")
    assert any(m["role"] == "user" and m["content"] == "hi" for m in latest["messages"])


async def test_session_replay_with_existing_id(client):
    with mock_llm(_sse("first")):
        r1 = await client.post("/v1/responses", json={"prompt": "a"})
    await _read_sse(r1)
    sid = r1.headers.get("X-Session-Id")

    with mock_llm(_sse("second")):
        r2 = await client.post("/v1/responses", json={"prompt": "b", "session_id": sid})
    await _read_sse(r2)
    assert r2.headers.get("X-Session-Id") == sid
    assert len(sessions[sid]) >= 4  # system, user a, assistant, user b, assistant


async def test_empty_prompt_returns_400(client):
    resp = await client.post("/v1/responses", json={})
    assert resp.status_code == 400


async def test_auth_required_when_api_key_set(authed_client):
    resp = await authed_client.post("/v1/responses", json={"prompt": "hi"})
    assert resp.status_code == 401


async def test_auth_accepted_with_bearer(authed_client):
    with mock_llm(_sse("ok")):
        resp = await authed_client.post(
            "/v1/responses",
            json={"prompt": "hi"},
            headers={"Authorization": "Bearer secret"},
        )
    assert resp.status_code == 200
    await _read_sse(resp)


async def test_load_system_prompt_is_notes_prompt():
    prompt = main_module._load_system_prompt("notes")
    assert "notes" in prompt.lower()
    assert "edit_page" in prompt or "create_page" in prompt


async def test_tool_schemas_include_page_tools():
    tool_names = {t["function"]["name"] for t in main_module.ALL_TOOL_SCHEMAS}
    assert "list_pages" in tool_names
    assert "read_page" in tool_names
    assert "edit_page" in tool_names
    assert "search" in tool_names
    assert "write_data" in tool_names
    assert "dom_query" in tool_names
    assert "get_client_logs" in tool_names
    # Old bootstrap tools should be gone
    assert "bash" not in tool_names
    assert "python" not in tool_names
    assert "web_search" not in tool_names


async def test_favicon_returns_svg(client):
    resp = await client.get("/favicon.ico")
    assert resp.status_code == 200
    assert resp.content_type.startswith("image/svg")
    body = await resp.get_data()
    assert b"<svg" in body


async def test_index_sets_session_cookie_allowing_iframe_loads(authed_client):
    """With API_KEY set, loading `/` should authorise subsequent same-origin
    navigations (iframe page loads) via the session cookie."""
    # First request without auth — blocked
    r = await authed_client.get("/v1/pages/welcome/raw")
    assert r.status_code == 401

    # Load the index with the bearer — this sets the session cookie.
    # (We pass the bearer here to simulate the browser having loaded the
    # SPA through whatever external auth flow injects it, and to prove the
    # session cookie is enough for subsequent iframe requests.)
    r = await authed_client.get("/", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200

    # Now the iframe request (no Authorization header) succeeds via cookie
    r = await authed_client.get("/v1/pages/welcome/raw")
    assert r.status_code == 200
    body = await r.get_data(as_text=True)
    assert "<title>Welcome</title>" in body
