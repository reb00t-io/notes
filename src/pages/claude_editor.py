"""Claude Code HTML editor wrapper.

Responsibilities:
1. Build a constrained prompt targeting a single page file.
2. Invoke the `claude` CLI subprocess with cwd = pages_dir.
3. Validate the result (HTML well-formed, title present, no out-of-scope writes).
4. Commit on success, roll back on failure.
5. Re-index the changed page.

The editor is pluggable via NOTES_EDITOR env var:
- claude (default): real claude CLI invocation
- mock: fallback that applies a minimal deterministic edit (for CI / no-claude envs)
- inject: tests can inject a callable via `set_editor_fn`

See docs/spec.md §4.9.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

try:
    from .data_store import DataStore
    from .parser import parse_html, validate_html
    from .store import PageRecord, PageStore, PageStoreError
except ImportError:  # pragma: no cover
    from data_store import DataStore  # type: ignore
    from parser import parse_html, validate_html  # type: ignore
    from store import PageRecord, PageStore, PageStoreError  # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class EditResult:
    ok: bool
    page_id: str
    rev: str | None = None
    summary: str = ""
    error: str | None = None
    stdout_tail: str = ""


# Plug point for tests: if set, this callable is used instead of the
# real claude subprocess. Signature: (page_path: Path, instruction: str,
# context: dict) -> None (it should mutate page_path directly).
_injected_editor_fn: Optional[Callable[[Path, str, dict], None]] = None


def set_editor_fn(fn: Optional[Callable[[Path, str, dict], None]]) -> None:
    global _injected_editor_fn
    _injected_editor_fn = fn


EDIT_PROMPT_TEMPLATE = """\
You are editing a single HTML page in the user's AI-native workspace.
The workspace is like Notion or Confluence — pages can be docs, wikis,
project trackers, dashboards, decision logs, comparison tables,
journals, or any structured artifact built from HTML + CSS + small
inline scripts. There is no fixed schema; build whatever the
instruction describes.

TARGET FILE (only this file may be modified): {filename}

USER INSTRUCTION:
{instruction}

{context_block}CONVENTIONS YOU MUST FOLLOW:
- Keep the existing <title> unless the instruction asks you to change it.
- Preserve every `data-section-id` attribute on `<section>` elements. If you
  add a new section, leave it without an id — the system will assign one.
- Never remove or alter any element that has `data-direct-edit="true"` unless
  the instruction explicitly targets it.
- Elements with `data-derived="true"` are safe to regenerate.
- Do NOT modify any file other than {filename}.
- Do NOT create new files outside of {filename}. Data files (CSV/JSON/etc.)
  already live in {data_dir_name}/ — reference them from the HTML but do not
  create them in this operation unless the instruction explicitly asks for it.
- Keep the result well-formed HTML5 with <title> and <body>.
- Prefer minimal, surgical edits. Do not reformat untouched sections.

