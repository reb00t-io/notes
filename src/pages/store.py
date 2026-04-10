"""Page store: CRUD on disk + git commits per edit.

Pages live as `pages/<slug>.html`. Data files live in `pages/<slug>.data/`.
Every mutating operation commits to git inside the pages directory so history
is the undo stack.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from .parser import ParsedPage, build_page_html, parse_html, validate_html
except ImportError:  # pragma: no cover - support flat import
    from parser import ParsedPage, build_page_html, parse_html, validate_html  # type: ignore

logger = logging.getLogger(__name__)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _default_pages_dir() -> Path:
    return Path(os.environ.get("PAGES_DIR", "pages")).resolve()


@dataclass
class PageRecord:
    id: str
    title: str
    tags: list[str]
    created: str | None
    updated: str | None
    path: Path
    data_dir: Path
    parsed: ParsedPage


class PageStoreError(Exception):
    pass


class PageStore:
    """Disk-backed page store. All writes are committed to git."""

    def __init__(self, pages_dir: Path | None = None, *, git_enabled: bool = True):
        self.pages_dir = Path(pages_dir) if pages_dir else _default_pages_dir()
        self.git_enabled = git_enabled
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        if self.git_enabled:
            self._ensure_git_repo()

    # ── git helpers ──────────────────────────────────────────────────

    def _run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(self.pages_dir),
            check=check,
            capture_output=True,
            text=True,
        )

    def _ensure_git_repo(self) -> None:
        if (self.pages_dir / ".git").exists():
            return
        try:
            self._run_git("init", "-q", "-b", "main")
            self._run_git("config", "user.email", "notes@localhost")
            self._run_git("config", "user.name", "notes")
            gitignore = self.pages_dir / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text("*.swp\n.DS_Store\n")
            self._run_git("add", ".gitignore")
            self._run_git("commit", "-q", "-m", "init pages repo", check=False)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.warning("git unavailable: %s", exc)
            self.git_enabled = False

    def _commit(self, message: str, subject: str = "notes") -> str | None:
        if not self.git_enabled:
            return None
        try:
            self._run_git("add", "-A")
            status = self._run_git("status", "--porcelain")
            if not status.stdout.strip():
                return None
            full_msg = f"{subject}\n\n{message}" if message != subject else subject
            self._run_git("commit", "-q", "-m", full_msg)
            rev = self._run_git("rev-parse", "HEAD").stdout.strip()
            return rev
        except subprocess.CalledProcessError as exc:
            logger.exception("git commit failed: %s", exc.stderr)
            return None

    def snapshot(self) -> str | None:
        if not self.git_enabled:
            return None
        try:
            return self._run_git("rev-parse", "HEAD", check=False).stdout.strip() or None
        except subprocess.CalledProcessError:  # pragma: no cover
            return None

    def restore(self, rev: str | None) -> None:
        if not self.git_enabled or not rev:
            return
        self._run_git("reset", "--hard", rev, check=False)

    def recent_commits(self, limit: int = 20) -> list[dict]:
        if not self.git_enabled:
            return []
        try:
            out = self._run_git(
                "log",
                f"-{limit}",
                "--pretty=format:%H%x1f%s%x1f%ai",
                check=False,
            ).stdout
        except subprocess.CalledProcessError:  # pragma: no cover
            return []
        rows = []
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                rows.append({"rev": parts[0], "subject": parts[1], "time": parts[2]})
        return rows

    # ── slug + path helpers ─────────────────────────────────────────

    def _page_path(self, slug: str) -> Path:
        return self.pages_dir / f"{slug}.html"

    def _data_dir(self, slug: str) -> Path:
        return self.pages_dir / f"{slug}.data"

    @staticmethod
    def slugify(title: str) -> str:
        s = title.strip().lower()
        s = re.sub(r"[^a-z0-9]+", "-", s)
        s = s.strip("-")
        return s[:64] or "page"

    def _validate_slug(self, slug: str) -> None:
        if not SLUG_RE.match(slug):
            raise PageStoreError(f"invalid slug: {slug!r}")

    def unique_slug(self, base: str) -> str:
        candidate = base
        n = 2
        while self._page_path(candidate).exists():
            candidate = f"{base}-{n}"
            n += 1
        return candidate

    # ── CRUD ─────────────────────────────────────────────────────────

    def exists(self, slug: str) -> bool:
        return self._page_path(slug).exists()

    def list_pages(self) -> list[dict]:
        results = []
        for path in sorted(self.pages_dir.glob("*.html")):
            slug = path.stem
            try:
                parsed = parse_html(path.read_text(encoding="utf-8"))
            except Exception:  # pragma: no cover - defensive
                continue
            results.append(
                {
                    "id": slug,
                    "title": parsed.title,
                    "tags": parsed.tags,
                    "updated": parsed.updated,
                    "created": parsed.created,
                }
            )
        results.sort(key=lambda r: r.get("updated") or r.get("created") or "", reverse=True)
        return results

    def read(self, slug: str) -> PageRecord:
        self._validate_slug(slug)
        path = self._page_path(slug)
        if not path.exists():
            raise PageStoreError(f"page not found: {slug}")
        html = path.read_text(encoding="utf-8")
        parsed = parse_html(html)
        return PageRecord(
            id=slug,
            title=parsed.title,
            tags=parsed.tags,
            created=parsed.created,
            updated=parsed.updated,
            path=path,
            data_dir=self._data_dir(slug),
            parsed=parsed,
        )

    def write(
        self,
        slug: str,
        html: str,
        *,
        commit_message: str,
        reparse: bool = True,
    ) -> PageRecord:
        """Write raw HTML for a page and commit. Reparses to assign section IDs."""
        self._validate_slug(slug)
        ok, err = validate_html(html)
        if not ok:
            raise PageStoreError(f"invalid html: {err}")
        path = self._page_path(slug)

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if reparse:
            parsed = parse_html(html)
            # Stamp updated + (if new) created
            if not parsed.created:
                parsed.created = now
            parsed.updated = now
            final = build_page_html(
                parsed.title,
                parsed.body_html,
                tags=parsed.tags,
                created=parsed.created,
                updated=parsed.updated,
            )
        else:
            final = html

        path.write_text(final, encoding="utf-8")
        self._commit(commit_message, subject=commit_message)
        return self.read(slug)

    def create(
        self,
        *,
        title: str,
        body_html: str,
        tags: list[str] | None = None,
        slug: str | None = None,
        commit_message: str | None = None,
    ) -> PageRecord:
        base = slug or self.slugify(title)
        self._validate_slug(base)
        chosen = self.unique_slug(base)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        full = build_page_html(
            title,
            body_html,
            tags=tags or [],
            created=now,
            updated=now,
        )
        msg = commit_message or f"create {chosen}"
        return self.write(chosen, full, commit_message=msg)

    def delete(self, slug: str, *, commit_message: str | None = None) -> None:
        self._validate_slug(slug)
        path = self._page_path(slug)
        if not path.exists():
            raise PageStoreError(f"page not found: {slug}")
        path.unlink()
        data_dir = self._data_dir(slug)
        if data_dir.exists():
            shutil.rmtree(data_dir)
        self._commit(commit_message or f"delete {slug}", subject=commit_message or f"delete {slug}")
