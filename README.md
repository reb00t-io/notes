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

- **Backend:** Quart (async Python) — LLM proxy, agent runtime, page CRUD, search index, auth.
- **Frontend:** SvelteKit, mobile-first PWA. Chat is the primary surface; pages render alongside.
- **Storage:** HTML files in a `pages/` directory under git as the source of truth. Qdrant as the search index — hybrid BM25 + dense vectors with RRF fusion in a single query. Embeddings via Qwen3-embedding-4b on the same OpenAI-compatible endpoint as the chat model.
- **Agent:** A tool-using LLM with structural page-edit tools (`read_page`, `edit_section`, `create_page`, `search`, ...). Streaming responses; every edit is a git commit.

The current repo already contains a working Quart streaming chat with tool execution; this is the foundation we extend. See [`docs/spec.md`](docs/spec.md) §5 for the integration plan.

## Open source & hosting

- **Open source under MIT** to drive adoption. Self-hosting is a first-class path.
- **Managed hosting** is the commercial offering: hosted instance, sync, backups, mobile push, multi-device, no LLM key required.
- The open-source build and the hosted build run the same code; hosting adds operational glue, not features.

## Quick start (current scaffold)

```bash
direnv allow                    # creates venv, installs deps
python src/main.py              # → http://localhost:$PORT
```

Or with Docker:

```bash
./scripts/build.sh
docker compose up
```

The current `/` route still shows the bootstrap chat panel. The notes UI replaces it — see the spec for the migration plan.

## Documentation

- [`docs/spec.md`](docs/spec.md) — full product + technical specification
- [`AGENTS.md`](AGENTS.md) — instructions for AI agents working in this repo
- [`plan.md`](plan.md) — short-term improvement plan
