"""Persistent conversation + message models (replaces Dify's message store)."""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base
from app.core.time_utils import utcnow_naive


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Conversation(Base):
    """A chat conversation owning its memory (long_memory + recent_dialogs)."""

    __tablename__ = "conversations"

    id = Column(String(64), primary_key=True, default=_new_uuid)
    owner_id = Column(String(128), nullable=False, index=True)
    name = Column(String(256), nullable=False, default="新对话")
    # JSONB arrays of strings; JSONB indexes/queries better than JSON on PG.
    long_memory = Column(JSONB, nullable=False, default=list)
    recent_dialogs = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime, default=utcnow_naive, nullable=False)
    updated_at = Column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )


class Message(Base):
    """One user/assistant message in a conversation."""

    __tablename__ = "messages"

    id = Column(String(64), primary_key=True, default=_new_uuid)
    conversation_id = Column(
        String(64), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role = Column(String(16), nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False, default="")
    seq = Column(Integer, nullable=False, default=0)  # monotonic per-conversation order
    created_at = Column(DateTime, default=utcnow_naive, nullable=False, index=True)
