"""Tests for src/pages/routes.py (HTTP + WebSocket routes)."""
from __future__ import annotations

import pytest
from quart import Quart

from src.pages.data_store import DataStore
from src.pages.routes import build_bridge_blueprint, build_pages_blueprint
from src.pages.store import PageStore


@pytest.fixture
def test_app(tmp_pages):
    store = PageStore(pages_dir=tmp_pages)
    data_store = DataStore(store)
    store.create(title="Welcome", body_html="<h1>hi</h1><p>ok</p>", tags=["welcome"])

    app = Quart(__name__)
    app.register_blueprint(build_pages_blueprint(store, data_store, None))
    app.register_blueprint(build_bridge_blueprint())
    return app, store, data_store


async def test_list_pages_route(test_app):
    app, *_ = test_app
    client = app.test_client()
    resp = await client.get("/v1/pages")
    assert resp.status_code == 200
    data = await resp.get_json()
    assert any(p["id"] == "welcome" for p in data["pages"])


async def test_get_page_route(test_app):
    app, *_ = test_app
    client = app.test_client()
    resp = await client.get("/v1/pages/welcome")
    assert resp.status_code == 200
    data = await resp.get_json()
    assert data["id"] == "welcome"
    assert "<title>Welcome</title>" in data["html"]


async def test_get_unknown_page(test_app):
    app, *_ = test_app
    client = app.test_client()
    resp = await client.get("/v1/pages/does-not-exist")
    assert resp.status_code == 404


async def test_raw_page_returns_html(test_app):
    app, *_ = test_app
    client = app.test_client()
    resp = await client.get("/v1/pages/welcome/raw")
    assert resp.status_code == 200
    assert resp.content_type.startswith("text/html")
    body = await resp.get_data(as_text=True)
    assert "<title>Welcome</title>" in body


async def test_raw_page_injects_stylesheet(test_app):
    app, *_ = test_app
    client = app.test_client()
    resp = await client.get("/v1/pages/welcome/raw")
    body = await resp.get_data(as_text=True)
    # The shared page stylesheet should be injected before </head>
    assert '<link rel="stylesheet" href="/static/notes/page.css">' in body
    head_end = body.index("</head>")
    link_pos = body.index("page.css")
    assert link_pos < head_end


async def test_raw_page_does_not_double_inject_stylesheet(test_app):
    app, store, _ = test_app
    client = app.test_client()
    # First fetch injects
    r1 = await client.get("/v1/pages/welcome/raw")
    b1 = await r1.get_data(as_text=True)
    assert b1.count("page.css") == 1
    # File on disk should NOT have been modified
    disk = store.read("welcome").path.read_text()
    assert "page.css" not in disk


async def test_put_and_get_data(test_app):
    app, *_ = test_app
    client = app.test_client()
    resp = await client.put(
        "/v1/pages/welcome/data/sales.csv",
        data="q,v\nQ1,100\n",
        headers={"Content-Type": "text/csv"},
    )
    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["ok"] is True
    resp2 = await client.get("/v1/pages/welcome/data/sales.csv")
    assert resp2.status_code == 200
    assert (await resp2.get_data(as_text=True)).startswith("q,v")


async def test_delete_data(test_app):
    app, *_ = test_app
    client = app.test_client()
    await client.put("/v1/pages/welcome/data/a.csv", data="a\n")
    resp = await client.delete("/v1/pages/welcome/data/a.csv")
    assert resp.status_code == 200


async def test_search_without_index_returns_empty(test_app):
    app, *_ = test_app
    client = app.test_client()
    resp = await client.get("/v1/search?q=hi")
    assert resp.status_code == 200
    data = await resp.get_json()
    assert data["total"] == 0
    assert data.get("error") == "search index not available"


async def test_delete_page_route(test_app):
    app, store, _ = test_app
    client = app.test_client()
    resp = await client.delete("/v1/pages/welcome")
    assert resp.status_code == 200
    assert not store.exists("welcome")


async def test_commits_route(test_app):
    app, *_ = test_app
    client = app.test_client()
    resp = await client.get("/v1/commits")
    assert resp.status_code == 200
    data = await resp.get_json()
    assert len(data["commits"]) >= 1


async def test_bridge_blueprint_websocket_requires_session_id(test_app):
    app, *_ = test_app
    client = app.test_client()
    async with client.websocket("/v1/bridge") as ws:
        # Should close with no session_id — the server closes, so receive raises
        with pytest.raises(Exception):
            await ws.receive()
