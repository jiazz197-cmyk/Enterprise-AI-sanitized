"""Conversation API — Dify-compatible chat endpoints backed by the langchain workflow.

Replaces the Dify advanced-chat app that the frontend previously proxied to.
Endpoints mirror Dify's wire format so the frontend SSE parser works unchanged:

- ``POST /chat-messages``              — streaming SSE answer (event: message / message_end / error)
- ``POST /chat-messages/{task_id}/stop`` — cooperative stop
- ``GET  /conversations``              — list conversations
- ``GET  /messages``                   — list messages in a conversation
- ``POST /conversations/{id}/name``    — rename

Mounted with empty prefix under ``/api/v1`` so paths are ``/api/v1/chat-messages``
etc., matching the frontend ``apiBaseUrl``.

This route is a "remediated" edge layer: it must NOT import ``app.integrations``
(enforced by ``scripts/check_layered_architecture.sh``). It assembles adapters
via ``app.adapters.conversation.deps`` and translates UseCase stream events into
Dify-shaped SSE bytes.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.adapters.conversation.deps import (
    build_conversation_repo,
    build_retriever_port,
    build_user_profile_port,
    build_web_search_port,
    build_workflow_port,
)
from app.core.config import settings
from app.core.dependencies import get_rag_instance
from app.core.security import get_current_user
from app.core.validators.conversation_id import validate_conversation_id
from app.ports.contracts.identity import CurrentUserPort
from app.usecases.conversation.list import (
    ListConversationsQuery,
    ListConversationsUseCase,
    ListMessagesQuery,
    ListMessagesUseCase,
)
from app.usecases.conversation.rename import (
    RenameConversationCommand,
    RenameConversationUseCase,
)
from app.usecases.conversation.run import RunConversationUseCase
from app.ports.dto.conversation import RunConversationCommand

logger = logging.getLogger(__name__)
router = APIRouter()

SEARCH_MODES = {"联网搜索", "本地检索", "本地&网络"}


# ---------------------------------------------------------------------------
# In-memory cooperative-cancel registry (per-process; lost on restart, like
# the short-lived ExecutorManager futures — see docs/task-state-truth.md).
# ---------------------------------------------------------------------------


class _CancelFlag:
    __slots__ = ("cancelled",)

    def __init__(self) -> None:
        self.cancelled = False

    def __call__(self) -> bool:
        return self.cancelled


_CANCEL_FLAGS: dict[str, _CancelFlag] = {}


def _register_cancel(task_id: str) -> _CancelFlag:
    flag = _CancelFlag()
    _CANCEL_FLAGS[task_id] = flag
    return flag


def _release_cancel(task_id: str) -> None:
    _CANCEL_FLAGS.pop(task_id, None)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ChatMessageInputs(BaseModel):
    search: Optional[str] = None
    user_id: Optional[str] = None
    token: Optional[str] = None
    background: Optional[str] = Field(default=None, max_length=65536)


class ChatMessageRequest(BaseModel):
    query: str
    user: Optional[str] = None
    user_id: Optional[str] = None
    search: str = "本地检索"
    inputs: ChatMessageInputs = Field(default_factory=ChatMessageInputs)
    conversation_id: Optional[str] = None
    response_mode: str = "streaming"
    token: Optional[str] = None  # legacy Dify field; ignored (auth via JWT)


class RenameConversationRequest(BaseModel):
    name: str
    auto_generate: bool = False


# ---------------------------------------------------------------------------
# SSE encoding
# ---------------------------------------------------------------------------


def _sse(event: dict) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


def _now_ts() -> int:
    return int(time.time())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _effective_user(current_user: CurrentUserPort) -> str:
    return (current_user.username or "").strip() or str(current_user.id)


@router.post("/chat-messages")
async def chat_messages(
    body: ChatMessageRequest,
    current_user: CurrentUserPort = Depends(get_current_user),
    rag_instance=Depends(get_rag_instance),
):
    """Stream a conversation answer as Dify-shaped SSE."""
    search_mode = body.search or body.inputs.search or "本地检索"
    if search_mode not in SEARCH_MODES:
        raise HTTPException(status_code=422, detail=f"invalid search mode: {search_mode}")

    conversation_id = body.conversation_id
    if conversation_id:
        try:
            conversation_id = validate_conversation_id(conversation_id)
        except Exception:
            raise HTTPException(status_code=422, detail="invalid conversation_id")

    background = body.inputs.background
    user_id = _effective_user(current_user)
    task_id = f"chat_{uuid.uuid4().hex[:24]}"
    cancel_flag = _register_cancel(task_id)

    repo = build_conversation_repo()
    workflow = build_workflow_port(
        retrieval=build_retriever_port(rag_instance),
        web_search=build_web_search_port(),
    )
    usecase = RunConversationUseCase(
        repo=repo,
        user_profile=build_user_profile_port(),
        workflow=workflow,
    )
    cmd = RunConversationCommand(
        user_id=user_id,
        query=body.query,
        search_mode=search_mode,
        background=background,
        conversation_id=conversation_id,
        cancel_checker=cancel_flag,
    )

    async def event_stream():
        try:
            async for ev in usecase.execute(cmd):
                if ev.type == "token":
                    yield _sse(
                        {
                            "event": "message",
                            "task_id": task_id,
                            "id": "",
                            # ev.conversation_id is set from the moment the
                            # conversation is resolved, so the FIRST message
                            # event carries the real id (even for new chats).
                            "conversation_id": ev.conversation_id or conversation_id or "",
                            "answer": ev.token,
                            "created_at": _now_ts(),
                        }
                    )
                elif ev.type == "done":
                    res = ev.result
                    yield _sse(
                        {
                            "event": "message_end",
                            "task_id": task_id,
                            "id": res.message_id if res else "",
                            "conversation_id": res.conversation_id if res else "",
                            "metadata": {
                                "usage": {
                                    "prompt_tokens": 0,
                                    "completion_tokens": 0,
                                    "total_tokens": 0,
                                },
                                "retriever_resources": [],
                            },
                        }
                    )
                elif ev.type == "error":
                    yield _sse(
                        {
                            "event": "error",
                            "task_id": task_id,
                            "code": "internal_error",
                            "message": ev.error or "internal error",
                            "status": 500,
                        }
                    )
        finally:
            _release_cancel(task_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat-messages/{task_id}/stop")
async def stop_chat_message(task_id: str):
    flag = _CANCEL_FLAGS.get(task_id)
    if flag is None:
        raise HTTPException(status_code=404, detail="task not found")
    flag.cancelled = True
    return {"result": "success"}


@router.get("/conversations")
async def list_conversations(
    user: str = Query(...),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    owner_id = _effective_user(current_user)
    usecase = ListConversationsUseCase(repo=build_conversation_repo())
    result = await usecase.execute(
        ListConversationsQuery(owner_id=owner_id, page=page, limit=limit)
    )
    return {
        "data": [
            {
                "id": c.conversation_id,
                "name": c.name,
                "inputs": {},
                "status": "normal",
                "introduction": "",
                "created_at": int(c.created_at.timestamp()) if c.created_at else 0,
                "updated_at": int(c.updated_at.timestamp()) if c.updated_at else 0,
            }
            for c in result.data
        ],
        "has_more": result.has_more,
        "limit": result.limit,
        "page": result.page,
    }


@router.get("/messages")
async def list_messages(
    user: str = Query(...),
    conversation_id: str = Query(...),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        conversation_id = validate_conversation_id(conversation_id)
    except Exception:
        raise HTTPException(status_code=422, detail="invalid conversation_id")

    usecase = ListMessagesUseCase(repo=build_conversation_repo())
    result = await usecase.execute(
        ListMessagesQuery(conversation_id=conversation_id, page=page, limit=limit)
    )
    return {
        "data": [
            {
                "id": m.id,
                "conversation_id": m.conversation_id,
                "query": m.content if m.role == "user" else "",
                "answer": m.content if m.role == "assistant" else "",
                "role": m.role,
                "created_at": int(m.created_at.timestamp()) if m.created_at else 0,
            }
            for m in result.data
        ],
        "has_more": result.has_more,
        "limit": result.limit,
        "page": result.page,
    }


@router.post("/conversations/{conversation_id}/name")
async def rename_conversation(
    conversation_id: str,
    body: RenameConversationRequest,
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        conversation_id = validate_conversation_id(conversation_id)
    except Exception:
        raise HTTPException(status_code=422, detail="invalid conversation_id")

    usecase = RenameConversationUseCase(repo=build_conversation_repo())
    item = await usecase.execute(
        RenameConversationCommand(
            conversation_id=conversation_id,
            name=body.name,
            auto_generate=body.auto_generate,
        )
    )
    return {
        "id": item.conversation_id,
        "name": item.name,
        "inputs": {},
        "status": "normal",
        "introduction": "",
        "created_at": int(item.created_at.timestamp()) if item.created_at else 0,
        "updated_at": int(item.updated_at.timestamp()) if item.updated_at else 0,
    }
