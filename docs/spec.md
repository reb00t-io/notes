# Notes — Specification

> Working name: **Notes**. Open source (MIT). Mobile-first. AI-native.

This document is the source of truth for the product vision, architecture, and near-term roadmap. It is intentionally opinionated; ambiguity here costs more than wrong calls we can revisit.

---

## 1. Vision

A note-taking and knowledge-management app where the primary interaction is **conversation with an embedded agent**, and the substrate is **persisted HTML** that grows over time like a personal website.

### 1.1 Core beliefs

1. **Structure is friction.** Notion's templates, databases, and block types are powerful but slow. The cost of "set up the right structure" is paid by every user, every time.
2. **HTML is the right substrate.** Universal, styleable, LLMs are excellent at generating it, browsers render it for free, it diffs in git.
3. **The agent should know everything you've written.** Retrieval over your full corpus on every interaction is now cheap enough to be the default.
4. **Talking is faster than typing — eventually.** Direct editing remains the escape hatch, but the default path is "describe the change."
5. **Mobile is where notes happen.** Desktop is secondary.

### 1.2 Non-goals (for v1)

- Real-time multi-user collaboration (single-user, multi-device sync only)
- A block-based WYSIWYG editor competing with Notion on its own terms
- Plugin marketplace, integrations directory, "no-code" app builder
- Replacing IDEs, project management, or spreadsheet apps

### 1.3 What "done" looks like for v1

A user can, on their phone:
- Open the app, see a list of recent pages
- Say "add to today's standup that I unblocked the deploy" → the right page is found or created and the entry appears
- Ask "what did I learn last week about postgres locks?" → get a synthesized answer with links into the source pages
- Ask "make me a dashboard from my last 10 standup notes" → a new page is generated and persisted
- Edit a page directly when faster than describing the change

---

## 2. Core concepts

### 2.1 Page

A **page** is a single HTML document. Pages are stored as files in `pages/<slug>.html`. Every page has:

- A stable `id` (the slug, kebab-case)
- A `<title>` and minimal frontmatter-equivalent in a `<meta>` tag (`created`, `updated`, `tags`)
- A body of arbitrary HTML the agent maintains
- A consistent set of structural anchors the agent uses for editing (see §4.2)

Pages are the only first-class *content* type. There are no block types and no templates as separate objects — "templates" are just instructions the agent has memorised. There is one database (Qdrant) but it stores a derived **search index** over the pages, not the pages themselves; pages on disk remain the source of truth (see §4.4–§4.5).

### 2.2 Edit

An **edit** is a structured change to one or more pages, authored by the agent in response to a user instruction. Edits are:

- **Atomic** — one user instruction → one git commit
- **Reversible** — `git revert` is the undo button
- **Auditable** — the commit message is the user instruction verbatim, the commit body is the agent's reasoning summary

The agent does not regenerate pages from scratch on every edit. It reads the page, plans a structural change, and emits a minimal patch. See §4.2.

### 2.3 The corpus

The user's full set of pages is the **corpus**. Every user instruction is answered with the corpus in mind, via a retrieval layer (§4.5). The corpus is the user's database; the agent is the query engine.

### 2.4 The agent

There is **exactly one agent**. It is neither a "user-mode helper" nor a full "dev-mode" code editor — it sits in the middle: a streaming, tool-using LLM whose job is to do *page-level dev work* on the user's behalf. Its scope is the **content layer**, not the core system.

**What the agent CAN do:**
- Read, create, edit, search, and restructure pages (via the structural tools in §4.2)
- Inspect and manipulate the **rendered DOM** of the current page in the user's browser, so it can verify a layout, fix a broken element, or apply a tweak that's easier to express visually than as an HTML edit
- Read **client-side logs** (console errors, network failures, runtime exceptions on the rendered page) so it can debug what the user is seeing
- Run small bits of JavaScript in the page context to introspect or transform the DOM
- Reload, navigate, and observe the result of its own edits before responding

**What the agent CANNOT do:**
- Modify the core system: backend code, the SvelteKit frontend, the agent's own tools, the build, the deploy pipeline, dependencies, configuration. Anything under `src/`, `frontend/`, `Dockerfile`, `pyproject.toml`, etc. is off-limits.
- Run shell commands, install packages, or touch the filesystem outside `pages/`
- Edit files unrelated to the user's notes

