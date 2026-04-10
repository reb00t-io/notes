"""Notes — AI-native note-taking app.

Entry point. Wires the product agent, HTTP routes, client bridge WebSocket,
and serves the mobile-first frontend from templates/index.html.
"""
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from quart import Quart, g, jsonify, redirect, render_template, request, session, url_for

try:
    from .agent_runtime.notes_agent import build_notes_agent
    from .client_bridge.tools import BRIDGE_TOOL_SCHEMAS, current_session_id, handle_bridge_tool
    from .pages.routes import build_bridge_blueprint, build_pages_blueprint
    from .pages.seed import maybe_seed
    from .runtime_logs import configure_runtime_log_capture
    from .streaming import get_session_response, post_chat_response
    from .tool_executor import register_tool_handler
except ImportError:  # pragma: no cover
    from agent_runtime.notes_agent import build_notes_agent  # type: ignore
    from client_bridge.tools import BRIDGE_TOOL_SCHEMAS, current_session_id, handle_bridge_tool  # type: ignore
    from pages.routes import build_bridge_blueprint, build_pages_blueprint  # type: ignore
    from pages.seed import maybe_seed  # type: ignore
    from runtime_logs import configure_runtime_log_capture  # type: ignore
    from streaming import get_session_response, post_chat_response  # type: ignore
    from tool_executor import register_tool_handler  # type: ignore

app = Quart(__name__)
configure_runtime_log_capture()
logger = logging.getLogger(__name__)


# ─── Request logging (unchanged from bootstrap) ─────────────────────────────

REQUEST_LOG_PATH = Path(os.environ.get("REQUEST_LOG_PATH", "data/requests.log"))
REQUEST_LOG_BODY_LIMIT = int(os.environ.get("REQUEST_LOG_BODY_LIMIT", "20000"))
_request_log_lock = threading.Lock()


def _resolve_existing_path(*relative_paths: str) -> Path:
    roots = [Path.cwd(), Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent]
    for root in roots:
        for rel in relative_paths:
            candidate = root / rel
            if candidate.exists():
                return candidate
    return Path(relative_paths[0])


def _request_log_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate_request_log_text(text: str) -> tuple[str, bool]:
    if len(text) <= REQUEST_LOG_BODY_LIMIT:
        return text, False
    return text[:REQUEST_LOG_BODY_LIMIT], True


def _normalize_request_log_headers(headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in {"authorization", "cookie", "set-cookie"}:
            out[k] = "[redacted]"
        else:
            out[k] = v
    return out


def _stringify_request_log_body(body: object) -> str:
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        return body
    return str(body)


def _append_request_log(payload: dict[str, object]) -> None:
    try:
        REQUEST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False)
        with _request_log_lock:
            with REQUEST_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.write("\n")
    except Exception:
        logger.exception("failed to append request log")


def _log_response_chunk(request_id: str, chunk_index: int, chunk: bytes | str) -> None:
    text, truncated = _truncate_request_log_text(_stringify_request_log_body(chunk))
    _append_request_log(
        {
            "ts": _request_log_timestamp(),
            "event": "response_chunk",
            "request_id": request_id,
            "chunk_index": chunk_index,
            "body": text,
            "body_truncated": truncated,
        }
    )


def _is_sse_response(response) -> bool:
    return response.headers.get("Content-Type", "").startswith("text/event-stream")


class LoggedResponseBody:
    def __init__(self, body, request_id: str):
        self._body = body
        self._request_id = request_id
        self._entered = None
        self._chunk_index = 0

    async def __aenter__(self):
        if hasattr(self._body, "__aenter__"):
            self._entered = await self._body.__aenter__()
        else:
            self._entered = self._body
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _append_request_log(
            {
                "ts": _request_log_timestamp(),
                "event": "response_end",
                "request_id": self._request_id,
                "chunk_count": self._chunk_index,
            }
        )
        if hasattr(self._body, "__aexit__"):
            return await self._body.__aexit__(exc_type, exc, tb)
        return False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        body = self._entered if self._entered is not None else self._body
        async for chunk in body:
            _log_response_chunk(self._request_id, self._chunk_index, chunk)
            self._chunk_index += 1
            yield chunk


