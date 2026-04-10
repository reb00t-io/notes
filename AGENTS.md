# AGENTS.md

## 1. Mission & Priorities
**Role of the agent in this repository:**
- This repo is an AI-native notes app: the product agent manages HTML pages for end users. When working in this repo as a developer, an agent helps extend the backend (Python/Quart), the Qdrant-backed search layer, the Claude-Code-driven editor, and the vanilla JS frontend. Agents MUST NOT modify the end-user `pages/` content during development work unless explicitly asked.

**Decision priority order:**
- correctness > safety (data integrity, git history) > maintainability > mobile UX quality > performance > speed

**Global constraints or goals:**
- Everything the product agent does must be reversible via `git revert` on the pages repo
- Self-hosters must be able to run the whole stack with `docker compose up` — no hosted-only dependencies
- All LLM-backed behavior must be mockable so tests run without network access

## 2. Executable Commands (Ground Truth)
All commands listed here must work.

- Install / setup:
  - `direnv allow`  (bootstraps `.venv` + installs `.[dev]`)
  - Or manually: `python3.13 -m venv .venv && source .venv/bin/activate && python -m ensurepip --upgrade && python -m pip install -e '.[dev]'`
- Dev server:
  - `python src/main.py`  (requires `LLM_BASE_URL`, `PORT`; Qdrant optional — set `NOTES_DISABLE_QDRANT=1` to skip)
- Lint:
  - N/A (not configured)
- Format:
  - N/A (not configured)
- Type check:
  - N/A (not configured)
- Unit tests:
  - `pytest`
- Integration / e2e tests:
  - `./test/e2e.sh`  (smoke-tests the running container)

## 3. Repository Map
**High-level structure:**
- `src/main.py` — Quart entry point, wires agent + routes + session store
- `src/pages/` — page store, parser, Qdrant indexer, hybrid search, Claude Code editor, HTTP routes, seed
- `src/client_bridge/` — WebSocket channel + DOM tools routed to the user's open tab
- `src/agent_runtime/` — single product agent: tool schemas, handlers, system prompt
- `src/agents/` — **separate** maintainer-facing self-improvement agent (unrelated to the product agent)
- `src/static/notes/` — vanilla ES modules + CSS for the mobile-first SPA
- `src/templates/index.html` — SPA shell
- `test/` — pytest suite
- `pages/` — end-user content (HTML + `<slug>.data/` dirs), git-tracked, **do not touch during dev work**
- `docs/spec.md` — product + technical specification (keep in sync with code)

**Entry points:**
- Backend: `src/main.py`
- Frontend: `src/templates/index.html` + `src/static/notes/app.js`
- CLI / Worker / Service: `src/agents/__main__.py` (self-improvement agent, not the product)

**Key configuration locations:**
- `pyproject.toml` — deps (including `qdrant-client`)
- `docker-compose.yml` — notes + qdrant sidecar
- `.envrc` — direnv config (auto-sources `.envrc.local`)
- `config/` — legacy bootstrap prompts (scheduled for removal once the self-improvement agent picks up the new ones)

## 4. Definition of Done

### Standard workflow for every change
Execute in order, do not skip steps:

1. **Develop** — implement the change, adding tests alongside it
2. **Test** — run `pytest` (all 109+ tests must pass)
3. **End-to-end** — run `PORT=31000 ./test/e2e.sh` (builds the image, starts
   the compose stack, verifies the server comes up with a fresh deploy date
   meta tag and the seeded pages are reachable, then tears down)
4. **Deploy** — run `./scripts/deploy.sh` (requires `PORT`, `PUBLIC_URL`,
   `LLM_BASE_URL`, `LLM_API_KEY`, `API_KEY`, `AUTH_PASSWORD` in the
   environment; load via `direnv allow` or `source .envrc.local`)
5. **Commit + push** — always to `main`. No feature branches or PRs for
   normal work; the repo is single-user and deploys from `main`.

If any step fails, fix it before moving on. Do not commit with failing
tests. Do not push without a successful deploy.

### Per-change checklist
- [ ] New tests cover the new behavior (unit tests in `test/test_*.py`,
      using `tmp_path` + `monkeypatch`; never touch real `pages/` content)
- [ ] `pytest` passes
- [ ] `./test/e2e.sh` passes
- [ ] `./scripts/deploy.sh` succeeds
- [ ] `docs/spec.md` updated if the behavior deviates from it
- [ ] Commit message explains the "why"
- [ ] No edits to `pages/` content as a side effect of the code change

### Common e2e / deploy gotchas
- A stale `python src/main.py` process holding `localhost:31000` will make
  e2e.sh appear to pass while actually hitting the old process. Check with
  `lsof -iTCP:31000` before running e2e if you see stale template output.
