"""Tests for src/streaming.py helpers (no LLM needed)."""
from __future__ import annotations

import json

from src.streaming import summarize_tool_call


def _tool(name: str, args: dict) -> dict:
    return {
        "id": "call_1",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_summarize_edit_page_includes_target_and_instruction():
    s = summarize_tool_call(_tool("edit_page", {
        "page_id": "diary",
        "instruction": "add a section about lunch and link to today",
    }))
    assert s["name"] == "edit_page"
    assert "diary" in s["preview"]
    assert "lunch" in s["preview"]


def test_summarize_truncates_long_instruction():
    long_instr = "a" * 200
    s = summarize_tool_call(_tool("edit_page", {
        "page_id": "x",
        "instruction": long_instr,
    }))
    # Preview should be truncated to keep the chip short
    assert len(s["preview"]) < 100
    assert s["preview"].endswith("…")


def test_summarize_create_page():
    s = summarize_tool_call(_tool("create_page", {
        "title": "Project tracker",
        "instruction": "build a tracker with columns",
    }))
    assert "Project tracker" in s["preview"]


def test_summarize_search():
    s = summarize_tool_call(_tool("search", {"query": "postgres locks"}))
    assert "postgres locks" in s["preview"]


def test_summarize_list_pages_with_and_without_filter():
    bare = summarize_tool_call(_tool("list_pages", {}))
    assert bare["preview"] == "listing pages"

    filtered = summarize_tool_call(_tool("list_pages", {"tag": "work"}))
    assert "work" in filtered["preview"]


def test_summarize_data_tools():
    write = summarize_tool_call(_tool("write_data", {
        "page_id": "sales",
        "file": "q1.csv",
    }))
    assert "sales/q1.csv" in write["preview"]

    read = summarize_tool_call(_tool("read_data", {
        "page_id": "sales",
        "file": "q1.csv",
    }))
    assert "sales/q1.csv" in read["preview"]


def test_summarize_unknown_tool_falls_back_to_name():
    s = summarize_tool_call(_tool("custom_tool", {"x": 1}))
    assert s["preview"] == "custom_tool"


def test_summarize_handles_malformed_arguments():
    """If the LLM emitted malformed JSON for arguments, summarize
    should not raise — it should still return a valid dict with the
    tool name."""
    bad = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "edit_page", "arguments": "{not json"},
    }
    s = summarize_tool_call(bad)
    assert s["name"] == "edit_page"
    # No instruction parsed, so just the empty page id placeholder
    assert "?" in s["preview"]
