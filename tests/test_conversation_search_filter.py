"""Tests for web-search result filtering (ported from Dify code nodes)."""

from __future__ import annotations

from app.domain.conversation.search_filter import filter_by_relevance, filter_by_time


def _items():
    return [
        {"title": "特斯拉 股价 跌幅", "content": "2026年3月特斯拉股价下跌", "url": "u1"},
        {"title": "无关内容", "content": "今天天气不错", "url": "u2"},
        {"title": "Qwen 推理能力 对比", "content": "DeepSeek Qwen 推理 基准测试", "url": "u3"},
    ]


def test_filter_by_relevance_returns_text_and_ranks():
    out = filter_by_relevance(_items(), "特斯拉 股价 跌幅")
    assert "特斯拉" in out
    assert "标题" in out
    assert "相关度得分" in out
    # Higher-relevance item (特斯拉) should appear before the unrelated one.
    assert out.index("特斯拉") < out.index("无关内容")


def test_filter_by_relevance_empty_returns_message():
    assert "未获取到" in filter_by_relevance([], "kw")


def test_filter_by_time_returns_text():
    out = filter_by_time(_items(), max_items=2)
    assert "标题" in out
    assert "链接" in out
    # Only the first 2 items kept.
    assert out.count("标题：") == 2


def test_filter_by_time_empty_returns_empty_string():
    assert filter_by_time([]) == ""
