"""Web search result filtering — ported verbatim from the Dify
``搜索筛选 (1)`` (relevance scoring) and ``搜索内容筛选`` (time-based) code nodes.

These run on raw search results produced by the web-search adapter (Tavily,
replacing the original SearXNG plugin). The output is a simplified text block
consumed by the answering LLM as ``{search_results}``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List

# ---------------------- relevance-scoring helpers (搜索筛选 (1)) ----------------------


def _clean_text(text: str) -> str:
    """Clean text: strip punctuation, lowercase, keep sales symbols (￥、%)."""
    if not isinstance(text, str):
        text = str(text) if text else ""
    text = text.strip().lower()
    text = re.sub(r"[^一-龥a-zA-Z0-9￥%]", "", text)
    return text


_SALES_STOP_WORDS = {"的", "了", "呢", "吗", "吧", "请问", "想", "了解", "咨询", "有没有", "能不能"}


def _get_core_sales_keywords(keyword: str) -> List[str]:
    """Extract sales core keywords (filter meaningless stop words)."""
    core_words = [
        _clean_text(w)
        for w in keyword.split()
        if _clean_text(w) and _clean_text(w) not in _SALES_STOP_WORDS
    ]
    return list(set(core_words))


_SALES_SYNONYM_MAP: Dict[str, List[str]] = {
    "报价": ["底价", "报价单", "价格", "售价", "定价"],
    "成交": ["签单", "下单", "购买", "订购"],
    "优惠": ["折扣", "满减", "立减", "返利", "促销"],
    "库存": ["现货", "备货", "有货", "缺货"],
    "型号": ["款", "系列", "规格", "配置"],
    "客户": ["意向客户", "精准客户", "大客户"],
}


def _get_sales_synonyms(word: str) -> List[str]:
    return _SALES_SYNONYM_MAP.get(word, [])


def _calculate_relevance_score(
    item: Dict[str, Any], core_keywords: List[str], extended_keywords: List[str]
) -> int:
    score = 0
    title = _clean_text(item.get("title", ""))
    content = _clean_text(item.get("content", ""))
    item_text = f"{title}{content}"

    matched_core = sum(1 for word in core_keywords if word in item_text)
    score += min(matched_core, 4)

    if any(ek in item_text for ek in extended_keywords):
        score += 2

    if any(word in title for word in core_keywords):
        score += 2
    elif any(word in content for word in core_keywords):
        score += 1

    product_words = [
        w
        for w in extended_keywords
        if any(x in w for x in ["款", "系列", "型号", "iPhone", "华为"])
    ]
    price_words = [
        w
        for w in extended_keywords
        if any(x in w for x in ["报价", "价格", "￥", "优惠", "折扣"])
    ]
    has_product = any(p in item_text for p in product_words) if product_words else False
    has_price = any(p in item_text for p in price_words) if price_words else False
    if has_product and has_price:
        score += 2

    return min(score, 10)


def _parse_time_from_content(content: str) -> datetime:
    time_pattern = r"(\d{4}年\d{1,2}月\d{1,2}日)"
    match = re.search(time_pattern, content)
    if match:
        time_str = (
            match.group(1).replace("年", "-").replace("月", "-").replace("日", "")
        )
        try:
            return datetime.strptime(time_str, "%Y-%m-%d")
        except Exception:
            pass
    if "天之前" in content:
        return datetime.now()
    return datetime(1970, 1, 1)


def filter_by_relevance(
    searxng_data: Any, keyword: str, max_items: int = 50, content_max_len: int = 200
) -> str:
    """Port of ``搜索筛选 (1)``: relevance-score sort, up to 50 items."""
    if isinstance(searxng_data, list):
        raw_items = searxng_data
    elif isinstance(searxng_data, dict):
        raw_items = searxng_data.get("json", [])
    elif isinstance(searxng_data, str):
        try:
            parsed = json.loads(searxng_data)
            raw_items = parsed.get("json", []) if isinstance(parsed, dict) else []
        except Exception:
            raw_items = []
    else:
        raw_items = []

    if not raw_items:
        return "未获取到SearXNG搜索结果"

    core_keywords = _get_core_sales_keywords(keyword)
    extended_keywords = core_keywords.copy()
    for word in core_keywords:
        extended_keywords.extend(_get_sales_synonyms(word))
    extended_keywords = list(set(extended_keywords))

    scored_items: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = item.get("title", "").strip()
        content = item.get("content", "").strip()
        url = item.get("url", "").strip()

        if len(content) > content_max_len:
            content = content[:content_max_len] + "..."

        publish_time = _parse_time_from_content(content)
        relevance_score = _calculate_relevance_score(
            item, core_keywords, extended_keywords
        )

        scored_items.append(
            {
                "title": title,
                "content": content,
                "url": url,
                "publish_time": publish_time,
                "relevance_score": relevance_score,
            }
        )

    scored_items.sort(key=lambda x: x["relevance_score"], reverse=True)
    final_items = (
        scored_items[:max_items] if len(scored_items) >= max_items else scored_items
    )

    result_lines = []
    for idx, item in enumerate(final_items, 1):
        result_lines.append(
            f"{idx}. 标题：{item['title']}\n内容：{item['content']}\n链接：{item['url']}\n相关度得分：{item['relevance_score']}\n"
        )
    return "\n".join(result_lines)


def filter_by_time(
    searxng_data: Any, max_items: int = 15, content_max_len: int = 200
) -> str:
    """Port of ``搜索内容筛选``: time-descending sort, up to 15 items."""
    if isinstance(searxng_data, list):
        raw_items = searxng_data
    else:
        raw_items = searxng_data.get("json", []) if isinstance(searxng_data, dict) else []

    if not raw_items:
        return ""

    clean_items: List[Dict[str, Any]] = []
    for item in raw_items:
        title = item.get("title", "").strip()
        content = item.get("content", "").strip()
        url = item.get("url", "").strip()

        if len(content) > content_max_len:
            content = content[:content_max_len] + "..."

        publish_time = _parse_time_from_content(content)
        clean_items.append(
            {"title": title, "content": content, "url": url, "publish_time": publish_time}
        )

    clean_items.sort(key=lambda x: x["publish_time"], reverse=True)
    clean_items = clean_items[:max_items]

    result_lines = []
    for idx, item in enumerate(clean_items, 1):
        result_lines.append(
            f"{idx}. 标题：{item['title']}\n内容：{item['content']}\n链接：{item['url']}\n"
        )
    return "\n".join(result_lines)
