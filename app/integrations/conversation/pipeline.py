"""Langchain answering pipeline — the core of the migrated Dify workflow.

Three search-mode branches, each: keyword extraction (Qwen3-8B) → retrieval
and/or web search → intent enhancement (Qwen3-8B) → streaming answer
(Qwen3.6-35B-A3B) with ``<think>`` stripped.

This module lives in the integration layer: it may import langchain and
``app.domain``/``app.ragsystem``, but is constructed by the adapter layer
(``app.adapters.conversation.workflow``) which injects the ``RetrieverPort``
and ``WebSearchPort``. It never imports ``app.adapters`` or ``app.api``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from langchain_core.messages import AIMessageChunk
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.domain.conversation.prompts import (
    ANSWER_SYSTEM_LOCAL,
    ANSWER_SYSTEM_LOCAL_WEB,
    ANSWER_SYSTEM_WEB,
    ANSWER_USER_LOCAL,
    ANSWER_USER_LOCAL_WEB,
    ANSWER_USER_WEB,
    INTENT_SYSTEM,
    KEYWORD_SYSTEM,
)
from app.domain.conversation.search_filter import filter_by_relevance, filter_by_time
from app.domain.conversation.think_strip import ThinkStreamFilter
from app.integrations.conversation.runtime import (
    ANSWER_LLM,
    INTENT_LLM,
    KEYWORD_LLM,
    SEM_35B,
    SEM_8B,
    run_retrieval_sync,
)
from app.ports.outbound.retriever import RetrievalQuery, RetrieverPort
from app.ports.dto.conversation import WorkflowContext

logger = logging.getLogger(__name__)

SEARCH_WEB = "联网搜索"
SEARCH_LOCAL = "本地检索"
SEARCH_LOCAL_WEB = "本地&网络"

_INSTANCE_FORM = 1  # 本地数据库-表单数据 (primary)
_INSTANCE_DISCRETE = 2  # 离散知识查询 (supplementary)


class ConversationPipeline:
    """Orchestrates the three-branch answering workflow."""

    def __init__(
        self,
        retrieval: RetrieverPort,
        web_search,
        keyword_llm=None,
        intent_llm=None,
        answer_llm=None,
    ):
        self._retrieval = retrieval
        self._web_search = web_search
        # Default to the shared singletons (process-wide connection-pool reuse).
        # Tests may inject fakes here.
        self._keyword_llm = keyword_llm or KEYWORD_LLM
        self._intent_llm = intent_llm or INTENT_LLM
        self._answer_llm = answer_llm or ANSWER_LLM

    # -- public -----------------------------------------------------------

    async def stream(self, ctx: WorkflowContext) -> AsyncIterator[str]:
        """Yield answer tokens (think-stripped) for one conversation turn."""
        try:
            async for token in self._run_branch(ctx):
                yield token
        except asyncio.CancelledError:
            logger.info("Conversation stream cancelled for query: %s", ctx.query[:80])
            raise

    # -- branch dispatch --------------------------------------------------

    async def _run_branch(self, ctx: WorkflowContext) -> AsyncIterator[str]:
        keywords = await self._extract_keywords(ctx.query, ctx.current_time)

        if ctx.search_mode == SEARCH_WEB:
            async for t in self._stream_web(ctx, keywords):
                yield t
        elif ctx.search_mode == SEARCH_LOCAL:
            async for t in self._stream_local(ctx, keywords):
                yield t
        else:  # SEARCH_LOCAL_WEB (default)
            async for t in self._stream_local_web(ctx, keywords):
                yield t

    # -- LLM helpers ------------------------------------------------------

    async def _extract_keywords(self, query: str, current_time: str) -> str:
        prompt = ChatPromptTemplate.from_messages([("system", KEYWORD_SYSTEM)])
        chain = prompt | self._keyword_llm | StrOutputParser()
        async with SEM_8B:
            keywords = await chain.ainvoke({"query": query, "current_time": current_time})
        return (keywords or "").strip()

    async def _enhance_intent(
        self, query: str, keywords: str, current_time: str
    ) -> str:
        prompt = ChatPromptTemplate.from_messages([("system", INTENT_SYSTEM)])
        chain = prompt | self._intent_llm | StrOutputParser()
        async with SEM_8B:
            intent = await chain.ainvoke(
                {"query": query, "keywords": keywords, "current_time": current_time}
            )
        return (intent or "").strip()

    # -- retrieval helper -------------------------------------------------

    def _retrieve(self, question: str, instance_id: int) -> str:
        """Run local RAG retrieval; return the answer text (empty on failure)."""
        collection = f"doc_collection_{instance_id}"
        q = RetrievalQuery(
            question=question, collection_name=collection, instance_id=instance_id
        )
        try:
            result = self._retrieval.query_db(q)
            return (result.answer or "").strip()
        except Exception as e:  # retrieval must not break the whole turn
            logger.warning("Retrieval instance=%s failed: %s", instance_id, e)
            return ""

    async def _retrieve_async(self, question: str, instance_id: int) -> str:
        # Run the blocking RAG call on the dedicated retrieval pool instead of
        # the shared default asyncio executor, so conversation retrieval is
        # isolated and bounded by its own max_workers cap.
        return await run_retrieval_sync(self._retrieve, question, instance_id)

    # -- web search helper -----------------------------------------------

    async def _web_search_filtered(
        self, keywords: str, *, by_relevance: bool
    ) -> str:
        try:
            raw = await self._web_search.search(keywords, time_range="month")
        except Exception as e:
            logger.warning("Web search failed: %s", e)
            return ""
        if by_relevance:
            return filter_by_relevance(raw, keywords)
        return filter_by_time(raw)

    # -- branches ---------------------------------------------------------

    async def _stream_local_web(
        self, ctx: WorkflowContext, keywords: str
    ) -> AsyncIterator[str]:
        primary, search_results, supplementary, intent = await asyncio.gather(
            self._retrieve_async(keywords, _INSTANCE_FORM),
            self._web_search_filtered(keywords, by_relevance=False),
            self._retrieve_async(keywords, _INSTANCE_DISCRETE),
            self._enhance_intent(ctx.query, keywords, ctx.current_time),
        )
        user_prompt = ANSWER_USER_LOCAL_WEB.format(
            current_time=ctx.current_time,
            intent=intent,
            user_profile=ctx.user_profile,
            primary_source=primary,
            search_results=search_results,
            supplementary_source=supplementary,
            query=ctx.query,
            memories=ctx.memories,
        )
        async for t in self._stream_answer(ANSWER_SYSTEM_LOCAL_WEB, user_prompt, ctx):
            yield t

    async def _stream_web(
        self, ctx: WorkflowContext, keywords: str
    ) -> AsyncIterator[str]:
        search_results = await self._web_search_filtered(keywords, by_relevance=True)
        user_prompt = ANSWER_USER_WEB.format(
            current_time=ctx.current_time,
            user_profile=ctx.user_profile,
            search_results=search_results,
            query=ctx.query,
            memories=ctx.memories,
        )
        async for t in self._stream_answer(ANSWER_SYSTEM_WEB, user_prompt, ctx):
            yield t

    async def _stream_local(
        self, ctx: WorkflowContext, keywords: str
    ) -> AsyncIterator[str]:
        primary, supplementary, intent = await asyncio.gather(
            self._retrieve_async(keywords, _INSTANCE_FORM),
            self._retrieve_async(keywords, _INSTANCE_DISCRETE),
            self._enhance_intent(ctx.query, keywords, ctx.current_time),
        )
        user_prompt = ANSWER_USER_LOCAL.format(
            current_time=ctx.current_time,
            intent=intent,
            user_profile=ctx.user_profile,
            primary_source=primary,
            supplementary_source=supplementary,
            query=ctx.query,
            memories=ctx.memories,
        )
        async for t in self._stream_answer(ANSWER_SYSTEM_LOCAL, user_prompt, ctx):
            yield t

    # -- answer streaming -------------------------------------------------

    async def _stream_answer(
        self,
        system_template: str,
        user_prompt: str,
        ctx: WorkflowContext,
    ) -> AsyncIterator[str]:
        """Stream the answering LLM, stripping ``<think>`` spans.

        ``system_template`` carries the ``{memories}`` placeholder; the user
        prompt is fully rendered already.
        """
        messages = [
            ("system", system_template),
            ("human", user_prompt),
        ]
        # ChatPromptTemplate validates + formats the system template's {memories}.
        prompt = ChatPromptTemplate.from_messages(messages)
        formatted = await prompt.ainvoke({"memories": ctx.memories})
        think_filter = ThinkStreamFilter()

        # Gate the streaming answer: each turn holds a 35B vLLM slot for the
        # whole stream duration. On cancel/exception the `async with` releases
        # the slot as the stack unwinds.
        async with SEM_35B:
            async for chunk in self._answer_llm.astream(formatted):
                self._check_cancel(ctx.cancel_checker)
                text = chunk.content if isinstance(chunk, AIMessageChunk) else str(chunk)
                if not isinstance(text, str) or not text:
                    continue
                emitted = think_filter.feed(text)
                if emitted:
                    yield emitted

            tail = think_filter.flush()
            if tail:
                yield tail

    @staticmethod
    def _check_cancel(cancel_checker: Optional[object]) -> None:
        if cancel_checker is None:
            return
        # Cooperative cancel: a callable returning truthy means "stop".
        if callable(cancel_checker) and cancel_checker():
            raise asyncio.CancelledError("Conversation turn cancelled by client")
