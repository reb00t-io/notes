"""HTTP routes for pages + search + data files + client bridge WebSocket.

Exposed under /v1/*. Auth: if API_KEY is set in env, all routes require
`Authorization: Bearer <API_KEY>`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

from quart import Blueprint, Response, jsonify, request, session, websocket

try:
    from ..client_bridge.channel import get_registry
    from ..pages.data_store import DataStore, DataStoreError
    from ..pages.index import PageIndex
    from ..pages.search import search as hybrid_search
    from ..pages.store import PageStore, PageStoreError
except ImportError:  # pragma: no cover
    from client_bridge.channel import get_registry  # type: ignore
    from pages.data_store import DataStore, DataStoreError  # type: ignore
    from pages.index import PageIndex  # type: ignore
    from pages.search import search as hybrid_search  # type: ignore
    from pages.store import PageStore, PageStoreError  # type: ignore

logger = logging.getLogger(__name__)


def _api_key() -> str:
    return os.environ.get("API_KEY", "")


def _is_authorized() -> bool:
    key = _api_key()
    if not key:
        return True
    # Bearer header (for external API clients, JS fetches, etc.)
    if request.headers.get("Authorization", "") == f"Bearer {key}":
        return True
    # Same-origin session cookie (for iframe / <img> / direct navigations
    # that the browser makes without our fetch wrappers). The index route
    # sets session["notes_authed"] when the browser loads the SPA shell.
    try:
        if session.get("notes_authed") is True:
            return True
    except RuntimeError:
        # Outside a request context (tests that call helpers directly)
        pass
    return False


def _unauthorized() -> tuple[Response, int]:
    return jsonify({"error": "Unauthorized"}), 401


def build_pages_blueprint(
    store: PageStore,
    data_store: DataStore,
    index: PageIndex | None,
) -> Blueprint:
    bp = Blueprint("pages", __name__, url_prefix="/v1")

    # ── page CRUD ─────────────────────────────────────────────────────

    @bp.route("/pages", methods=["GET"])
    async def list_pages() -> Response:
        if not _is_authorized():
            return _unauthorized()
        return jsonify({"pages": store.list_pages()})

    @bp.route("/pages/<page_id>", methods=["GET"])
    async def get_page(page_id: str) -> Response:
        if not _is_authorized():
            return _unauthorized()
        try:
            rec = store.read(page_id)
        except PageStoreError as exc:
            return jsonify({"error": str(exc)}), 404
        try:
            files = [df.__dict__ for df in data_store.list(page_id)]
        except Exception:  # pragma: no cover
            files = []
        return jsonify(
            {
                "id": rec.id,
                "title": rec.title,
                "tags": rec.tags,
                "created": rec.created,
                "updated": rec.updated,
                "html": rec.path.read_text(encoding="utf-8"),
                "sections": rec.parsed.section_index(),
                "data_files": files,
            }
        )

    @bp.route("/pages/<page_id>/raw", methods=["GET"])
    async def get_page_raw(page_id: str) -> Response:
        if not _is_authorized():
            return _unauthorized()
        try:
            rec = store.read(page_id)
        except PageStoreError as exc:
            return Response(str(exc), status=404, content_type="text/plain")
        html = rec.path.read_text(encoding="utf-8")
        # Inject the shared page stylesheet so pages render with app-matched
        # typography and theme inside the iframe. Storing pages as plain HTML
        # keeps them portable; the styling is applied at serve time.
        stylesheet = '<link rel="stylesheet" href="/static/notes/page.css">'
        if "</head>" in html and stylesheet not in html:
            html = html.replace("</head>", f"    {stylesheet}\n  </head>", 1)
        return Response(html, content_type="text/html; charset=utf-8")

    @bp.route("/pages/<page_id>", methods=["DELETE"])
    async def delete_page(page_id: str) -> Response:
        if not _is_authorized():
            return _unauthorized()
        try:
            store.delete(page_id)
        except PageStoreError as exc:
            return jsonify({"error": str(exc)}), 404
        if index is not None:
            index.delete_page(page_id)
        return jsonify({"ok": True, "page_id": page_id})

    # ── data files ────────────────────────────────────────────────────

    @bp.route("/pages/<page_id>/data", methods=["GET"])
    async def list_data(page_id: str) -> Response:
        if not _is_authorized():
            return _unauthorized()
        try:
            return jsonify({"files": [df.__dict__ for df in data_store.list(page_id)]})
        except PageStoreError as exc:
            return jsonify({"error": str(exc)}), 404

    @bp.route("/pages/<page_id>/data/<path:name>", methods=["GET"])
    async def get_data(page_id: str, name: str) -> Response:
        # Same-origin fetches from page HTML must work without auth headers
        # in the browser, so this route honors auth only if API_KEY is set
        # AND the caller has no session. For simplicity in v1 we keep auth
        # as elsewhere.
        if not _is_authorized():
            return _unauthorized()
        try:
            raw = data_store.read_bytes(page_id, name)
        except (PageStoreError, DataStoreError) as exc:
            return Response(str(exc), status=404, content_type="text/plain")
        mime, _ = mimetypes.guess_type(name)
        return Response(raw, content_type=mime or "application/octet-stream")

    @bp.route("/pages/<page_id>/data/<path:name>", methods=["PUT"])
    async def put_data(page_id: str, name: str) -> Response:
        if not _is_authorized():
            return _unauthorized()
        body = await request.get_data()
        try:
            info = data_store.write(page_id, name, body)
        except (PageStoreError, DataStoreError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "file": info.__dict__})

    @bp.route("/pages/<page_id>/data/<path:name>", methods=["DELETE"])
    async def del_data(page_id: str, name: str) -> Response:
        if not _is_authorized():
            return _unauthorized()
        try:
            data_store.delete(page_id, name)
        except (PageStoreError, DataStoreError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True})

    # ── search ────────────────────────────────────────────────────────

    @bp.route("/search", methods=["GET"])
    async def search_route() -> Response:
        if not _is_authorized():
            return _unauthorized()
        query = (request.args.get("q") or "").strip()
        if not query:
            return jsonify({"query": "", "results": [], "total": 0})
        if index is None or index.client is None:
            return jsonify({
                "query": query,
                "results": [],
                "total": 0,
                "error": "search index not available",
            })
        limit = int(request.args.get("limit") or 8)
        result = await hybrid_search(
            client=index.client,
            bm25=index.bm25,
            query=query,
            limit=limit,
            collection=index.collection,
        )
        return jsonify(result)

    # ── recent commits ────────────────────────────────────────────────

    @bp.route("/commits", methods=["GET"])
    async def commits_route() -> Response:
        if not _is_authorized():
            return _unauthorized()
        limit = int(request.args.get("limit") or 20)
        return jsonify({"commits": store.recent_commits(limit=limit)})

    return bp


def build_bridge_blueprint() -> Blueprint:
    bp = Blueprint("bridge", __name__, url_prefix="/v1")

    @bp.websocket("/bridge")
    async def bridge_ws() -> None:
        session_id = websocket.args.get("session_id") or ""
        if not session_id:
            await websocket.close(1008)
            return
        registry = get_registry()
        client = await registry.register(session_id)
        logger.info("bridge connected: %s", session_id)

        async def pump_outbound() -> None:
            try:
                while True:
                    msg = await client.send.get()
                    await websocket.send(json.dumps(msg))
            except asyncio.CancelledError:
                raise

        pumper = asyncio.create_task(pump_outbound())
        try:
            while True:
                raw = await websocket.receive()
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                registry.ingest(session_id, msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.info("bridge disconnected: %s", session_id)
        finally:
            pumper.cancel()
            await registry.unregister(session_id)

    return bp
