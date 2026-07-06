"""Conversation outbound ports (Protocol contracts)."""

from __future__ import annotations

from typing import Any, AsyncIterator, List, Optional, Protocol

from app.ports.dto.conversation import (
    ConversationListItem,
    ConversationMessage,
    ConversationSnapshot,
    WorkflowContext,
)


class ConversationRepoPort(Protocol):
    """Persistence boundary for conversations, messages, and memory.

    Replaces Dify's ownership of conversation variables (``long_memory`` /
    ``recent_dialogs``) and message history. Single source of truth shared by
    the conversation workflow, chat-summary, and context-compression.
    """

    async def get_or_create_conversation(
        self, owner_id: str, conversation_id: Optional[str], query: str
    ) -> ConversationSnapshot:
        ...

    async def load_snapshot(self, conversation_id: str) -> Optional[ConversationSnapshot]:
        ...

    async def override_memory(
        self, conversation_id: str, background: str
    ) -> ConversationSnapshot:
        """Clear long_memory + recent_dialogs and write background to long_memory."""
        ...

    async def append_message(
        self, conversation_id: str, role: str, content: str
    ) -> ConversationMessage:
        ...

    async def append_dialog_line(
        self, conversation_id: str, user_query: str, assistant_answer: str
    ) -> None:
        """Append a ``用户：…\\n助手：…`` line to recent_dialogs (memory only)."""
        ...

    async def list_conversations(
        self, owner_id: str, page: int, limit: int
    ) -> List[ConversationListItem]:
        ...

    async def list_messages(
        self, conversation_id: str, page: int, limit: int
    ) -> List[ConversationMessage]:
        ...

    async def rename_conversation(
        self, conversation_id: str, name: str, auto_generate: bool
    ) -> ConversationListItem:
        ...


class UserProfilePort(Protocol):
    """Read the user-habits summary (Dify ``获取用户习惯`` node, now in-process)."""

    async def get_latest_summary(self, user_id: str) -> Optional[str]:
        ...


class WebSearchPort(Protocol):
    """Web search boundary (replaces the Dify SearXNG plugin)."""

    async def search(self, query: str, time_range: str = "month") -> Any:
        """Return raw search results (list/dict) consumed by the filter helpers."""
        ...


class ConversationWorkflowPort(Protocol):
    """The langchain answering engine — streams the final answer token-by-token.

    ``<think>`` reasoning is stripped inside the implementation; consumers only
    receive answer tokens. Yields an empty stream + raises on unrecoverable
    errors.
    """

    async def stream_answer(self, ctx: WorkflowContext) -> AsyncIterator[str]:
        ...


class ConversationCancellationRegistry(Protocol):
    """In-memory registry for cooperative stop of a streaming turn."""

    def register(self, task_id: str) -> Any: ...
    def cancel(self, task_id: str) -> bool: ...
    def release(self, task_id: str) -> None: ...