When you are done, respond with a one-line summary of what changed.
"""


def _build_prompt(
    page_record: PageRecord,
    instruction: str,
    context: dict,
    data_dir_name: str,
) -> str:
    ctx_lines: list[str] = []
    if context.get("page_index"):
        ctx_lines.append("CURRENT SECTIONS:")
        for s in context["page_index"]:
            ctx_lines.append(
                f"- {s['id']}: {s['heading']!r}{' (direct edit)' if s.get('direct_edit') else ''}"
            )
    if context.get("data_files"):
        ctx_lines.append("\nDATA FILES ATTACHED TO THIS PAGE:")
        for df in context["data_files"]:
            ctx_lines.append(f"- {data_dir_name}/{df['name']} ({df['size']} bytes)")
    ctx_lines.append("")
    context_block = "\n".join(ctx_lines)
    if context_block.strip():
        context_block += "\n\n"
    return EDIT_PROMPT_TEMPLATE.format(
        filename=f"{page_record.id}.html",
        instruction=instruction.strip(),
        context_block=context_block,
        data_dir_name=f"{page_record.id}.data",
    )


def _run_claude_subprocess(prompt: str, cwd: Path, stdout_tail_lines: int = 20) -> tuple[int, str, str]:
    binary = os.environ.get("CLAUDE_BIN", "claude")
    cmd = [
        binary,
        "-p",
        prompt,
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    logger.info("running claude cwd=%s", cwd)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=float(os.environ.get("CLAUDE_TIMEOUT", "180")),
        )
    except FileNotFoundError as exc:
        return 127, "", f"claude binary not found: {exc}"
    except subprocess.TimeoutExpired:
        return 124, "", "claude timed out"

    # Extract the last few text lines from stream-json for a readable tail
    tail: list[str] = []
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
            if ev.get("type") == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "text" and block.get("text"):
                        tail.append(block["text"].strip())
        except json.JSONDecodeError:
            continue
    tail_text = "\n".join(tail[-stdout_tail_lines:])
    return proc.returncode, tail_text, proc.stderr


def _mock_edit(page_path: Path, instruction: str, context: dict) -> None:
    """Test stub: appends a section recording the instruction.

    Deliberately boring. Only intended for tests — never set
    NOTES_EDITOR=mock in production; the agent's edits will look like
    "the instruction was pasted onto the page" because that's literally
    what this function does.
    """
    html = page_path.read_text(encoding="utf-8")
    note = f'<section data-derived="true"><h3>Note</h3><p>{instruction}</p></section>'
    if "</body>" in html:
        html = html.replace("</body>", f"    {note}\n  </body>")
    else:
        html = html + note
    page_path.write_text(html, encoding="utf-8")


LLM_EDIT_SYSTEM_PROMPT = """\
You are an HTML page editor for an AI-native workspace (think Notion / \
Confluence / Loop). You receive the current HTML of a single workspace \
page and an instruction describing what to change. You output the \
COMPLETE new HTML for the page after applying the change.

Pages in this workspace are not just notes — they can be docs, wikis,
project trackers, dashboards with inline charts, comparison tables,
decision logs, journals, runbooks, design docs, or any structured
artifact made from HTML + CSS + small inline scripts. There is no fixed
schema. Build whatever the instruction describes.

Hard rules:
- Output ONLY the raw HTML. No commentary, no explanation, no markdown
  fences. Start with <!doctype html> or <html>. End with </html>.
- Preserve the existing <title> unless the instruction explicitly asks
  to change it.
- Preserve every `data-section-id` attribute on existing <section>
  elements. New sections you add can omit this attribute — the system
  will assign one.
- Never remove or alter elements with `data-direct-edit="true"` unless
  the instruction explicitly targets them.
- Elements with `data-derived="true"` are safe to regenerate or replace.
- Make the smallest, most surgical change that satisfies the
  instruction. Do not reformat or rewrite untouched sections.
- Keep the document well-formed HTML5: <html>, <head> with <title>,
  and <body>.
- Reference data files by relative URLs like
  `/v1/pages/<page_id>/data/<filename>` — they are already attached.
- For structured content (tables, lists, trackers, dashboards), pick
  the right HTML primitive for the job: <table> for tabular data,
  <ul>/<ol> for lists, <details>/<summary> for collapsible sections,
  <canvas> + inline <script> for charts that read attached data files.
"""


def _strip_code_fence(text: str) -> str:
    """Some LLMs wrap output in ```html ... ``` even when told not to."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    # Drop opening fence (possibly with language tag)
    lines = lines[1:]
    # Drop trailing fence if present
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


