"""SqlAlchemy adapter for the conversation repo port.

Owns conversations + messages + memory, replacing Dify's conversation-variable
and message storage. Each method manages its own ``AsyncSessionLocal`` lifecycle
(background-worker style), so it can be called from the streaming route without
holding a request-scoped session open across the whole LLM stream.
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.time_utils import utcnow_naive
from app.domain.conversation.memory import format_dialog_line
from app.models.orm.conversation import Conversation, Message
from app.ports.dto.conversation import (
    ConversationListItem,
    ConversationMessage,
    ConversationSnapshot,
)

logger = get_logger("conversation.persistence")


def _to_snapshot(conv: Conversation) -> ConversationSnapshot:
    return ConversationSnapshot(
        conversation_id=conv.id,
        owner_id=conv.owner_id,
        name=conv.name,
        long_memory=list(conv.long_memory or []),
        recent_dialogs=list(conv.recent_dialogs or []),
    )


def _to_message_dto(msg: Message) -> ConversationMessage:
    return ConversationMessage(
        id=msg.id,
        conversation_id=msg.conversation_id,
        role=msg.role,
        content=msg.content,
        created_at=msg.created_at,
    )


class SqlAlchemyConversationRepoAdapter:
    """Implements ``ConversationRepoPort`` against PostgreSQL via SQLAlchemy."""

    async def get_or_create_conversation(
        self, owner_id: str, conversation_id: Optional[str], query: str
    ) -> ConversationSnapshot:
        async with AsyncSessionLocal() as db:
            conv: Optional[Conversation] = None
            if conversation_id:
                result = await db.execute(
                    select(Conversation).where(Conversation.id == conversation_id)
                )
                conv = result.scalars().first()
            if conv is None:
                conv = Conversation(
                    owner_id=owner_id,
                    name=(query[:48] + "…") if len(query) > 48 else (query or "新对话"),
                )
                db.add(conv)
                await db.flush()
            snapshot = _to_snapshot(conv)
            await db.commit()
            return snapshot

    async def load_snapshot(self, conversation_id: str) -> Optional[ConversationSnapshot]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conv = result.scalars().first()
            return _to_snapshot(conv) if conv else None

    async def override_memory(
        self, conversation_id: str, background: str
    ) -> ConversationSnapshot:
        """Clear long_memory + recent_dialogs, write background into long_memory."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conv = result.scalars().first()
            if conv is None:
                raise ValueError(f"conversation not found: {conversation_id}")
            conv.long_memory = [background] if background else []
            conv.recent_dialogs = []
            conv.updated_at = utcnow_naive()
            snapshot = _to_snapshot(conv)
            await db.commit()
            return snapshot

    async def append_message(
        self, conversation_id: str, role: str, content: str
    ) -> ConversationMessage:
        async with AsyncSessionLocal() as db:
            seq = await self._next_seq(db, conversation_id)
            msg = Message(
                conversation_id=conversation_id, role=role, content=content, seq=seq
            )
            db.add(msg)
            await db.flush()
            dto = _to_message_dto(msg)
            await db.commit()
            return dto

    async def append_dialog_line(
        self, conversation_id: str, user_query: str, assistant_answer: str
    ) -> None:
        """Append a dialog line to recent_dialogs (memory update only)."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conv = result.scalars().first()
            if conv is None:
                raise ValueError(f"conversation not found: {conversation_id}")
            recent = list(conv.recent_dialogs or [])
            recent.append(format_dialog_line(user_query, assistant_answer))
            conv.recent_dialogs = recent
            conv.updated_at = utcnow_naive()
            await db.commit()

    async def list_conversations(
        self, owner_id: str, page: int, limit: int
    ) -> List[ConversationListItem]:
        page = max(1, page)
        limit = max(1, min(limit, 100))
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Conversation)
                .where(Conversation.owner_id == owner_id)
                .order_by(Conversation.updated_at.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
            return [
                ConversationListItem(
                    conversation_id=c.id,
                    name=c.name,
                    created_at=c.created_at,
                    updated_at=c.updated_at,
                )
                for c in result.scalars().all()
            ]

    async def list_messages(
        self, conversation_id: str, page: int, limit: int
    ) -> List[ConversationMessage]:
        page = max(1, page)
        limit = max(1, min(limit, 100))
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.seq.asc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
            return [_to_message_dto(m) for m in result.scalars().all()]

    async def rename_conversation(
        self, conversation_id: str, name: str, auto_generate: bool
    ) -> ConversationListItem:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conv = result.scalars().first()
            if conv is None:
                raise ValueError(f"conversation not found: {conversation_id}")
            if not auto_generate and name:
                conv.name = name[:256]
            conv.updated_at = utcnow_naive()
            item = ConversationListItem(
                conversation_id=conv.id,
                name=conv.name,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
            )
            await db.commit()
            return item

    @staticmethod
    async def _next_seq(db, conversation_id: str) -> int:
        result = await db.execute(
            select(func.coalesce(func.max(Message.seq), -1)).where(
                Message.conversation_id == conversation_id
            )
        )
        current = result.scalar_one()
        return int(current) + 1
