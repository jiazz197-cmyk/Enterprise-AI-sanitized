"""DTOs for the conversation use cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


SearchMode = str  # "联网搜索" | "本地检索" | "本地&网络"


@dataclass
class RunConversationCommand:
    """Inputs for a single conversation turn (one user query)."""

    user_id: str
    query: str
    search_mode: SearchMode
    background: Optional[str] = None
    conversation_id: Optional[str] = None
    cancel_checker: Optional[object] = None


@dataclass
class ConversationSnapshot:
    """In-memory view of a conversation's memory state."""

    conversation_id: str
    owner_id: str
    name: str
    long_memory: List[str] = field(default_factory=list)
    recent_dialogs: List[str] = field(default_factory=list)


@dataclass
class ConversationMessage:
    """One persisted message row."""

    id: str
    conversation_id: str
    role: str  # "user" | "assistant"
    content: str
    created_at: object = None


@dataclass
class ConversationListItem:
    conversation_id: str
    name: str
    created_at: object = None
    updated_at: object = None


@dataclass
class WorkflowContext:
    """Inputs handed to the answering workflow (memory + profile + time).

    The UseCase assembles this; the workflow port consumes it. Memory here is
    the already-assembled dual-memory string (see
    ``app.domain.conversation.memory.assemble_dual_memory``).
    """

    query: str
    search_mode: SearchMode
    current_time: str
    user_profile: str
    memories: str
    cancel_checker: Optional[object] = None


@dataclass
class ConversationTurnResult:
    """Metadata produced after a turn completes (used for SSE message_end)."""

    conversation_id: str
    message_id: str
    answer: str


@dataclass
class ConversationStreamEvent:
    """Items yielded by ``RunConversationUseCase.execute``.

    ``type`` is one of ``"token"`` (streaming answer chunk), ``"done"`` (turn
    finished, ``result`` populated), or ``"error"`` (``error`` populated).
    The route layer translates these into Dify-shaped SSE bytes.

    ``conversation_id`` is the resolved conversation id (populated on every
    event from the moment the conversation is resolved), so the route can
    emit it in the *first* ``message`` SSE event — Dify-compatible behavior
    the frontend relies on (it reads ``conversation_id`` off any event).
    """

    type: str
    token: str = ""
    result: Optional[ConversationTurnResult] = None
    error: Optional[str] = None
    conversation_id: Optional[str] = None
