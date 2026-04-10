# Notes

> **An AI-native alternative to Notion.** No fixed structures, no block types, no databases. You describe what you want; an embedded agent grows your knowledge base as a single, persistent website.

**Status:** early / pre-alpha. The vision and architecture live in [`docs/spec.md`](docs/spec.md).

## The idea in one paragraph

Pages are plain HTML, persisted to disk and versioned with git. You don't write into a fixed editor — you talk to an agent that creates, extends, and restructures pages on your behalf. The agent reasons over your entire knowledge base on every edit, so the more you write, the better it understands your system. Direct editing exists as an escape hatch, but the default is *describe the change, the agent makes it*. Mobile-first, because most note-taking happens on a phone.

## How it differs from Notion

| | Notion | Notes |
|---|---|---|
| Primary input | Typing into structured blocks | Talking to an agent |
| Page model | Block tree of fixed types | Freeform HTML the agent maintains |
| Templates | First-class concept | Generated on demand |
| Databases | First-class concept | Emerge from the index over your pages |
| Storage | Proprietary cloud | HTML files + git, self-hostable |
| Customisation | Templates and integrations | "Make me a dashboard for X" — the agent builds it |

## Architecture (high level)

- **Backend:** Quart (async Python) — streaming LLM proxy, agent runtime, page CRUD, Qdrant-backed hybrid search, auth, client-bridge WebSocket.
- **Frontend:** framework-free vanilla ES modules, mobile-first. Chat is the primary surface; pages open in a sandboxed iframe sheet. No build step.
- **Storage:** HTML files in a `pages/` directory under git as the source of truth. Data files live in sibling `pages/<slug>.data/` directories. Qdrant is the search index — hybrid BM25 + dense vectors with RRF fusion in a single query. Embeddings via Qwen3-embedding-4b on the same OpenAI-compatible endpoint as the chat model.
- **Agent:** A single product agent. The orchestrator LLM handles conversation, retrieval, data management, and planning. All HTML edits are delegated to **Claude Code** (`claude` CLI subprocess), constrained to the target page file. Every edit is a git commit.
- **Client bridge:** WebSocket from browser to backend so agent tools can inspect the rendered DOM and read console logs on the user's open page.

See [`docs/spec.md`](docs/spec.md) for the full specification.

## Open source & hosting

- **Open source under MIT** to drive adoption. Self-hosting is a first-class path.
- **Managed hosting** is the commercial offering: hosted instance, sync, backups, mobile push, multi-device, no LLM key required.
- The open-source build and the hosted build run the same code; hosting adds operational glue, not features.

## Quick start

```bash
direnv allow                    # creates venv, installs deps

# Start Qdrant in the background (search index)
docker run -d --name notes-qdrant -p 6333:6333 qdrant/qdrant

# Environment: point at your OpenAI-compatible LLM endpoint
export LLM_BASE_URL=https://your-llm/v1
export LLM_API_KEY=sk-...

python src/main.py              # → http://localhost:$PORT
```

On first run the app seeds three starter pages (`welcome`, `getting-started`, `chart-example`) so there's something to look at.

Or with Docker (brings up Quart + Qdrant as a compose stack):

```bash
./scripts/build.sh
docker compose up
```

## Testing

```bash
pytest
```

105 tests covering parser, page store, data store, BM25, hybrid search, Claude Code editor (mocked), page tools, routes, client bridge, and the main HTTP surface.

## Documentation

- [`docs/spec.md`](docs/spec.md) — full product + technical specification
- [`AGENTS.md`](AGENTS.md) — instructions for AI agents working in this repo
