"""First-run seed of the pages directory.

Creates a small set of starter pages so a brand-new install has something
to look at. Only runs when `pages/` has no *.html files.
"""
from __future__ import annotations

import logging
from pathlib import Path

try:
    from .store import PageStore
except ImportError:  # pragma: no cover
    from store import PageStore  # type: ignore

logger = logging.getLogger(__name__)


WELCOME_HTML = """\
<section>
  <h1>Welcome to your notebook</h1>
  <p>
    This is a different kind of notes app. There are no fixed structures,
    no templates, no block types. Every page is just HTML that grows over
    time as you talk to the assistant in the chat panel.
  </p>
  <p>
    Try saying things like:
  </p>
  <ul>
    <li><em>&ldquo;Add a page about my current reading list and include
      the three books I mentioned last week.&rdquo;</em></li>
    <li><em>&ldquo;What did I write about postgres locks?&rdquo;</em></li>
    <li><em>&ldquo;Turn this page into a table grouped by project.&rdquo;</em></li>
    <li><em>&ldquo;Chart the numbers in sales.csv as a bar chart.&rdquo;</em></li>
  </ul>
</section>
<section>
  <h2>How it works</h2>
  <p>
    When you ask for a change, the assistant invokes Claude Code under the
    hood to make a minimal, targeted edit to the page file. Every edit is
    committed to git, so undo is just &ldquo;revert the last change.&rdquo;
  </p>
  <p>
    Your notes live as HTML files on disk. Nothing is trapped in a
    proprietary format &mdash; you can open them with any browser, edit them
    with any editor, or rsync them to another machine.
  </p>
</section>
<section>
  <h2>Data lives next to pages</h2>
  <p>
    When a page has associated data (a CSV of numbers, a JSON config, an
    image), it&rsquo;s stored alongside the page in a sibling directory.
    The assistant can read and write those files and generate
    visualisations that fetch from them.
  </p>
</section>
"""


GETTING_STARTED_HTML = """\
<section>
  <h1>Getting started</h1>
  <p>
    Here are a few concrete ways to use this notebook today.
  </p>
</section>
<section>
  <h2>1. Capture a thought</h2>
  <p>
    Open the chat and say what&rsquo;s on your mind. The assistant will
    either add it to an existing page that fits, or create a new one.
    You don&rsquo;t have to decide where things go.
  </p>
</section>
<section>
  <h2>2. Ask a question about your notes</h2>
  <p>
    Ask <em>&ldquo;what did I learn about X?&rdquo;</em> or
    <em>&ldquo;where did I write about Y?&rdquo;</em>. The assistant
    searches across your whole notebook using hybrid BM25 + semantic
    search and synthesises an answer grounded in what you actually wrote.
  </p>
</section>
<section>
  <h2>3. Restructure a messy page</h2>
  <p>
    When a page gets long and disorganised, ask the assistant to clean it
    up: <em>&ldquo;group these bullets by project and make a table for the
    deadlines.&rdquo;</em>
  </p>
</section>
<section>
  <h2>4. Chart some data</h2>
  <p>
    Paste a CSV into the chat, or upload it, and ask for a chart. The
    assistant stores the file alongside the page and adds an inline
    visualisation you can iterate on.
  </p>
</section>
"""


CHART_EXAMPLE_HTML = """\
<section>
  <h1>Example: charting a CSV</h1>
  <p>
    This page demonstrates the data-alongside-pages pattern. The CSV
    lives in <code>chart-example.data/sales.csv</code> and a small inline
    script fetches it and renders a bar chart.
  </p>
</section>
<section>
  <h2>Chart</h2>
  <figure data-derived="true">
    <canvas id="chart-canvas" width="640" height="320"
      style="max-width:100%;background:#fafafa;border-radius:12px;"></canvas>
    <figcaption>Quarterly sales, from <code>sales.csv</code>.</figcaption>
  </figure>
  <script>
  (async () => {
    const pageId = "chart-example";
    const res = await fetch(`/v1/pages/${pageId}/data/sales.csv`);
    if (!res.ok) return;
    const text = await res.text();
    const rows = text.trim().split(/\\n/).slice(1).map(l => l.split(","));
    const labels = rows.map(r => r[0]);
    const values = rows.map(r => Number(r[1]));

    const canvas = document.getElementById("chart-canvas");
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height, pad = 32;
    const max = Math.max(...values, 1);
    const barW = (W - pad * 2) / values.length * 0.7;
    const gap  = (W - pad * 2) / values.length * 0.3;

    ctx.fillStyle = "#fafafa";
    ctx.fillRect(0, 0, W, H);
    ctx.font = "12px -apple-system, system-ui, sans-serif";
    ctx.textAlign = "center";

    values.forEach((v, i) => {
      const x = pad + i * (barW + gap);
      const h = (v / max) * (H - pad * 2);
      const y = H - pad - h;
      const grad = ctx.createLinearGradient(0, y, 0, y + h);
      grad.addColorStop(0, "#6366f1");
      grad.addColorStop(1, "#8b5cf6");
      ctx.fillStyle = grad;
      ctx.fillRect(x, y, barW, h);
      ctx.fillStyle = "#1e293b";
      ctx.fillText(labels[i], x + barW / 2, H - pad + 16);
      ctx.fillText(String(v), x + barW / 2, y - 6);
    });
  })();
  </script>
</section>
<section>
  <h2>Try it</h2>
  <p>
    Ask the assistant: <em>&ldquo;change the chart colors to green&rdquo;</em>
    or <em>&ldquo;add a row for Q4 with 820&rdquo;</em>. It&rsquo;ll edit this
    page (and the CSV, for the second one) and the chart will update.
  </p>
</section>
"""


SALES_CSV = """quarter,sales
Q1,410
Q2,520
Q3,670
"""


def maybe_seed(store: PageStore) -> list[str]:
    """Seed the store if empty. Returns the list of created page ids."""
    existing = list(store.pages_dir.glob("*.html"))
    if existing:
        return []
    logger.info("seeding empty pages dir: %s", store.pages_dir)
    created: list[str] = []

    store.create(
        title="Welcome",
        body_html=WELCOME_HTML,
        tags=["welcome"],
        slug="welcome",
        commit_message="seed: welcome page",
    )
    created.append("welcome")

    store.create(
        title="Getting started",
        body_html=GETTING_STARTED_HTML,
        tags=["welcome", "guide"],
        slug="getting-started",
        commit_message="seed: getting started",
    )
    created.append("getting-started")

    store.create(
        title="Example: charting a CSV",
        body_html=CHART_EXAMPLE_HTML,
        tags=["example", "data"],
        slug="chart-example",
        commit_message="seed: chart example",
    )
    # Attach the sample CSV
    data_dir = store.pages_dir / "chart-example.data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "sales.csv").write_text(SALES_CSV, encoding="utf-8")
    store._commit("seed: chart example data", subject="seed: chart example data")
    created.append("chart-example")

    return created
