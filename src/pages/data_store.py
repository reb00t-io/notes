"""Per-page data files living in `pages/<slug>.data/`.

Contract:
- Files are per-page; no global shared store.
- Max 10 MB per file.
- Allowed extensions: csv, json, txt, md, png, jpg, jpeg, webp, svg.
- Path traversal is rejected (no subdirectories, no `..`).
- Writes go through the PageStore so they can be committed atomically with
  the HTML edit that references them.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from .store import PageStore, PageStoreError
except ImportError:  # pragma: no cover
    from store import PageStore, PageStoreError  # type: ignore

MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_EXTS = {"csv", "json", "txt", "md", "png", "jpg", "jpeg", "webp", "svg"}
TEXT_EXTS = {"csv", "json", "txt", "md", "svg"}
DATA_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class DataStoreError(Exception):
    pass


@dataclass
class DataFileInfo:
    name: str
    size: int
    ext: str
    is_text: bool


class DataStore:
    """Thin wrapper over PageStore for per-page data files."""

    def __init__(self, page_store: PageStore):
        self.page_store = page_store

    # ── validation ────────────────────────────────────────────────────

    @staticmethod
    def _validate_name(name: str) -> None:
        if not DATA_NAME_RE.match(name):
            raise DataStoreError(f"invalid data filename: {name!r}")
        if "/" in name or ".." in name:
            raise DataStoreError(f"invalid data filename: {name!r}")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_EXTS:
            raise DataStoreError(
                f"disallowed extension {ext!r}; allowed: {sorted(ALLOWED_EXTS)}"
            )

    def _data_dir(self, page_id: str) -> Path:
        return self.page_store.pages_dir / f"{page_id}.data"

    def _file_path(self, page_id: str, name: str) -> Path:
        self._validate_name(name)
        return self._data_dir(page_id) / name

    @staticmethod
    def _ext(name: str) -> str:
        return name.rsplit(".", 1)[-1].lower() if "." in name else ""

    @classmethod
    def is_text(cls, name: str) -> bool:
        return cls._ext(name) in TEXT_EXTS

    # ── CRUD ──────────────────────────────────────────────────────────

    def list(self, page_id: str) -> list[DataFileInfo]:
        if not self.page_store.exists(page_id):
            raise PageStoreError(f"page not found: {page_id}")
        d = self._data_dir(page_id)
        if not d.exists():
            return []
        files: list[DataFileInfo] = []
        for entry in sorted(d.iterdir()):
            if not entry.is_file() or entry.name.startswith("."):
                continue
            ext = self._ext(entry.name)
            files.append(
                DataFileInfo(
                    name=entry.name,
                    size=entry.stat().st_size,
                    ext=ext,
                    is_text=ext in TEXT_EXTS,
                )
            )
        return files

    def read_bytes(self, page_id: str, name: str) -> bytes:
        if not self.page_store.exists(page_id):
            raise PageStoreError(f"page not found: {page_id}")
        path = self._file_path(page_id, name)
        if not path.exists():
            raise DataStoreError(f"data file not found: {name}")
        return path.read_bytes()

    def read_text(self, page_id: str, name: str) -> str:
        return self.read_bytes(page_id, name).decode("utf-8")

    def write(
        self,
        page_id: str,
        name: str,
        content: bytes | str,
        *,
        commit_message: str | None = None,
    ) -> DataFileInfo:
        if not self.page_store.exists(page_id):
            raise PageStoreError(f"page not found: {page_id}")
        self._validate_name(name)
        if isinstance(content, str):
            data = content.encode("utf-8")
        else:
            data = content
        if len(data) > MAX_FILE_BYTES:
            raise DataStoreError(
                f"file too large: {len(data)} bytes (max {MAX_FILE_BYTES})"
            )
        d = self._data_dir(page_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / name
        path.write_bytes(data)
        msg = commit_message or f"update data {page_id}/{name}"
        self.page_store._commit(msg, subject=msg)
        ext = self._ext(name)
        return DataFileInfo(name=name, size=len(data), ext=ext, is_text=ext in TEXT_EXTS)

    def write_base64(
        self,
        page_id: str,
        name: str,
        b64: str,
        *,
        commit_message: str | None = None,
    ) -> DataFileInfo:
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception as exc:
            raise DataStoreError(f"invalid base64: {exc}") from exc
        return self.write(page_id, name, raw, commit_message=commit_message)

    def delete(
        self,
        page_id: str,
        name: str,
        *,
        commit_message: str | None = None,
    ) -> None:
        if not self.page_store.exists(page_id):
            raise PageStoreError(f"page not found: {page_id}")
        path = self._file_path(page_id, name)
        if path.exists():
            path.unlink()
            # If dir is empty, remove it for cleanliness
            try:
                next(self._data_dir(page_id).iterdir())
            except StopIteration:
                self._data_dir(page_id).rmdir()
            except FileNotFoundError:
                pass
            msg = commit_message or f"delete data {page_id}/{name}"
            self.page_store._commit(msg, subject=msg)
