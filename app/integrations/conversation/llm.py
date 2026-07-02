"""LLM factories for the conversation workflow (langchain ChatOpenAI).

All models are served by local vLLM behind OpenAI-compatible endpoints:
- 关键词拆分 / 问题分析 → Qwen3-8B (thinking disabled).
- 答案生成               → Qwen3.6-35B-A3B (thinking enabled, stripped downstream).
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.core.config import settings


def _no_think_extra_body() -> dict:
    """vLLM Qwen3 chat-template kwarg to disable thinking."""
    return {"chat_template_kwargs": {"enable_thinking": False}}


def make_keyword_llm(temperature: float = 0.7, max_tokens: int = 512) -> ChatOpenAI:
    """Qwen3-8B for search-keyword extraction (thinking disabled)."""
    return ChatOpenAI(
        base_url=settings.SECONDARY_LLM_API_URL,
        # Local vLLM leaves the key empty → "not-needed"; external providers set SECONDARY_LLM_API_KEY.
        api_key=settings.SECONDARY_LLM_API_KEY or "not-needed",
        model=settings.SECONDARY_LLM_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_body=_no_think_extra_body(),
    )


def make_intent_llm(temperature: float = 0.7, max_tokens: int = 512) -> ChatOpenAI:
    """Qwen3-8B for intent enhancement (thinking disabled)."""
    return ChatOpenAI(
        base_url=settings.SECONDARY_LLM_API_URL,
        api_key=settings.SECONDARY_LLM_API_KEY or "not-needed",
        model=settings.SECONDARY_LLM_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_body=_no_think_extra_body(),
    )


def make_answer_llm(temperature: float = 0.7) -> ChatOpenAI:
    """Qwen3.6-35B-A3B for final answer generation (streaming, thinking on).

    Thinking output (``<think>...</think>``) is stripped by the pipeline's
    ``ThinkStreamFilter`` before tokens reach the user.
    """
    return ChatOpenAI(
        base_url=settings.PRIMARY_LLM_API_URL,
        api_key=settings.PRIMARY_LLM_API_KEY or "not-needed",
        model=settings.PRIMARY_LLM_MODEL,
        temperature=temperature,
        streaming=True,
    )