- Docker bind-mount source dirs are auto-created by docker as root if
  absent. `deploy.sh` handles this via `sudo chown`/`chmod` on the remote;
  passwordless `sudo` is required on the deploy host.
- The Docker image runs with `NOTES_EDITOR=mock` (no `claude` CLI in the
  container). Real Claude Code edits only work in local dev.

## 5. Code Style & Conventions (Repo-Specific)
Only list conventions that are easy to get wrong.

- Language(s) + version(s):
  - `python@>=3.13`, `javascript` (native ES modules, no bundler)
- Formatter:
  - None configured. Match surrounding style. Python: 4-space indent, snake_case, type hints where trivial.
- Naming conventions:
  - Python: snake_case everywhere. Page slugs are kebab-case `[a-z0-9-]`.
- Error handling pattern:
  - Prefer loud failures over silent fallbacks for programmer errors.
  - For user-facing errors in tool handlers, return `{"error": "..."}` dicts (do not raise) so the streaming layer can surface them.
  - Claude Code editor rolls back via `git reset --hard <snapshot>` on any failure.
- Logging rules:
  - Use module-level `logger = logging.getLogger(__name__)`.
  - `logger.info` for happy-path lifecycle events, `logger.warning` for recoverable problems, `logger.exception` in defensive `except`.
  - Never log the contents of pages, data files, or user messages.

## 6. Boundaries & Guardrails
The agent must **not**:
- Modify files under `pages/` as part of a code change (that's the product agent's job, not the dev agent's)
- Introduce new runtime dependencies without updating `pyproject.toml` AND `docker-compose.yml`
- Disable or skip tests to make them pass
- Add a node / npm build step (the frontend is deliberately bundler-free)
- Remove the self-improvement agent in `src/agents/` — it's orthogonal to the product

When unsure:
- Prefer the smallest possible change
- Leave a TODO with context rather than guessing
- Ask before taking destructive actions on `pages/` or `qdrant/` volumes

## 7. Security & Privacy Constraints
- Sensitive data locations:
  - `.envrc.local` (gitignored) for `LLM_API_KEY`, `API_KEY`, `AUTH_PASSWORD`
  - User content in `pages/` — may contain PII, must not be exfiltrated to logs or tool outputs
- Redaction / handling rules:
  - Request logging redacts `Authorization`, `Cookie`, `Set-Cookie` headers (see `src/main.py::_normalize_request_log_headers`)
  - Never write page contents to stdout in tests; use fixtures
- Approved crypto / storage patterns:
  - Passwords for auth mode are SHA-256 hashed into the Quart `secret_key`; no other crypto is expected
- Threat model notes:
  - The product agent runs Claude Code with `--dangerously-skip-permissions`; the blast radius is limited to `cwd=pages/`. Do not loosen that constraint.
  - Page HTML is rendered inside a sandboxed iframe on the frontend. Do not remove the `sandbox` attribute.

## 8. Common Pitfalls & Couplings
Things that are easy to break:
- If you change section-ID assignment in `src/pages/parser.py`, the Qdrant index becomes stale and must be rebuilt (`PageIndex.reindex_all`)
- If you touch the `tool_executor` registry, make sure `main.py` still registers both `handle_bridge_tool` and the notes agent's handler
- Do not import `src.tool_schemas` — it's legacy from the bootstrap template and should not be used by new code. Use `src/agent_runtime/tools.py` for schemas.
- The existing `test/test_improve.py` targets the self-improvement agent in `src/agents/`; it must keep passing after your changes
- Qdrant dimension is fixed at index-creation time (1024 for Qwen3-embedding-4b). Changing `EMBEDDING_MODEL` to something with different dimensions requires dropping the collection.

## 9. Examples & Canonical Patterns

### Example: add a new orchestrator tool
- Files to edit:
  - `src/agent_runtime/tools.py` — add schema to `NOTES_TOOL_SCHEMAS` and a handler function, wire it in `_handlers()`
- Tests to add:
  - `test/test_page_tools.py` — cover happy path + validation failure
- Commands to run:
  - `pytest test/test_page_tools.py`

### Example: change the system prompt
- Files to edit:
  - `src/agent_runtime/notes_agent.py::NOTES_SYSTEM_PROMPT`
- Tests to add:
  - None strictly required; the existing test_main.py assertion (`"edit_page" in prompt`) will catch regressions

## 10. Pull Requests & Branching
Default branch: main

When a PR is requested, create a branch `agent/<branch_name>` and create a PR from there using `gh`.
