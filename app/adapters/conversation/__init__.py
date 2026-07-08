"""Conversation adapters.

Lazy-loaded subpackage: importing ``app.adapters.conversation`` does not pull
langchain or the RAG system until a concrete adapter is referenced.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, List

__all__ = [
    "SqlAlchemyConversationRepoAdapter",
    "TavilyWebSearchAdapter",
    "LangChainConversationWorkflowAdapter",
]

_EXPORTS = {
    "SqlAlchemyConversationRepoAdapter": ".persistence",
    "TavilyWebSearchAdapter": ".web_search",
    "LangChainConversationWorkflowAdapter": ".workflow",
}


def __getattr__(name: str) -> Any:
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(mod, __name__), name)


def __dir__() -> List[str]:
    return sorted({*__all__, *globals().keys()})