**Why one agent, not two:** the existing scaffold has separate `user` and `dev` modes. For this product that split is wrong. Pure user mode is too weak (it can't actually change anything), full dev mode is too dangerous (it can break the app). The right shape is a single agent that owns the content layer end-to-end, with the same kind of tools a developer would have — but scoped to pages, not code.

The agent is always single-turn from the user's perspective: one instruction, one response, one set of edits.

Self-modification of the core system is still possible via the existing self-improvement agent in `src/agents/`, but that is a **separate process** invoked by the maintainer, not by end users. See §4.7.

---

## 3. User experience

### 3.1 Surfaces

The app has exactly **two primary surfaces**, plus auxiliary screens:

1. **Chat** — a full-screen conversation with the agent. The default landing surface on mobile.
2. **Page view** — a rendered HTML page, scrollable. Reachable from chat results, page list, or direct link.

Auxiliary: page list (chronological / search), settings, login.

There is no separate "editor" surface in v1. Direct editing happens inline on the page view via `contenteditable`, with changes flushed to disk on blur.

### 3.2 Mobile-first interaction model

- **Bottom-anchored input.** The chat input lives at the bottom of the screen, always reachable by thumb.
- **Voice input is a primary affordance**, not a hidden feature. A mic button next to the send button.
- **Page results render inline in the chat as cards** with a tap-to-open affordance.
- **Sheet transitions, not page navigations.** Opening a page slides up over chat; closing returns you to the same scroll position in chat.
- **Offline-tolerant.** Pages are cached locally; edits queue when offline and replay on reconnect.

### 3.3 Canonical flows

**Capture**
1. User opens app → chat
2. Says "remind me that the auth migration is blocked on legal review"
3. Agent decides where this belongs (existing project page, new page, or daily log) and shows the change as a card with "view page" / "undo"

**Recall**
1. User asks "what's the status of the auth migration?"
2. Agent searches the corpus, returns a synthesized answer with inline links to source pages
3. Tap a link → page view opens as a sheet

**Restructure**
1. User says "the auth project page is getting messy, split out the legal review into its own page"
2. Agent plans the split, shows a preview of both pages, asks for confirmation
3. On confirm: two commits (extract, then update links)

**Generate a view**
1. User says "show me everything I've decided about postgres this quarter"
2. Agent generates a new page summarising relevant content with backlinks
3. The page is persisted (so it can be revisited and updated) but marked as "derived"

### 3.4 Direct editing

- Tap any element on a page → it becomes editable in place
- Plain text only by default; richer edits go through the agent
- On blur: the change is committed with `direct edit` as the commit author
- The agent never overwrites a direct edit without explicit instruction; direct edits are anchors the agent must preserve

---

## 4. Architecture

### 4.1 Component overview

```
┌─────────────────────────────────────────────────────────────┐
│  SvelteKit PWA (frontend/)                                  │
│  - Chat surface, page view, page list                       │
│  - Service worker: offline cache, edit queue                │
│  - SSE client for streaming agent responses                 │
│  - WebSocket "client bridge": exposes DOM + console logs    │
│    of the open page to the backend so the agent can         │
│    inspect/patch what the user is actually seeing           │
└──────────┬──────────────────────────────────┬───────────────┘
           │ HTTPS / SSE                      │ WebSocket
           │ (chat, CRUD, search)             │ (DOM tools, client logs)
┌──────────▼──────────────────────────────────▼───────────────┐
│  Quart backend (src/)                                       │
│  - /v1/responses (existing) — agent chat, streaming         │
│  - /v1/pages — CRUD                                         │
│  - /v1/search — semantic + keyword                          │
│  - /v1/sync — pull/push for offline edits                   │
│  - /v1/bridge — WebSocket: routes DOM/log tool calls        │
│    from the agent to the user's open browser tab            │
│  - Auth (none / password / auth0)                           │
└────┬──────────────────┬────────────────────┬────────────────┘
     │                  │                    │
┌────▼──────┐   ┌───────▼────────┐   ┌───────▼─────────┐
│ pages/    │   │ Qdrant         │   │ LLM provider    │
│ (HTML +   │   │ (BM25 sparse + │   │ chat + embed    │
│  git,     │   │  dense vectors,│   │ (OpenAI-compat, │
│  source   │   │  hybrid via    │   │  Qwen3-embed-4b)│
│  of truth)│   │  RRF fusion)   │   │                 │
└───────────┘   └────────────────┘   └─────────────────┘
```

### 4.2 The agent's edit model

This is the hardest part of the system and deserves the most care.

**Principle:** the agent operates on a *semantic tree*, not raw HTML. Pages are parsed into a tree of sections, each with a stable `data-section-id`. The agent's edit tools take section IDs as targets.

**Tool surface (initial):**

| Tool | Purpose |
|---|---|
| `list_pages(query?, limit?)` | Browse / filter pages by title or tag |
| `read_page(id)` | Return page HTML + section index |
| `search(query, k?)` | Hybrid keyword + semantic search across the corpus, returns snippets |
| `create_page(title, html, tags?)` | New page; agent provides initial HTML |
| `edit_section(page_id, section_id, action, content)` | `action ∈ {replace, append_after, prepend_before, delete}` |
| `add_section(page_id, after_section_id?, html)` | Insert a new top-level section |
| `rename_section(page_id, section_id, new_heading)` | Cosmetic restructure |
| `link_pages(from_id, to_id, anchor_text)` | Create a backlink-aware reference |
| `get_recent_edits(limit?)` | Audit / context for follow-up instructions |
| `dom_query(selector)` | Read elements from the rendered page in the user's browser (text, attrs, computed style) |
| `dom_eval(js)` | Run a small JS snippet in the current page context, return the JSON-serialisable result |
| `dom_patch(selector, action, value)` | Apply a transient DOM change (`set_text`, `set_attr`, `add_class`, ...) so the agent can preview an effect before persisting it as an `edit_section` |
| `get_client_logs(limit?, level?)` | Recent console messages, network failures, and runtime exceptions from the rendered page |
| `reload_page()` | Force the client to re-render after an edit, so the agent can verify the result |

**Why structural tools instead of "regenerate this page":**
- Naive regeneration loses fidelity, formatting, and direct edits
- Structural edits diff cleanly in git
- The agent's failure modes become localised (a bad section, not a destroyed page)

**Anchors and conventions:**
- Every `<section>` has a `data-section-id` (short hash, stable across edits)
- Direct edits get `data-direct-edit="true"` so the agent treats them as immutable unless instructed
- Generated/derived sections get `data-derived="true"` so they can be regenerated freely

**Failure handling:**
- All edits go through a validate step (HTML well-formed, section IDs exist, no orphan links)
- Failed edits are rolled back before the response is shown to the user
- The user sees the planned edit as a preview *before* commit, with confirm/cancel for non-trivial changes

### 4.3 Client-side capabilities (DOM access & logs)

The agent's `dom_*` and `get_client_logs` tools cross the network boundary: they execute against the user's *open browser tab*, not on the server. This is what makes the agent a real "page-level dev" — it can see what the user sees, debug what the user is hitting, and verify its own edits before responding.

**Mechanism:**
- The frontend opens a long-lived WebSocket (or SSE + POST channel) to the backend on page load and registers itself with the current session
- When the agent calls a `dom_*` or `get_client_logs` tool, the backend forwards the request over that channel, awaits a response, and returns it as the tool result
- The frontend captures `console.*`, `window.onerror`, unhandled rejections, and failed `fetch`/XHR into a bounded ring buffer that `get_client_logs` reads from

**Why route through the backend instead of running tools in the browser directly:**
- The agent runtime stays on the server (one place to reason about, one place to log)
- Tool calls and their results land in the same request log and audit trail as edits
- The same channel handles `reload_page`, future toast notifications, "agent is editing this section now" indicators, etc.

**Scope rules:**
- DOM tools can read and patch only the *currently open* page in the browser tab the user is interacting with — never another tab, never the chat surface itself
- `dom_eval` runs in a sandboxed context: no access to the user's auth token, cookies, or `localStorage` keys outside `pages.*`
- `dom_patch` changes are **transient by default**. They are visible in the user's browser but not persisted. The agent must follow up with an `edit_section` to make the change durable. This separation keeps "preview an effect" cleanly distinct from "commit an edit."
- Client logs are local to the browser session and never leave the user's device except as tool results in the agent's context

**Failure modes to plan for:**
- Browser tab is closed: tool calls return `tab_unavailable`; the agent falls back to server-only tools
- User navigates away mid-tool-call: pending calls cancel; the agent re-reads the page from disk
- Conflicting transient `dom_patch` and a real direct edit: direct edit wins, transient state is discarded on next render

### 4.4 Storage layout

```
pages/
  index.html              # generated home page (recent + pinned)
  2026-04-10-standup.html
  auth-migration.html
  postgres-notes.html
  ...
data/
  bm25_vocab.json         # BM25 IDF/vocab state, fit incrementally
  sessions.json           # existing chat session store
qdrant/                   # Qdrant storage volume (when running locally)
  ...
```

- `pages/` is a git repository and is the **source of truth**. It can live inside the app data dir or as a sibling repo — configurable.
- Qdrant runs as a separate process (Docker container next to Quart). Its collection is **derived** from `pages/` and can be rebuilt at any time by re-indexing — losing the Qdrant volume is not data loss.
- `data/bm25_vocab.json` holds the incrementally-fit BM25 vocabulary and document frequencies. This is a small JSON file, regenerable from `pages/` but cheap to keep.
- A file watcher on `pages/` upserts changed sections into Qdrant on save, so external edits (git pull, text editor) are picked up automatically.

### 4.5 Retrieval & search

**Backend:** Qdrant. Recent versions support hybrid search natively by storing **sparse BM25 vectors** alongside **dense embedding vectors** in the same collection, then fusing the two result sets with **Reciprocal Rank Fusion (RRF)** in a single query. The repo at `/Users/marko/dev_p/gmail/src/search/` is a working reference implementation we copy from — collection setup, indexer, and the hybrid query path are all directly applicable.

**Vectors per point:**
- `dense` — Qwen3 embedding (1024-dim, cosine distance) — see §4.8 for the embedding client
- `bm25` — sparse vector with `Modifier.IDF`, scored server-side; the encoder runs client-side and fits the vocabulary incrementally as new pages are indexed

**Granularity:** sections, not pages. Each `<section>` (with its stable `data-section-id`) becomes one Qdrant point. A query for "postgres locks" should return the paragraph about postgres locks, not the 3000-line ops log it lives in.

**Payload schema (per point):**

| Field | Purpose |
|---|---|
| `page_id` | Slug of the parent page |
| `section_id` | Stable section anchor |
| `page_title` | For display in result cards |
| `heading` | Nearest enclosing heading text (h1–h4) |
| `text` | Plain-text content of the section (also used to generate the snippet) |
| `tags` | Tags inherited from the page meta |
| `updated` | ISO timestamp, used for date filters and recency boosts |

Payload indices on `page_id` (keyword) and `tags` (keyword) so the agent can scope a search to a single page or tag set.

**Indexing flow (on every page save):**
1. Parse the page → list of sections with stable IDs
2. Diff against the previous version to find changed/new/removed sections
3. For removed: delete the corresponding points from Qdrant
4. For changed/new: embed the section text via the LLM provider's `/embeddings` endpoint, encode the BM25 sparse vector locally, upsert into Qdrant
5. Persist updated BM25 vocab to `data/bm25_vocab.json`

Embeddings are not separately cached: Qdrant *is* the cache, and unchanged sections aren't re-embedded because they aren't re-upserted.

**Query flow:**
1. Embed the query with the same model
2. Encode the query with the BM25 encoder (sparse indices + values)
3. Issue a single Qdrant `query_points` with two `Prefetch` blocks (one sparse, one dense) and `FusionQuery(fusion=Fusion.RRF)`
4. Apply the score-thresholding heuristic from the gmail reference (`MIN_RESULTS`, relative-score floor, gap detection) so the agent gets a tight result set, not a noisy long tail
5. Generate per-result snippets by picking the highest-overlap sentences with the query terms

**Why Qdrant, not SQLite FTS5 / pgvector / others:**
- Native hybrid in one query, with proper RRF fusion — no client-side merge logic to maintain
- BM25 with `Modifier.IDF` is a real BM25, not "FTS5 ranks happen to look similar"
- Filter conditions (page_id, tags, date) compose cleanly with the hybrid search
- Single binary, runs as a Docker sidecar, zero ops for self-hosters
- Same stack as the gmail project — code reuse, one less thing to learn

**Other databases (future):** if we add user accounts, billing, or audit logs for the hosted offering, those go in a separate relational store (Postgres). Qdrant stays focused on search.

### 4.6 Frontend: SvelteKit

**Why SvelteKit:**
- Smallest-runtime mainstream framework → best fit for mobile-first PWA
- File-based routing, SSR optional, easy to deploy as static + API
- Stores model fits a chat-driven app well (single source of truth for messages, pages, edit queue)
- The user is comfortable with it / wants it

**Layout:**

```
frontend/
  src/
    routes/
      +layout.svelte         # app shell, bottom nav
      +page.svelte           # chat (default surface)
      pages/
        +page.svelte         # page list
        [id]/+page.svelte    # page view + inline edit
      settings/+page.svelte
    lib/
      api.ts                 # fetch wrappers for /v1/*
      sse.ts                 # SSE client for streaming chat
      stores/
        chat.ts
        pages.ts
        sync.ts              # offline edit queue
      components/
        ChatInput.svelte
        ChatMessage.svelte
        PageCard.svelte
        EditPreview.svelte
    service-worker.ts        # offline cache + queue
  static/
  svelte.config.js
  vite.config.ts
```

**Build & serve:**
- Dev: SvelteKit dev server on `:5173`, proxies `/v1/*` to Quart on `$PORT`
- Prod: `vite build` outputs static assets; Quart serves them from `frontend/build/` and the API from `/v1/*` on the same origin
- The existing `Dockerfile` extends with a node build stage that produces the static bundle

### 4.7 Backend modules

The existing `src/main.py` already provides streaming `/v1/responses` with tool execution, session persistence, auth (none / password), and request logging. We keep that infrastructure and add the product-specific modules around it.

The existing scaffold ships two modes (`user` / `dev`) — for the product, these collapse into a **single agent** (see §2.4). No mode switcher in the product UI.

The maintainer-facing self-improvement agent in `src/agents/` is unrelated to the product agent and lives separately.

**Modules:**

| Module | Purpose |
|---|---|
| `src/pages/store.py` | Page CRUD on disk + git commit on every write |
| `src/pages/parser.py` | HTML → section tree, section ID assignment, validation |
| `src/pages/index.py` | Qdrant indexer: parse → diff → embed → upsert; watches `pages/` for external edits. Pattern lifted from `/Users/marko/dev_p/gmail/src/search/indexer.py`. |
| `src/pages/search.py` | Hybrid query (sparse BM25 + dense Qwen3 + RRF), score thresholding, snippet generation. Pattern lifted from `/Users/marko/dev_p/gmail/src/search/search.py`. |
| `src/pages/bm25.py` | Incremental BM25 vocabulary / IDF encoder. Copied from `/Users/marko/dev_p/gmail/src/search/bm25.py`. |
| `src/pages/embeddings.py` | OpenAI-compatible `/embeddings` client targeting `LLM_BASE_URL` with the Qwen3-embedding-4b model. Copied from the gmail reference. |
| `src/pages/tools.py` | Page tool implementations (`read_page`, `edit_section`, `search`, ...) |
| `src/pages/routes.py` | `/v1/pages`, `/v1/search`, `/v1/sync` HTTP endpoints |
| `src/client_bridge/channel.py` | WebSocket session registry: backend ↔ open browser tab, used by the DOM/log tools |
| `src/client_bridge/tools.py` | DOM tools (`dom_query`, `dom_eval`, `dom_patch`, `reload_page`) and `get_client_logs`, all routed through the channel |
| `src/agent_runtime/notes_agent.py` | The single product agent: registers page tools + client-bridge tools, owns the system prompt |

**New runtime dependency:** Qdrant. Runs as a Docker sidecar in both dev and prod (added to `docker-compose.yml`). The Python side adds `qdrant-client` to `pyproject.toml`.

**Removed from the existing scaffold:**
- `src/templates/index.html` and `src/static/chat/` — replaced by the SvelteKit frontend
- The `user` / `dev` mode split: `tool_schemas.get_tools_for_mode`, the `DEFAULT_MODE`/`USER_MODE`/`DEV_MODE` constants, mode-keyed system prompts, and the per-session `mode` field
- `BASH_TOOL`, `PYTHON_TOOL`, `WEB_SEARCH_TOOL`, `FETCH_URL_TOOL`, `GET_LOGS_TOOL` — none of them belong in the product agent. Its reach is pages + DOM, nothing else.

**Kept unchanged:**
- Streaming infrastructure (`src/streaming.py`)
- Tool executor (`src/tool_executor.py`) — page tools and DOM tools register the same way
- Session store, auth, request logging
- Docker, CI/CD, deploy scripts (extended, not replaced)

### 4.8 LLM and embeddings

**One provider, two endpoints, one base URL.** Both chat and embeddings hit the same OpenAI-compatible `LLM_BASE_URL` already configured for the agent. No second provider, no second API key.

- **Agent runtime:** existing `LLM_BASE_URL` + `LLM_MODEL` (currently `gpt-oss-120b`, swappable). For the hosted offering, the default chat model is TBD — likely Claude Sonnet/Opus for tool-use reliability.
- **Embeddings:** [`qwen3-embedding-4b`](https://docs.privatemode.ai/models/qwen3-embedding-4b/) via `POST {LLM_BASE_URL}/embeddings`, 1024 dimensions, cosine distance. PrivateMode is the reference provider; any OpenAI-compatible `/embeddings` endpoint works.
- **Configuration:** `EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` env vars (defaulting to `qwen3-embedding-4b` and `1024`), mirroring the gmail project.
- **No model lock-in.** All chat and embedding calls go through one client interface; switching providers is one config change. The dimension is fixed per-collection, so switching embedding models requires a re-index — that's by design and acceptable.

---

## 5. Repository layout

Single repo, two top-level concerns:

```
src/                — Python backend
frontend/           — SvelteKit PWA
pages/              — user content (HTML files, git-tracked, source of truth)
data/               — runtime state (sessions, BM25 vocab) — gitignored
qdrant/             — Qdrant storage volume — gitignored
docs/spec.md        — this file
docker-compose.yml  — Quart + Qdrant
```

**One Quart process** serves both the API and the built Svelte assets in production. Dev runs Vite separately and proxies `/v1/*` to Quart. Qdrant runs as a sidecar container alongside Quart in both modes. Same-origin in prod, so no CORS.

**Why this shape, not the alternatives:**
- **Separate frontend repo:** more ceremony, no benefit at this scale, harder to keep API and UI in sync.
- **Node backend instead of Quart:** the existing Quart streaming + tool execution + auth + request log already work and the gmail search code we're copying is also Python. Switching languages is pure cost.
- **Vanilla JS frontend:** can't deliver the mobile UX we want; service worker, stores, offline queue are painful without a framework.
- **Next.js instead of SvelteKit:** larger runtime, slower first paint on mobile, more JS to ship. SvelteKit is the right tradeoff.

---

## 6. Open source & commercial model

### 6.1 License

**MIT.** Maximally permissive, encourages adoption, no friction for self-hosters or contributors.

### 6.2 What is open source

- All product code (backend, frontend, agent tools, page format, search index)
- Self-hosting instructions, Docker images, deploy scripts
- The exact same binary the hosted offering runs

### 6.3 What is commercial

- **Managed hosting:** zero-ops instance, automatic backups, sync across devices, mobile push, TLS, custom domain
- **LLM credits included** so users don't need their own provider key
- **Optional:** team / multi-user features, SSO, audit logs (later, not v1)

### 6.4 Why this works

- The bar to *try* the product is low (open source + good docs)
- The bar to *operate* the product reliably is high enough that most users will pay rather than DIY
- There is no feature gap between OSS and hosted — only operational quality

### 6.5 Anti-goals

- No proprietary "enterprise edition" with locked features
- No telemetry beyond opt-in error reporting
- No phoning home from self-hosted instances

---

## 7. Open questions

Decisions still to make.

1. **Pages repo vs. project repo.** Should `pages/` live inside the app repo (simple), in a sibling repo (clean separation), or be configurable (flexible, more code)? *Tentative:* configurable, default to `./pages/` inside the app data dir.
2. **Edit confirmation threshold.** When does an edit auto-apply vs. require preview confirmation? *Tentative:* auto-apply for additive edits to existing sections; preview for deletes, restructures, and new pages.
3. **Voice input transcription.** Web Speech API (free, browser-dependent, English-good) vs. Whisper via the LLM provider (paid, multilingual, more reliable). *Tentative:* Web Speech in v1, swap to Whisper if quality is the bottleneck.
4. **Multi-device sync mechanism.** Git push/pull to a remote (powerful, technical), or the backend-as-server-of-record (simpler, what users expect)? *Tentative:* backend as server of record; git history is for audit/recovery, not sync.
5. **Versioning surface in UI.** Do users see the git history? *Tentative:* no in v1, except a single "undo last edit" button. History is recoverable via the CLI.
6. **Section granularity.** How aggressively does the parser split a page into sections for indexing? Per `<section>` is obvious but a long flat page may need finer granularity (per heading? per N paragraphs?). *Tentative:* one indexed point per `<section>`, fall back to per-heading splits if a section exceeds ~1500 chars.

---

## 8. Out of scope (for v1, may revisit)

- Real-time collaboration / multi-user
- Plugin / integration ecosystem
- WYSIWYG block editor parity
- Native mobile apps (PWA only)
- End-to-end encryption (the LLM sees the content; E2E is mostly theatre here)
- Workspace / team features
- Public sharing / publishing of pages
