"""Process-wide shared runtime resources for the conversation workflow.

These are singletons shared across all concurrent conversation turns:

- **LLM clients** (``ChatOpenAI``): each wraps an ``httpx.AsyncClient`` whose
  connection pool is coroutine-safe and reusable. Sharing one instance means
  TCP connections to vLLM are pooled and reused across users, instead of being
  re-handshaked on every request (which is what happened when the pipeline
  ``new``-ed three clients per turn).

- **``asyncio.Semaphore``** per model: bounds in-flight LLM calls so a burst of
  users cannot exhaust vLLM or balloon memory. The (N+1)-th caller simply
  awaits a slot; on cancel or completion the slot is released (``async with``
  unwinds). The 8B pool is shared by keyword extraction + intent enhancement;
  the 35B pool gates the streaming answer.

- **Dedicated retrieval ``ThreadPoolExecutor``**: the RAG ``retriever.get_response``
  is synchronous/blocking (embedding + vector store). Running it on the default
  asyncio executor would let conversation retrieval starve — and be starved by
  — every other ``to_thread`` / ``run_in_executor`` call in the process. A
  dedicated bounded pool isolates it with its own backpressure: the pool's
  ``max_workers`` is the hard cap on concurrent retrievals; extras queue.

``ChatOpenAI`` holds **no conversation state** — the messages are passed per
``ainvoke``/``astream`` call and discarded afterward — so sharing the instance
across users does not mix conversations (same model as a shared DB engine).
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.adapters.conversation.llm import (
    make_answer_llm,
    make_intent_llm,
    make_keyword_llm,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# --- LLM singletons -------------------------------------------------------
# ChatOpenAI construction is side-effect-free (no connection is opened until the
# first request), so building these at import is safe. They live for the whole
# process and are shared by every conversation turn.
KEYWORD_LLM: ChatOpenAI = make_keyword_llm()
INTENT_LLM: ChatOpenAI = make_intent_llm()
ANSWER_LLM: ChatOpenAI = make_answer_llm()

# --- Per-model concurrency caps -------------------------------------------
# 8B (keyword + intent): calls are short and non-streaming — allow more.
# 35B (streaming answer): each call holds a vLLM slot for the whole stream
# duration — keep tight to protect the heavy model and bound SSE memory.
SEM_8B: asyncio.Semaphore = asyncio.Semaphore(settings.CONVERSATION_8B_MAX_CONCURRENT)
SEM_35B: asyncio.Semaphore = asyncio.Semaphore(settings.CONVERSATION_35B_MAX_CONCURRENT)

# --- Dedicated retrieval thread pool --------------------------------------
# Isolates blocking RAG retrieval from the default asyncio executor. Its
# max_workers is the hard cap on concurrent retrievals; further submissions
# queue (backpressure), so a retrieval burst cannot exhaust process threads.
_RETRIEVAL_POOL: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=settings.CONVERSATION_RETRIEVAL_MAX_WORKERS,
    thread_name_prefix="conv_retrieval_",
)


def run_retrieval_sync(fn: Callable[..., T], *args: object) -> "asyncio.Future[T]":
    """Schedule a blocking retrieval call on the dedicated pool.

    Returns the awaitable from ``loop.run_in_executor`` so callers can
    ``await run_retrieval_sync(self._retrieve, q, instance_id)``.
    """
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(_RETRIEVAL_POOL, fn, *args)


def shutdown_conversation_runtime() -> None:
    """Release the retrieval pool on application shutdown.

    The LLM ``httpx`` clients hold no threads; they are reclaimed by process
    exit. The retrieval pool holds non-daemon worker threads that would block
    exit, so it must be shut down explicitly — mirroring the other pools in
    ``main.py`` lifespan.
    """
    try:
        _RETRIEVAL_POOL.shutdown(wait=False, cancel_futures=True)
        logger.info("[success] 对话检索线程池已关闭")
    except Exception as e:  # graceful — shutdown must not abort sibling cleanup
        logger.warning("关闭对话检索线程池时出错: %s", e)