async def _llm_edit(
    page_path: Path,
    instruction: str,
    context: dict,
    *,
    client_factory=None,
) -> None:
    """Edit a page by asking the orchestrator LLM to rewrite its HTML.

    Sends a single non-streaming chat completion to LLM_BASE_URL with the
    current HTML + the instruction, and writes the response back to disk.
    Used in production where Claude Code is not installed.
    """
    import httpx

    base = os.environ.get("LLM_BASE_URL", "")
    key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_EDIT_MODEL") or os.environ.get("LLM_MODEL") or "gpt-oss-120b"
    if not base:
        raise RuntimeError("LLM_BASE_URL not set")

    current_html = page_path.read_text(encoding="utf-8")

    data_files = context.get("data_files") or []
    if data_files:
        df_lines = "\n".join(
            f"- {d['name']} ({d.get('size', '?')} bytes)" for d in data_files
        )
    else:
        df_lines = "(none)"

    section_summary = "(no sections yet)"
    sections = context.get("page_index") or []
    if sections:
        section_summary = "\n".join(
            f"- {s['id']}: {s['heading']!r}" for s in sections
        )

    user_msg = (
        f"INSTRUCTION:\n{instruction.strip()}\n\n"
        f"DATA FILES ATTACHED TO THIS PAGE:\n{df_lines}\n\n"
        f"CURRENT SECTIONS:\n{section_summary}\n\n"
        f"CURRENT HTML:\n{current_html}\n\n"
        "Output the new complete HTML now, and nothing else."
    )

    factory = client_factory or httpx.AsyncClient
    timeout = float(os.environ.get("LLM_EDIT_TIMEOUT", "180"))
    async with factory(timeout=timeout) as client:
        resp = await client.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": LLM_EDIT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"unexpected LLM response shape: {exc}") from exc

    new_html = _strip_code_fence(content)
    if not new_html.strip():
        raise RuntimeError("LLM returned empty content")

    # Sanity guard: never write something that obviously isn't HTML.
    lower = new_html.lower().lstrip()
    if not (lower.startswith("<!doctype") or lower.startswith("<html")):
        raise RuntimeError(
            f"LLM did not return a full HTML document (got: {new_html[:80]!r})"
        )

    page_path.write_text(new_html, encoding="utf-8")


class ClaudeEditor:
    """High-level page editor that invokes Claude Code against one file."""

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
            ctx.setdefault("data_files", [df.__dict__ for df in self.data_store.list(page_id)])
        except Exception:  # pragma: no cover - defensive
            ctx.setdefault("data_files", [])

        snapshot = self.store.snapshot()
        prompt = _build_prompt(record, instruction, ctx, f"{page_id}.data")

        page_path = record.path
        stdout_tail = ""

        try:
            if _injected_editor_fn is not None:
                _injected_editor_fn(page_path, instruction, ctx)
            elif self.mode == "mock":
                _mock_edit(page_path, instruction, ctx)
            elif self.mode == "llm":
                await _llm_edit(page_path, instruction, ctx)
            else:
                rc, tail, stderr = _run_claude_subprocess(prompt, self.store.pages_dir)
                stdout_tail = tail
                if rc != 0:
                    self.store.restore(snapshot)
                    return EditResult(
                        ok=False,
                        page_id=page_id,
                        error=f"claude exited {rc}: {stderr.strip()[:500]}",
                        stdout_tail=tail,
                    )
        except Exception as exc:
            logger.exception("editor raised")
            self.store.restore(snapshot)
            return EditResult(ok=False, page_id=page_id, error=f"editor error: {exc}")

        # Validate + commit
        new_html = page_path.read_text(encoding="utf-8")
        ok, err = validate_html(new_html)
        if not ok:
            self.store.restore(snapshot)
            return EditResult(
                ok=False,
                page_id=page_id,
                error=f"validation failed: {err}",
                stdout_tail=stdout_tail,
            )

        try:
            updated_record = self.store.write(
                page_id, new_html, commit_message=f"edit {page_id}: {instruction[:60]}"
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
            stdout_tail=stdout_tail,
        )

    async def create_page(
        self,
        title: str,
        initial_instruction: str,
        *,
        tags: list[str] | None = None,
        slug: str | None = None,
    ) -> EditResult:
        # Create an empty-ish scaffold first, then let claude flesh it out.
        placeholder = (
            f'<section data-derived="true"><h1>{title}</h1>'
            f'<p><em>Creating this page from instruction…</em></p></section>'
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
