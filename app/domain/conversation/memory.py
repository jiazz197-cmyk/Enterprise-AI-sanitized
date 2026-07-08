"""Memory assembly — ported from Dify ``双通记忆转换`` and ``模板转换`` nodes.

The conversation workflow maintains two conversation variables:
- ``long_memory``    — long-term compressed summaries (latest one).
- ``recent_dialogs`` — recent raw dialog turns (``用户：...\\n助手：...``).

These functions are pure (no IO) and unit-tested directly.
"""

from __future__ import annotations

from typing import Iterable, List


def clean_background(background: object) -> str:
    """Port of the ``background 清洗器`` code node: strip whitespace."""
    return "" if background is None else str(background).strip()


def format_dialog_line(query: str, answer: str) -> str:
    """Port of the ``模板转换`` node: one recent-dialog entry."""
    return f"用户：{query}\n助手：{answer}"


def _non_empty(items: Iterable[str]) -> List[str]:
    return [s for s in items if isinstance(s, str) and s.strip()]


def assemble_dual_memory(
    long_memory: Iterable[str], recent_dialogs: Iterable[str], user_query: str
) -> str:
    """Port of the ``双通记忆转换`` template node.

    Renders the structured memory context consumed by the answering LLM:

    ```
    ------------------- 长期摘要记忆 -------------------
    <long_memory items>
    --------------------------------------------------
    ------------------- 近期原始对话 -------------------
    <recent_dialogs items>
    --------------------------------------------------
    当前用户问题：<user_query>
    ```

    Empty sections are omitted, matching the Jinja ``{% if ... | length > 0 %}``
    guards. Items that are blank-after-trim are skipped, matching the
    ``{% if item | trim | length > 0 %}`` filter.
    """
    long_items = _non_empty(long_memory)
    recent_items = _non_empty(recent_dialogs)

    parts: List[str] = []

    if long_items:
        parts.append("------------------- 长期摘要记忆 -------------------")
        parts.extend(long_items)
        parts.append("--------------------------------------------------")

    if recent_items:
        parts.append("------------------- 近期原始对话 -------------------")
        parts.extend(recent_items)
        parts.append("--------------------------------------------------")

    parts.append(f"当前用户问题：{user_query}")
    return "\n".join(parts)


def should_override_memory(clean_background_value: object) -> bool:
    """Port of the ``判断是否覆盖记忆`` if-else branch logic.

    In Dify, the ``true`` branch (background empty) skipped the memory overwrite;
    the ``false`` branch (background non-empty) cleared long_memory +
    recent_dialogs and wrote ``background`` into long_memory. So override
    happens when background is **non-empty** (after stripping).
    """
    return bool(clean_background(clean_background_value))
