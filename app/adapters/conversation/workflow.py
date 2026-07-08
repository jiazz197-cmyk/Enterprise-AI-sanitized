"""Workflow adapter: bridges ``ConversationWorkflowPort`` to the langchain pipeline.

The adapter is the only layer that imports ``app.adapters.conversation``.
It owns the ``RetrieverPort`` and ``WebSearchPort`` instances (injected at
construction) and builds the ``ConversationPipeline``.
"""

from __future__ import annotations

from typing import AsyncIterator

from app.adapters.conversation.pipeline import ConversationPipeline
from app.ports.outbound.conversation import (
    ConversationWorkflowPort,
    WebSearchPort,
)
from app.ports.outbound.retriever import RetrieverPort
from app.ports.dto.conversation import WorkflowContext


class LangChainConversationWorkflowAdapter(ConversationWorkflowPort):
    """Run the langchain answering pipeline and stream think-stripped tokens."""

    def __init__(
        self,
        retrieval: RetrieverPort,
        web_search: WebSearchPort,
        pipeline: ConversationPipeline | None = None,
    ):
        self._retrieval = retrieval
        self._web_search = web_search
        self._pipeline = pipeline or ConversationPipeline(
            retrieval=retrieval, web_search=web_search
        )

    async def stream_answer(self, ctx: WorkflowContext) -> AsyncIterator[str]:
        async for token in self._pipeline.stream(ctx):
            yield token
