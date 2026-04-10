"""Tests for src/pages/parser.py."""
from __future__ import annotations

from src.pages.parser import build_page_html, parse_html, validate_html


def test_parse_simple_page_has_title_and_sections():
    html = build_page_html(
        "My notes",
        "<h1>Intro</h1><p>hi</p><h2>Details</h2><p>more</p>",
        tags=["demo"],
        created="2026-04-10T00:00:00+00:00",
        updated="2026-04-10T00:00:00+00:00",
    )
    parsed = parse_html(html)
    assert parsed.title == "My notes"
    assert parsed.tags == ["demo"]
    assert parsed.created == "2026-04-10T00:00:00+00:00"
    assert len(parsed.sections) == 2
    assert parsed.sections[0].heading == "Intro"
    assert parsed.sections[1].heading == "Details"
    # every section has a stable id
    for section in parsed.sections:
        assert section.id.startswith("s-")


def test_section_ids_are_stable_across_reparse():
    html = build_page_html(
        "T",
        "<h1>A</h1><p>1</p><h1>B</h1><p>2</p>",
    )
    first = parse_html(html)
    ids1 = [s.id for s in first.sections]
    # Write the full_html back and reparse — ids must match exactly
    second = parse_html(first.full_html)
    ids2 = [s.id for s in second.sections]
    assert ids1 == ids2


def test_direct_edit_attribute_is_captured():
    html = (
        '<!doctype html><html><head><title>T</title></head><body>'
        '<section data-direct-edit="true"><h1>Locked</h1><p>keep me</p></section>'
        '<section><h1>Free</h1><p>change me</p></section>'
        '</body></html>'
    )
    parsed = parse_html(html)
    assert parsed.sections[0].direct_edit is True
    assert parsed.sections[1].direct_edit is False


def test_derived_attribute_is_captured():
    html = (
        '<!doctype html><html><head><title>T</title></head><body>'
        '<section data-derived="true"><h1>Gen</h1><p>x</p></section>'
        '</body></html>'
    )
    parsed = parse_html(html)
    assert parsed.sections[0].derived is True


def test_pre_heading_content_becomes_intro_section():
    html = (
        '<!doctype html><html><head><title>T</title></head><body>'
        '<p>floating text</p>'
        '<h1>Heading</h1><p>body</p>'
        '</body></html>'
    )
    parsed = parse_html(html)
    # "floating text" becomes its own intro section
    assert len(parsed.sections) == 2
    assert "floating text" in parsed.sections[0].text
    assert parsed.sections[1].heading == "Heading"


def test_validate_html_accepts_good_and_rejects_bad():
    good = build_page_html("T", "<p>ok</p>")
    assert validate_html(good) == (True, None)
    ok, err = validate_html("")
    assert not ok and err
    ok, err = validate_html("<p>no title</p>")
    assert not ok and "title" in (err or "")


def test_meta_tags_parse_notes_fields():
    html = build_page_html(
        "T", "<h1>X</h1>",
        tags=["a", "b", "c"],
        created="2026-01-01T00:00:00Z",
        updated="2026-04-10T00:00:00Z",
    )
    parsed = parse_html(html)
    assert parsed.tags == ["a", "b", "c"]
    assert parsed.created == "2026-01-01T00:00:00Z"
    assert parsed.updated == "2026-04-10T00:00:00Z"


def test_section_index_summary():
    html = build_page_html("T", "<h1>One</h1><p>body content for one</p>")
    parsed = parse_html(html)
    idx = parsed.section_index()
    assert len(idx) == 1
    entry = idx[0]
    assert entry["heading"] == "One"
    assert entry["ordinal"] == 0
    assert "body content" in entry["preview"]
    assert entry["direct_edit"] is False
