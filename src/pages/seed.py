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
  <h1>Welcome to your workspace</h1>
  <p>
    This is an AI-native alternative to Notion, Confluence, and Loop.
    There are no block libraries, no templates to pick from, and no
    fixed schemas. You describe what you want; the assistant builds it.
  </p>
  <p>
    Every page is just HTML that grows over time as you talk to the
    assistant in the chat panel. Pages can be anything &mdash; they are
    not limited to notes:
  </p>
  <ul>
    <li>meeting notes, decision logs, retros</li>
    <li>project trackers, OKRs, weekly reviews</li>
    <li>technical design docs, runbooks, postmortems</li>
    <li>team wikis and onboarding guides</li>
    <li>reading lists, learning trackers, watch lists</li>
    <li>dashboards with inline charts that read attached data files</li>
    <li>comparison tables, scorecards, feature matrices</li>
    <li>journals, gratitude lists, habit trackers</li>
  </ul>
  <p>
    You don&rsquo;t pick a &ldquo;type&rdquo; before writing. You just
    tell the assistant what you want, and the right structure appears.
  </p>
</section>
<section>
  <h2>Try saying things like</h2>
  <ul>
    <li><em>&ldquo;Create a project tracker for our Q2 launches with
      columns Status, Owner, and Due.&rdquo;</em></li>
    <li><em>&ldquo;Start a design doc for the new auth flow with
      Background, Goals, Non-goals, and Plan sections.&rdquo;</em></li>
    <li><em>&ldquo;Add a reading list page and include the three books I
      mentioned last week.&rdquo;</em></li>
    <li><em>&ldquo;What did I decide about postgres locks?&rdquo;</em></li>
    <li><em>&ldquo;Turn this page into a table grouped by
      project.&rdquo;</em></li>
    <li><em>&ldquo;Chart the numbers in sales.csv as a bar
      chart.&rdquo;</em></li>
  </ul>
</section>
<section>
  <h2>How it works</h2>
  <p>
    When you ask for a change, the assistant invokes an HTML editor
    under the hood to make a minimal, targeted edit to the page. Every
    edit is committed to git, so undo is just &ldquo;revert the last
    change.&rdquo;
  </p>
  <p>
    Your workspace lives as HTML files on disk. Nothing is trapped in a
    proprietary format &mdash; you can open them with any browser, edit
    them with any editor, or rsync them to another machine.
  </p>
</section>
<section>
  <h2>Data lives next to pages</h2>
  <p>
    When a page has associated data (a CSV of numbers, a JSON config, an
    image), it&rsquo;s stored alongside the page in a sibling directory.
    The assistant can read and write those files and build dashboards or
    visualisations that fetch from them in real time.
  </p>
</section>
"""


GETTING_STARTED_HTML = """\
<section>
  <h1>Getting started</h1>
  <p>
    A few concrete ways to use this workspace today. Pick whichever
    matches what you actually need to do right now &mdash; the assistant
    handles the structure.
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
  <h2>2. Build a structured artifact</h2>
  <p>
    Ask for a project tracker, a design doc, a comparison table, or a
    decision log. Describe what columns or sections you want and the
    assistant will lay it out:
    <em>&ldquo;Create a project tracker for the website redesign with
    columns Task, Owner, Status, Due.&rdquo;</em>
  </p>
</section>
<section>
  <h2>3. Ask a question across your workspace</h2>
  <p>
    Ask <em>&ldquo;what did I learn about X?&rdquo;</em> or
    <em>&ldquo;where did I decide on Y?&rdquo;</em>. The assistant
    searches across every page using hybrid BM25 + semantic search and
    synthesises an answer grounded in what you actually wrote, with
    links to the source pages.
  </p>
</section>
<section>
  <h2>4. Restructure a messy page</h2>
  <p>
    When a page gets long and disorganised, ask the assistant to clean
    it up: <em>&ldquo;group these bullets by project and make a table
    for the deadlines.&rdquo;</em>
  </p>
</section>
<section>
  <h2>5. Build a dashboard from data</h2>
  <p>
    Paste a CSV into the chat, or upload it, and ask for a chart. The
    assistant stores the file alongside the page and adds an inline
    visualisation you can iterate on. See the <strong>chart
    example</strong> page for a working demo.
  </p>
</section>
<section>
  <h2>6. Connect pages into a wiki</h2>
  <p>
    As your workspace grows, ask the assistant to add cross-references
    between related pages. Over time, your collection becomes a
    connected web of knowledge instead of a flat list of files.
  </p>
</section>
"""


CHART_EXAMPLE_HTML = """\
<section>
  <h1>Example: a small dashboard</h1>
  <p>
    This page demonstrates the data-alongside-pages pattern. The CSV
    lives in <code>chart-example.data/sales.csv</code> and a small inline
    script fetches it and renders a bar chart. Any page in your
    workspace can become a dashboard like this &mdash; just attach data
    and ask the assistant to chart it.
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


PROJECT_TRACKER_HTML = """\
<section>
  <h1>Example: a project tracker</h1>
  <p>
    Pages in this workspace are not just prose. This one is a small
    project tracker built entirely as HTML &mdash; ask the assistant to
    add a row, change a status, or split it by owner and the table
    updates in place.
  </p>
</section>
<section>
  <h2>Q2 launches</h2>
  <table>
    <thead>
      <tr><th>Project</th><th>Owner</th><th>Status</th><th>Due</th></tr>
    </thead>
    <tbody>
      <tr><td>Mobile redesign</td><td>Alex</td><td>In progress</td><td>2026-05-15</td></tr>
      <tr><td>Auth migration</td><td>Sam</td><td>Blocked on legal review</td><td>2026-04-30</td></tr>
      <tr><td>Onboarding revamp</td><td>Priya</td><td>Not started</td><td>2026-06-01</td></tr>
      <tr><td>Pricing experiment</td><td>Jordan</td><td>Done</td><td>2026-04-08</td></tr>
    </tbody>
  </table>
</section>
<section>
  <h2>Try it</h2>
  <p>
    Open the chat and try things like:
  </p>
  <ul>
    <li><em>&ldquo;Mark the auth migration as Done.&rdquo;</em></li>
    <li><em>&ldquo;Add a row for the analytics rebuild, owned by Sam,
      not started, due July 1.&rdquo;</em></li>
    <li><em>&ldquo;Group this by owner instead of being one big
      table.&rdquo;</em></li>
    <li><em>&ldquo;Add a Notes column.&rdquo;</em></li>
  </ul>
  <p>
    The assistant edits this HTML page directly. There&rsquo;s no
    database behind the table &mdash; the table <em>is</em> the data.
  </p>
</section>
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
        title="Example: a small dashboard",
        body_html=CHART_EXAMPLE_HTML,
        tags=["example", "data", "dashboard"],
        slug="chart-example",
        commit_message="seed: dashboard example",
    )
    # Attach the sample CSV
    data_dir = store.pages_dir / "chart-example.data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "sales.csv").write_text(SALES_CSV, encoding="utf-8")
    store._commit("seed: dashboard example data", subject="seed: dashboard example data")
    created.append("chart-example")

    store.create(
        title="Example: a project tracker",
        body_html=PROJECT_TRACKER_HTML,
        tags=["example", "tracker"],
        slug="project-tracker",
        commit_message="seed: project tracker example",
    )
    created.append("project-tracker")

    return created
