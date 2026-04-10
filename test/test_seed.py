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
    assert store.exists("welcome")
    assert store.exists("getting-started")
    assert store.exists("chart-example")


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
