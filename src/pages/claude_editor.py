"""HTML page editor.

The editor delegates HTML edits to Claude Code, invoked via the
ClaudeAgent wrapper from agent_scripts/agent.py. The orchestrator LLM
does NOT write HTML itself; that's deliberately delegated to Claude
Code, which is much better at file-aware structural edits.

Modes (NOTES_EDITOR env var):
- claude (default): real Claude Code subprocess via ClaudeAgent
- mock:             test stub that appends a section recording the
                    instruction text. Only used by the test suite —
                    NEVER set NOTES_EDITOR=mock in production; the
                    edit will literally consist of pasting the
                    instruction onto the page.
- (test injection): tests can call `set_editor_fn(...)` to install a
                    fake editor that mutates the page directly. Takes
                    precedence over the mode setting.

Workflow per edit:
1. Snapshot the git HEAD of the pages repo.
2. Build a focused prompt naming the target file + the instruction +
   the conventions claude must preserve (title, data-section-id,
   data-direct-edit, etc.).
3. Invoke `claude -p --allowedTools Read,Edit,Write <prompt>` with cwd
   set to the pages directory, via ClaudeAgent.
4. Validate the resulting file (well-formed HTML, has <title>).
5. Commit on success, restore the snapshot on any failure.
6. Re-index the changed page in Qdrant if an indexer is wired.

See docs/spec.md §4.9.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

try:
    from agent_scripts.agent import ClaudeAgent
except ImportError:  # pragma: no cover - claude editor unavailable
    ClaudeAgent = None  # type: ignore[assignment,misc]

try:
    from .data_store import DataStore
    from .parser import validate_html
    from .store import PageRecord, PageStore, PageStoreError
except ImportError:  # pragma: no cover
    from data_store import DataStore  # type: ignore
    from parser import validate_html  # type: ignore
    from store import PageRecord, PageStore, PageStoreError  # type: ignore

logger = logging.getLogger(__name__)

# claude --allowedTools list. Tight enough that claude can only touch
# files in cwd (=pages_dir); broad enough to read other pages for
# cross-reference and edit/create the target file.
CLAUDE_ALLOWED_TOOLS = "Read,Edit,Write"


@dataclass
class EditResult:
    ok: bool
    page_id: str
    rev: str | None = None
    summary: str = ""
    error: str | None = None
    stdout_tail: str = ""


# Plug point for tests: if set, this callable is used instead of any
# real editor. Signature: (page_path, instruction, context) -> None.
_injected_editor_fn: Optional[Callable[[Path, str, dict], None]] = None


def set_editor_fn(fn: Optional[Callable[[Path, str, dict], None]]) -> None:
    global _injected_editor_fn
    _injected_editor_fn = fn


EDIT_PROMPT_TEMPLATE = """\
You are editing a single HTML page in the user's AI-native workspace.
The workspace is like Notion / Confluence / Loop — pages can be docs,
wikis, project trackers, dashboards with inline charts, decision logs,
comparison tables, runbooks, journals, design docs, or any other
structured artifact built from HTML + CSS + small inline scripts. There
is no fixed schema; pick the right HTML primitive (table, list,
details, canvas, ...) for the change you are making.

TARGET FILE (the only file you may modify): {filename}

USER INSTRUCTION:
{instruction}

{context_block}CONVENTIONS YOU MUST FOLLOW:
- Keep the existing <title> unless the instruction explicitly asks to
  change it.
- Preserve every `data-section-id` attribute on existing <section>
  elements. New sections you add can omit this attribute — the system
  will assign one.
- Never remove or alter elements with `data-direct-edit="true"` unless
  the instruction explicitly targets them.
- Elements with `data-derived="true"` are safe to regenerate or
  replace.
- Do NOT modify any file other than {filename}.
- Do NOT create new files outside of {filename}. Data files (CSV/JSON/
  etc.) already live in {data_dir_name}/ — reference them from the
  HTML with `/v1/pages/{page_id}/data/<filename>` URLs but do not
  create them in this operation unless the instruction explicitly
  asks.
- Keep the document well-formed HTML5: <html>, <head> with <title>,
  and <body>.
- Make minimal, surgical edits. Do not reformat untouched sections.

