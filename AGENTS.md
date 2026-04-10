# AGENTS.md

## Setup
- Virtual env: `.venv` (auto-activated via `direnv allow`)
- Bootstrap: `source scripts/venv.rc` or `pip install -e '.[dev]'`
- Config: `pyproject.toml`

## Testing
- Run: `pytest`
- Path: `tests/` dir, `pythonpath = ["src"]` in pyproject.toml
- **Every code change MUST include tests.** New module `src/foo.py` → `tests/test_foo.py`.
- Use `tmp_path` and `monkeypatch`; never touch real data files.
- Run `pytest` before every commit. If tests fail, fix before committing.

## Code Style
- Python >= 3.13, snake_case everywhere
- Prefer loud failures over silent fallbacks
- No hardcoded secrets — use env vars or `.envrc.local` (gitignored)

## Commits
- Run `pytest` before committing — do not commit with failing tests
- Write concise commit messages focused on the "why"
- Do not push to main without passing tests

## Project Layout
```
src/           — application source
tests/         — pytest tests
scripts/       — build/setup scripts
.envrc         — direnv config (auto-sources .envrc.local if present)
.envrc.local   — local credentials (gitignored)
pyproject.toml — project config + dependencies
```
