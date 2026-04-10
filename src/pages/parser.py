"""HTML → section tree, with stable data-section-id anchors.

A page is parsed into a list of sections. Each section has:
- A stable id (deterministic hash of heading text + ordinal position)
- A heading (nearest h1-h4 text, or "intro" for pre-heading content)
- An HTML fragment
- A plain-text form used for search indexing

Section IDs are stable across edits: if you rename a heading, the id changes,
but if you only edit body text under an unchanged heading, the id is preserved.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString, Tag

HEADING_TAGS = ("h1", "h2", "h3", "h4")
DIRECT_EDIT_ATTR = "data-direct-edit"
DERIVED_ATTR = "data-derived"
SECTION_ID_ATTR = "data-section-id"


@dataclass
class Section:
    id: str
    heading: str
    html: str
    text: str
    ordinal: int
    direct_edit: bool = False
    derived: bool = False


@dataclass
class ParsedPage:
    title: str
    tags: list[str] = field(default_factory=list)
    created: str | None = None
    updated: str | None = None
    sections: list[Section] = field(default_factory=list)
    body_html: str = ""  # inner HTML of <body>, with section IDs assigned
    full_html: str = ""  # canonicalised full document

    def section_index(self) -> list[dict]:
        """Return a compact per-section summary suitable for tool responses."""
        return [
            {
                "id": s.id,
                "heading": s.heading,
                "ordinal": s.ordinal,
                "preview": s.text[:120],
                "direct_edit": s.direct_edit,
                "derived": s.derived,
            }
            for s in self.sections
        ]


def _slugify_fragment(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:40] or "section"


def _section_id(heading: str, ordinal: int) -> str:
    """Deterministic short id from heading text + ordinal position."""
    key = f"{_slugify_fragment(heading)}:{ordinal}".encode()
    digest = hashlib.blake2s(key, digest_size=4).hexdigest()
    return f"s-{digest}"


def _element_text(el: Tag | NavigableString) -> str:
    if isinstance(el, NavigableString):
        return str(el)
    return el.get_text(" ", strip=True)


def _split_body_into_sections(body: Tag) -> list[list[Tag | NavigableString]]:
    """Group top-level body children by heading boundaries.

    Strategy: walk top-level children; start a new group whenever we hit a
    heading tag OR an explicit <section>. Pre-heading content becomes an "intro"
    group. <section> elements are treated as already-grouped and become their
    own single group each.
    """
    groups: list[list[Tag | NavigableString]] = []
    current: list[Tag | NavigableString] = []

    def flush() -> None:
        nonlocal current
        if current and any(
            isinstance(c, Tag) or (isinstance(c, NavigableString) and str(c).strip())
            for c in current
        ):
            groups.append(current)
        current = []

    for child in body.children:
        if isinstance(child, Tag):
            if child.name == "section":
                flush()
                groups.append([child])
                continue
            if child.name in HEADING_TAGS:
                flush()
                current = [child]
                continue
        current.append(child)
    flush()
    return groups


def _first_heading_text(nodes: list[Tag | NavigableString]) -> str:
    for node in nodes:
        if isinstance(node, Tag):
            if node.name in HEADING_TAGS:
                return _element_text(node) or "section"
            inner = node.find(HEADING_TAGS)
            if inner:
                return _element_text(inner) or "section"
    return "intro"


def _group_to_section(
    soup: BeautifulSoup,
    nodes: list[Tag | NavigableString],
    ordinal: int,
) -> Section:
    is_section_tag = (
        len(nodes) == 1 and isinstance(nodes[0], Tag) and nodes[0].name == "section"
    )
    if is_section_tag:
        section_tag = nodes[0]
        assert isinstance(section_tag, Tag)
        heading_el = section_tag.find(HEADING_TAGS)
        heading = _element_text(heading_el) if heading_el else "section"
        direct_edit = section_tag.get(DIRECT_EDIT_ATTR) == "true"
        derived = section_tag.get(DERIVED_ATTR) == "true"
        sid = section_tag.get(SECTION_ID_ATTR) or _section_id(heading, ordinal)
        section_tag[SECTION_ID_ATTR] = sid
        html = str(section_tag)
        text = section_tag.get_text(" ", strip=True)
        return Section(
            id=sid,
            heading=heading,
            html=html,
            text=text,
            ordinal=ordinal,
            direct_edit=direct_edit,
            derived=derived,
        )

    heading = _first_heading_text(nodes)
    sid = _section_id(heading, ordinal)
    wrapper = soup.new_tag("section", attrs={SECTION_ID_ATTR: sid})
    for node in nodes:
        if isinstance(node, Tag):
            wrapper.append(node.extract() if node.parent else node)
        else:
            wrapper.append(NavigableString(str(node)))
    html = str(wrapper)
    text = wrapper.get_text(" ", strip=True)
    return Section(
        id=sid,
        heading=heading,
        html=html,
        text=text,
        ordinal=ordinal,
        direct_edit=False,
        derived=False,
    )


def _extract_meta(soup: BeautifulSoup) -> tuple[str, list[str], str | None, str | None]:
    title_el = soup.find("title")
    title = _element_text(title_el).strip() if title_el else "Untitled"

    created: str | None = None
    updated: str | None = None
    tags: list[str] = []

    for meta in soup.find_all("meta"):
        name = (meta.get("name") or "").lower()
        content = meta.get("content") or ""
        if name == "notes:created":
            created = content.strip() or None
        elif name == "notes:updated":
            updated = content.strip() or None
        elif name == "notes:tags":
            tags = [t.strip() for t in content.split(",") if t.strip()]

    return title, tags, created, updated


def parse_html(html: str) -> ParsedPage:
    """Parse a full HTML document into a ParsedPage with stable section IDs.

    This function mutates the parsed document so that every section has a
    data-section-id. Call `parsed.full_html` to get the canonical form to
    write back to disk if the mutation matters (it does after a fresh create).
    """
    soup = BeautifulSoup(html or "<!doctype html><html><head></head><body></body></html>", "html.parser")
    if soup.find("html") is None:
        wrapped = BeautifulSoup(
            f"<!doctype html><html><head></head><body>{html}</body></html>",
            "html.parser",
        )
        soup = wrapped

    body = soup.find("body")
    if body is None:
        body = soup.new_tag("body")
        html_tag = soup.find("html")
        if html_tag:
            html_tag.append(body)

    title, tags, created, updated = _extract_meta(soup)

    # Build section groups from the original body, then replace the body
    # contents with the (possibly wrapped) sections so IDs are persisted.
    assert isinstance(body, Tag)
    groups = _split_body_into_sections(body)
    sections: list[Section] = []

    # We rebuild the body by appending wrapper sections; this drops the old
    # top-level children but preserves all content because _group_to_section
    # extracts them into the wrapper.
    rebuilt = soup.new_tag("body")
    # Copy attributes
    for k, v in body.attrs.items():
        rebuilt[k] = v

    for ordinal, group in enumerate(groups):
        section = _group_to_section(soup, group, ordinal)
        sections.append(section)
        frag = BeautifulSoup(section.html, "html.parser")
        rebuilt.append(frag)

    body.replace_with(rebuilt)
    body = rebuilt

    body_html = body.decode_contents()
    full_html = str(soup)

    return ParsedPage(
        title=title,
        tags=tags,
        created=created,
        updated=updated,
        sections=sections,
        body_html=body_html,
        full_html=full_html,
    )


def validate_html(html: str) -> tuple[bool, str | None]:
    """Best-effort validation. Returns (ok, error_message)."""
    if not html or not html.strip():
        return False, "empty html"
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:  # pragma: no cover - bs4 is very forgiving
        return False, f"parse error: {exc}"
    if soup.find("title") is None:
        return False, "missing <title>"
    if soup.find("body") is None:
        return False, "missing <body>"
    return True, None


def build_page_html(
    title: str,
    body: str,
    *,
    tags: list[str] | None = None,
    created: str | None = None,
    updated: str | None = None,
) -> str:
    """Build a fresh canonical HTML document with notes meta tags."""
    tags = tags or []
    meta_parts = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
    ]
    if created:
        meta_parts.append(f'<meta name="notes:created" content="{created}">')
    if updated:
        meta_parts.append(f'<meta name="notes:updated" content="{updated}">')
    if tags:
        meta_parts.append(f'<meta name="notes:tags" content="{",".join(tags)}">')
    head = "\n    ".join(meta_parts)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        f"    {head}\n"
        f"    <title>{title}</title>\n"
        "  </head>\n"
        "  <body>\n"
        f"{body}\n"
        "  </body>\n"
        "</html>\n"
    )
