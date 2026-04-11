"""Tests for src/pages/seed.py."""
from __future__ import annotations

from src.pages.seed import maybe_seed
from src.pages.store import PageStore


def test_seed_populates_empty_dir(tmp_pages):
    store = PageStore(pages_dir=tmp_pages)
    created = maybe_seed(store)
    assert "welcome" in created
    assert "getting-started" in created
    assert "chart-example" in created
    assert "project-tracker" in created
    assert store.exists("welcome")
    assert store.exists("getting-started")
    assert store.exists("chart-example")
    assert store.exists("project-tracker")


def test_seed_welcome_uses_workspace_framing(tmp_pages):
    """The seeded welcome page must position this as a workspace, not a notes app."""
    store = PageStore(pages_dir=tmp_pages)
    maybe_seed(store)
    welcome = store.read("welcome")
    body = welcome.path.read_text().lower()
    assert "workspace" in body
    # Should mention that pages can be more than notes
    assert "tracker" in body or "design doc" in body or "dashboard" in body


def test_seed_creates_chart_example_data_file(tmp_pages):
    store = PageStore(pages_dir=tmp_pages)
    maybe_seed(store)
    csv_path = tmp_pages / "chart-example.data" / "sales.csv"
    assert csv_path.exists()
    content = csv_path.read_text()
    assert "quarter,sales" in content


def test_seed_noop_if_pages_exist(tmp_pages):
    store = PageStore(pages_dir=tmp_pages)
    store.create(title="Existing", body_html="<p>x</p>")
    created = maybe_seed(store)
    assert created == []
    assert not store.exists("welcome")
