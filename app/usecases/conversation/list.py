"""UseCases: list conversations and messages (Dify-shaped pagination)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from app.ports.outbound.conversation import ConversationRepoPort
from app.ports.dto.conversation import ConversationListItem, ConversationMessage


@dataclass
class ListConversationsQuery:
    owner_id: str
    page: int = 1
    limit: int = 20


@dataclass
class ListConversationsResult:
    data: List[ConversationListItem]
    has_more: bool
    limit: int
    page: int


@dataclass
class ListMessagesQuery:
    conversation_id: str
    page: int = 1
    limit: int = 20


@dataclass
class ListMessagesResult:
    data: List[ConversationMessage]
    has_more: bool
    limit: int
    page: int


class ListConversationsUseCase:
    def __init__(self, repo: ConversationRepoPort):
        self._repo = repo

    async def execute(self, q: ListConversationsQuery) -> ListConversationsResult:
        # Fetch one extra to determine has_more.
        items = await self._repo.list_conversations(q.owner_id, q.page, q.limit + 1)
        has_more = len(items) > q.limit
        return ListConversationsResult(
            data=items[: q.limit], has_more=has_more, limit=q.limit, page=q.page
        )


class ListMessagesUseCase:
    def __init__(self, repo: ConversationRepoPort):
        self._repo = repo

    async def execute(self, q: ListMessagesQuery) -> ListMessagesResult:
        items = await self._repo.list_messages(q.conversation_id, q.page, q.limit + 1)
        has_more = len(items) > q.limit
        return ListMessagesResult(
            data=items[: q.limit], has_more=has_more, limit=q.limit, page=q.page
        )