@app.before_request
async def log_client_request() -> None:
    request_id = os.urandom(8).hex()
    g.request_log_id = request_id
    body = _stringify_request_log_body(await request.get_data(cache=True, as_text=True))
    body, truncated = _truncate_request_log_text(body)
    _append_request_log(
        {
            "ts": _request_log_timestamp(),
            "event": "request",
            "request_id": request_id,
            "method": request.method,
            "path": request.path,
            "query_string": request.query_string.decode("utf-8", errors="replace"),
            "headers": _normalize_request_log_headers(request.headers),
            "body": body,
            "body_truncated": truncated,
        }
    )


@app.after_request
async def log_client_response(response):
    request_id = getattr(g, "request_log_id", os.urandom(8).hex())
    headers = _normalize_request_log_headers(response.headers)

    if _is_sse_response(response):
        _append_request_log(
            {
                "ts": _request_log_timestamp(),
                "event": "response_start",
                "request_id": request_id,
                "status_code": response.status_code,
                "headers": headers,
                "streamed": True,
            }
        )
        response.response = LoggedResponseBody(response.response, request_id)
        return response

    body = await response.get_data(as_text=True)
    body, truncated = _truncate_request_log_text(body)
    _append_request_log(
        {
            "ts": _request_log_timestamp(),
            "event": "response",
            "request_id": request_id,
            "status_code": response.status_code,
            "headers": headers,
            "body": body,
            "body_truncated": truncated,
            "streamed": False,
        }
    )
    return response


# ─── Config ─────────────────────────────────────────────────────────────────

VERSION_PATH = _resolve_existing_path("VERSION")
VERSION = VERSION_PATH.read_text().strip() if VERSION_PATH.exists() else "0.0.0"
DEPLOY_DATE = os.environ.get("DEPLOY_DATE", "unknown")

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-oss-120b")
STREAM_PACE_SECONDS = float(os.environ.get("STREAM_PACE_SECONDS", "0.003"))

API_KEY = os.environ.get("API_KEY", "")

AUTH_MODE = os.environ.get("AUTH_MODE", "none")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")
if AUTH_MODE == "password" and not AUTH_PASSWORD:
    raise RuntimeError("AUTH_PASSWORD must be set when AUTH_MODE=password")
if AUTH_MODE == "password":
    app.secret_key = hashlib.sha256(AUTH_PASSWORD.encode()).hexdigest()
else:
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-replace-me")


def _is_authenticated() -> bool:
    if AUTH_MODE == "none":
        return True
    if AUTH_MODE == "password":
        return session.get("authed") is True
    return False


# ─── Session store (simplified: no mode split) ─────────────────────────────

SESSIONS_PATH = Path(os.environ.get("SESSIONS_PATH", "data/sessions.json"))
sessions: dict[str, list[dict]] = {}
# Kept for streaming API compatibility; always holds "notes" now.
session_modes: dict[str, str] = {}
last_session_id: str | None = None


def _load_sessions() -> None:
    global last_session_id
    if SESSIONS_PATH.exists():
        try:
            data = json.loads(SESSIONS_PATH.read_text())
            if isinstance(data, dict) and "_meta" in data:
                sessions.update(data.get("sessions", {}))
                last_session_id = data["_meta"].get("last_session_id")
        except Exception:
            logger.exception("failed to load sessions")


