"""Shared fixtures for notes tests."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("NOTES_DISABLE_QDRANT", "1")
os.environ.setdefault("NOTES_EDITOR", "mock")
os.environ.setdefault("LLM_BASE_URL", "http://fake-llm")


@pytest.fixture
def tmp_pages(tmp_path, monkeypatch) -> Path:
    pages_dir = tmp_path / "pages"
    monkeypatch.setenv("PAGES_DIR", str(pages_dir))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return pages_dir