When you are done, exit. The system will validate the file, commit,
and surface the result to the user.
"""


def _build_prompt(
    page_record: PageRecord,
    instruction: str,
    context: dict,
) -> str:
    ctx_lines: list[str] = []
    sections = context.get("page_index") or []
    if sections:
        ctx_lines.append("CURRENT SECTIONS:")
        for s in sections:
            extra = " (direct edit, do not change)" if s.get("direct_edit") else ""
            ctx_lines.append(f"- {s['id']}: {s['heading']!r}{extra}")
    data_files = context.get("data_files") or []
    if data_files:
        ctx_lines.append("")
        ctx_lines.append("DATA FILES ATTACHED TO THIS PAGE:")
        for df in data_files:
            ctx_lines.append(
                f"- {page_record.id}.data/{df['name']} ({df.get('size', '?')} bytes)"
            )
    ctx_lines.append("")
    context_block = "\n".join(ctx_lines)
    if context_block.strip():
        context_block += "\n\n"
    return EDIT_PROMPT_TEMPLATE.format(
        filename=f"{page_record.id}.html",
        page_id=page_record.id,
        instruction=instruction.strip(),
        context_block=context_block,
        data_dir_name=f"{page_record.id}.data",
    )


def _claude_edit(
    page_record: PageRecord,
    instruction: str,
    context: dict,
    *,
    pages_dir: Path,
) -> None:
    """Invoke Claude Code via ClaudeAgent against the target page.

    cwd is the entire pages directory so claude can read other pages
    for cross-reference. allowedTools is restricted (Read, Edit, Write)
    so claude cannot run shell commands or touch anything outside cwd.
    """
    if ClaudeAgent is None:
        raise RuntimeError(
            "ClaudeAgent is not importable. Make sure agent_scripts/ is "
            "on the Python path (the Docker image must COPY agent_scripts)."
        )
    prompt = _build_prompt(page_record, instruction, context)
    agent = ClaudeAgent(allowed_tools=CLAUDE_ALLOWED_TOOLS)
    logger.info(
        "invoking ClaudeAgent for page %s in %s", page_record.id, pages_dir
    )
    agent.run(pages_dir, prompt)


def _mock_edit(page_path: Path, instruction: str, _context: dict) -> None:
    """Test stub: appends a section recording the instruction.

    Deliberately boring. NEVER set NOTES_EDITOR=mock in production —
    edits will literally consist of pasting the instruction text onto
    the page. The orchestrator's "did the edit work?" check will say
    yes because the file changed, but the change is meaningless.

    The unused `_context` parameter exists to match the
    `(Path, str, dict) -> None` shape that the injection seam uses.
    """
    html = page_path.read_text(encoding="utf-8")
    note = (
        f'<section data-derived="true">'
        f'<h3>Note</h3><p>{instruction}</p></section>'
    )
    if "</body>" in html:
        html = html.replace("</body>", f"    {note}\n  </body>")
    else:
        html = html + note
    page_path.write_text(html, encoding="utf-8")


class ClaudeEditor:
    """High-level page editor.

    Production: invokes Claude Code via ClaudeAgent.
    Tests: uses the mock editor or an injected callable.
    """

    def __init__(
        self,
        store: PageStore,
        data_store: DataStore,
        *,
        reindex: Callable[[PageRecord], Awaitable[int]] | None = None,
        mode: str | None = None,
    ):
        self.store = store
        self.data_store = data_store
        self.reindex = reindex
        self.mode = (mode or os.environ.get("NOTES_EDITOR") or "claude").lower()

    async def edit_page(
        self,
        page_id: str,
        instruction: str,
        *,
        context: dict | None = None,
    ) -> EditResult:
        if not instruction.strip():
            return EditResult(
                ok=False, page_id=page_id, error="empty instruction"
            )
        try:
            record = self.store.read(page_id)
        except PageStoreError as exc:
            return EditResult(ok=False, page_id=page_id, error=str(exc))

        ctx = dict(context or {})
        ctx.setdefault("page_index", record.parsed.section_index())
        try:
            ctx.setdefault(
                "data_files",
                [df.__dict__ for df in self.data_store.list(page_id)],
            )
        except Exception:  # pragma: no cover - defensive
            ctx.setdefault("data_files", [])

        snapshot = self.store.snapshot()
        page_path = record.path

        try:
            if _injected_editor_fn is not None:
                _injected_editor_fn(page_path, instruction, ctx)
            elif self.mode == "mock":
                _mock_edit(page_path, instruction, ctx)
            elif self.mode == "claude":
                _claude_edit(
                    record, instruction, ctx, pages_dir=self.store.pages_dir
                )
            else:
                self.store.restore(snapshot)
                return EditResult(
                    ok=False,
                    page_id=page_id,
                    error=f"unknown editor mode: {self.mode}",
                )
        except subprocess.CalledProcessError as exc:
            self.store.restore(snapshot)
            return EditResult(
                ok=False,
                page_id=page_id,
                error=f"claude exited with code {exc.returncode}",
            )
        except FileNotFoundError as exc:
            self.store.restore(snapshot)
            return EditResult(
                ok=False,
                page_id=page_id,
                error=f"claude binary not found: {exc}",
            )
        except Exception as exc:
            logger.exception("editor raised")
            self.store.restore(snapshot)
            return EditResult(
                ok=False, page_id=page_id, error=f"editor error: {exc}"
            )

        # Validate + commit
        new_html = page_path.read_text(encoding="utf-8")
        ok, err = validate_html(new_html)
        if not ok:
            self.store.restore(snapshot)
            return EditResult(
                ok=False,
                page_id=page_id,
                error=f"validation failed: {err}",
            )

        try:
            updated_record = self.store.write(
                page_id,
                new_html,
                commit_message=f"edit {page_id}: {instruction[:60]}",
            )
        except PageStoreError as exc:
            self.store.restore(snapshot)
            return EditResult(
                ok=False, page_id=page_id, error=f"write failed: {exc}"
            )

        rev = self.store.snapshot()

        if self.reindex is not None:
            try:
                await self.reindex(updated_record)
            except Exception:  # pragma: no cover
                logger.exception("reindex failed after edit")

        return EditResult(
            ok=True,
            page_id=page_id,
            rev=rev,
            summary=f"edited {page_id}",
        )

    async def create_page(
        self,
        title: str,
        initial_instruction: str,
        *,
        tags: list[str] | None = None,
        slug: str | None = None,
    ) -> EditResult:
        # Create a placeholder page first, then let claude flesh it out.
        placeholder = (
            f'<section data-derived="true"><h1>{title}</h1>'
            f'<p><em>Building this page from instruction…</em></p></section>'
        )
        record = self.store.create(
            title=title,
            body_html=placeholder,
            tags=tags or [],
            slug=slug,
        )
        return await self.edit_page(
            record.id,
            initial_instruction,
            context={"is_new_page": True},
        )
