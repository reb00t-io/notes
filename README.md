# Notes

> **An AI-native alternative to Notion, Confluence, and Loop.** No block library, no templates, no fixed schemas. You describe what you want — a meeting doc, a project tracker, a wiki page, a dashboard, a decision log — and an embedded agent builds it as a real, persistent HTML page.

**Status:** early / pre-alpha. The vision and architecture live in [`docs/spec.md`](docs/spec.md).

## The idea in one paragraph

Your workspace is a collection of plain HTML pages, persisted to disk and versioned with git. You don't pick a block type or fill in a template — you talk to an agent that creates, extends, and restructures pages on your behalf. Pages can be anything: meeting notes, design docs, runbooks, project trackers, comparison tables, dashboards with inline charts, wikis, journals — there is no fixed schema. The agent reasons over the entire workspace on every edit, so the more you write, the more it understands. Direct editing exists as an escape hatch, but the default is *describe what you want, the agent builds it*. Mobile-first, because most capture happens on a phone.

## How it differs from Notion

| | Notion | Notes |
|---|---|---|
| Primary input | Typing into structured blocks | Talking to an agent |
| Page model | Block tree of fixed types | Freeform HTML the agent maintains |
| Templates | First-class concept | Generated on demand |
| Databases | First-class concept | Emerge from the index over your pages |
| Storage | Proprietary cloud | HTML files + git, self-hostable |
| Customisation | Templates and integrations | "Make me a dashboard for X" — the agent builds it |

## What you can build

A page is freeform HTML, so the agent can build any of these (and many more) on demand:

- meeting notes, decision logs, retros
- project trackers with status, owner, due date columns
- technical design docs, architecture pages, runbooks, postmortems
- team wikis, onboarding guides, reference pages
- reading lists, watch lists, learning trackers
- dashboards with inline charts that read attached CSV / JSON data files
- comparison tables, feature matrices, scorecards
- structured records (one page per customer / product / experiment)
- daily journals, gratitude lists, habit trackers
- any other structured artifact you can describe in a sentence

You don't pick a "type" before writing. You tell the agent what you want and it builds the right structure with HTML, CSS, and small inline scripts.

## Architecture (high level)

- **Backend:** Quart (async Python) — streaming LLM proxy, agent runtime, page CRUD, Qdrant-backed hybrid search, auth, client-bridge WebSocket.
- **Frontend:** framework-free vanilla ES modules, mobile-first. Chat is the primary surface; pages open in a sandboxed iframe sheet. No build step.
- **Storage:** HTML files in a `pages/` directory under git as the source of truth. Data files live in sibling `pages/<slug>.data/` directories. Qdrant is the search index — hybrid BM25 + dense vectors with RRF fusion in a single query. Embeddings via Qwen3-embedding-4b on the same OpenAI-compatible endpoint as the chat model.
- **Agent:** A single product agent. The orchestrator LLM handles conversation, retrieval, data management, and planning. HTML edits are delegated to a constrained editor — **Claude Code** (`claude` CLI subprocess) locally, or the same `LLM_BASE_URL` endpoint in production (`NOTES_EDITOR=llm`). Every edit is a git commit.
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
