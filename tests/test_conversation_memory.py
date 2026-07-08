"""Tests for conversation domain pure functions (memory + think stripping)."""

from __future__ import annotations

from app.domain.conversation.memory import (
    assemble_dual_memory,
    clean_background,
    format_dialog_line,
    should_override_memory,
)
from app.domain.conversation.think_strip import ThinkStreamFilter, strip_think


# ---------------------------------------------------------------------------
# clean_background / should_override_memory
# ---------------------------------------------------------------------------


def test_clean_background_strips_whitespace():
    assert clean_background("  hello  ") == "hello"
    assert clean_background(None) == ""
    assert clean_background("\t\n") == ""


def test_should_override_memory_only_when_non_empty():
    assert should_override_memory("背景") is True
    assert should_override_memory("") is False
    assert should_override_memory("   ") is False  # cleaned to empty


# ---------------------------------------------------------------------------
# format_dialog_line
# ---------------------------------------------------------------------------


def test_format_dialog_line():
    assert format_dialog_line("你好", "你好呀") == "用户：你好\n助手：你好呀"


# ---------------------------------------------------------------------------
# assemble_dual_memory
# ---------------------------------------------------------------------------


def test_assemble_dual_memory_both_sections():
    out = assemble_dual_memory(
        ["摘要A", "摘要B"], ["用户：q1\n助手：a1", "用户：q2\n助手：a2"], "当前问题"
    )
    assert "长期摘要记忆" in out
    assert "近期原始对话" in out
    assert "摘要A" in out and "摘要B" in out
    assert "用户：q1" in out
    assert "当前用户问题：当前问题" in out


def test_assemble_dual_memory_empty_sections_omitted():
    out = assemble_dual_memory([], [], "q")
    assert "长期摘要记忆" not in out
    assert "近期原始对话" not in out
    assert "当前用户问题：q" in out


def test_assemble_dual_memory_skips_blank_items():
    out = assemble_dual_memory(["", "  ", "有效"], [], "q")
    assert "有效" in out
    # blank items should not produce extra blank lines between the markers
    assert out.count("有效") == 1


# ---------------------------------------------------------------------------
# strip_think
# ---------------------------------------------------------------------------


def test_strip_think_removes_think_block():
    text = "<think>reasoning here</think>最终答案"
    assert strip_think(text) == "最终答案"


def test_strip_think_keeps_after_last_close():
    text = "<think>first</think>中间<think>second</think>答案"
    assert strip_think(text) == "答案"


def test_strip_think_no_tags_unchanged():
    assert strip_think("普通文本") == "普通文本"


def test_strip_think_reasoning_prefix():
    text = "Thinking Process: blah\n好的，这是答案"
    assert strip_think(text) == "好的，这是答案"


# ---------------------------------------------------------------------------
# ThinkStreamFilter
# ---------------------------------------------------------------------------


def test_think_stream_filter_strips_think_span():
    f = ThinkStreamFilter()
    out = f.feed("<think>reason")
    assert out == ""
    out = f.feed("ing</think>real ")
    assert out == "real "
    out = f.feed("answer")
    assert out == "answer"
    assert f.flush() == ""


def test_think_stream_filter_passes_plain_text():
    f = ThinkStreamFilter()
    assert f.feed("hello ") == "hello "
    assert f.feed("world") == "world"
    assert f.flush() == ""


def test_think_stream_filter_holds_partial_open_tag():
    f = ThinkStreamFilter()
    # Feed a string ending with a prefix of "<think"; must not emit it yet.
    out = f.feed("text<thi")
    assert out == "text"
    out = f.feed("nk>hidden</think>visible")
    assert out == "visible"
    assert f.flush() == ""


def test_think_stream_filter_flush_releases_unterminated_literal():
    f = ThinkStreamFilter()
    out = f.feed("text<thin")
    assert out == "text"
    # No closing tag ever arrives; flush treats remainder as literal text.
    assert f.flush() == "<thin"
