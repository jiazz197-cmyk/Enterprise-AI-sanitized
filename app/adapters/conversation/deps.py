"""Composition helpers for the conversation feature.

Factories wire adapters into use cases. The route layer calls these so it does
not import ``app.integrations`` (enforced by the architecture guard).
"""

from __future__ import annotations

from app.adapters.conversation.persistence import SqlAlchemyConversationRepoAdapter
from app.adapters.conversation.web_search import TavilyWebSearchAdapter
from app.adapters.conversation.workflow import LangChainConversationWorkflowAdapter
from app.adapters.retriever import RAGRetrieverAdapter
from app.adapters.chat_summary import UserProfileSummaryRepoAdapter
from app.ports.outbound.conversation import (
    ConversationRepoPort,
    ConversationWorkflowPort,
    UserProfilePort,
    WebSearchPort,
)
from app.ports.outbound.retriever import RetrieverPort


def build_conversation_repo() -> ConversationRepoPort:
    return SqlAlchemyConversationRepoAdapter()


def build_user_profile_port() -> UserProfilePort:
    # Reuses the existing chat-summary summary store (UserProfileDB).
    return UserProfileSummaryRepoAdapter()


def build_web_search_port() -> WebSearchPort:
    return TavilyWebSearchAdapter()


def build_workflow_port(
    retrieval: RetrieverPort, web_search: WebSearchPort
) -> ConversationWorkflowPort:
    return LangChainConversationWorkflowAdapter(retrieval=retrieval, web_search=web_search)


def build_retriever_port(rag_instance) -> RetrieverPort:
    """Wrap the shared RAG system (app.state.rag) behind the retriever port."""
    return RAGRetrieverAdapter(rag_instance=rag_instance)