def _save_sessions() -> None:
    SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_PATH.write_text(
        json.dumps(
            {
                "_meta": {"last_session_id": last_session_id},
                "sessions": sessions,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _on_session_start(session_id: str, _mode: str) -> None:
    global last_session_id
    last_session_id = session_id
    _save_sessions()


_load_sessions()


# ─── Build the product agent ────────────────────────────────────────────────

PAGES_DIR = Path(os.environ.get("PAGES_DIR", "pages")).resolve()

# If running under pytest we lazily build the agent per-test via fixtures,
# so skip the module-level build.
_building_under_pytest = os.environ.get("PYTEST_CURRENT_TEST") is not None

notes_agent = None
if not _building_under_pytest:
    try:
        notes_agent = build_notes_agent(pages_dir=PAGES_DIR)
        maybe_seed(notes_agent.store)
    except Exception:
        logger.exception("failed to build notes agent")

ALL_TOOL_SCHEMAS = []
if notes_agent is not None:
    ALL_TOOL_SCHEMAS = list(notes_agent.tools) + list(BRIDGE_TOOL_SCHEMAS)
    register_tool_handler(handle_bridge_tool)
    SYSTEM_PROMPT = notes_agent.system_prompt
else:
    SYSTEM_PROMPT = "You are the notes assistant. (Agent build failed; limited mode.)"


def _load_system_prompt(_mode: str) -> str:
    return SYSTEM_PROMPT


# ─── Routes ─────────────────────────────────────────────────────────────────


@app.route("/login", methods=["GET", "POST"])
async def login():
    if AUTH_MODE == "none":
        return redirect(url_for("index"))
    if _is_authenticated():
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        form = await request.form
        if form.get("password") == AUTH_PASSWORD:
            session["authed"] = True
            return redirect(url_for("index"))
        error = "Incorrect password."
    return await render_template("login.html", error=error)


@app.route("/logout")
async def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
async def index():
    if not _is_authenticated():
        return redirect(url_for("login"))
    # Mark the session as authorized for same-origin resource loads
    # (iframe page/data navigations that can't set the Bearer header).
    session["notes_authed"] = True
    return await render_template(
        "index.html",
        version=VERSION,
        deploy_date=DEPLOY_DATE,
        chat_api_key=API_KEY,
    )


_FAVICON_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    b'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    b'<stop offset="0" stop-color="#7c74ff"/><stop offset="1" stop-color="#b388ff"/>'
    b'</linearGradient></defs>'
    b'<rect width="32" height="32" rx="8" fill="url(#g)"/>'
    b'<path d="M16 6l2.6 6.8L26 14l-5.2 4.7L22.2 26 16 22.4 9.8 26l1.4-7.3L6 14l7.4-1.2L16 6z" '
    b'fill="#fff"/></svg>'
)


@app.route("/favicon.ico")
async def favicon():
    from quart import Response

    return Response(_FAVICON_SVG, content_type="image/svg+xml")


@app.route("/v1/sessions/latest", methods=["GET"])
async def get_latest_session():
    if API_KEY and request.headers.get("Authorization", "") != f"Bearer {API_KEY}":
        return jsonify({"error": "Unauthorized"}), 401
    if not last_session_id or last_session_id not in sessions:
        return jsonify({"session_id": None, "messages": []})
    msgs = [
        m for m in sessions[last_session_id]
        if m.get("role") in {"user", "assistant"} and m.get("content")
    ]
    return jsonify({"session_id": last_session_id, "messages": msgs})


@app.route("/v1/responses/<session_id>", methods=["GET"])
async def get_session(session_id: str):
    return await get_session_response(
        session_id=session_id,
        sessions=sessions,
        api_key=API_KEY,
        authorization=request.headers.get("Authorization", ""),
    )


@app.route("/v1/responses", methods=["POST"])
async def chat_responses():
    body = await request.get_json(force=True)
    # Set the bridge session context so dom_* tools know which tab to talk to
    session_id = body.get("session_id") or ""
    token = current_session_id.set(session_id)
    try:
        return await post_chat_response(
            body={**body, "mode": "notes"},
            sessions=sessions,
            session_modes=session_modes,
            api_key=API_KEY,
            authorization=request.headers.get("Authorization", ""),
            load_system_prompt=_load_system_prompt,
            save_sessions=_save_sessions,
            on_session_start=_on_session_start,
            tools=ALL_TOOL_SCHEMAS,
            client_factory=httpx.AsyncClient,
            llm_base_url=LLM_BASE_URL,
            llm_api_key=LLM_API_KEY,
            llm_model=LLM_MODEL,
            stream_pace_seconds=STREAM_PACE_SECONDS,
        )
    finally:
        current_session_id.reset(token)


# Register pages + bridge blueprints if the agent built successfully
if notes_agent is not None:
    app.register_blueprint(
        build_pages_blueprint(notes_agent.store, notes_agent.data_store, notes_agent.index)
    )
    app.register_blueprint(build_bridge_blueprint())


if __name__ == "__main__":
    import uvicorn

    logger.info("notes v%s (deployed %s)", VERSION, DEPLOY_DATE)
    port = int(os.environ["PORT"])
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
