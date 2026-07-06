"""UseCase: run one conversation turn (streaming)."""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime
from typing import AsyncIterator, Callable, Optional
from zoneinfo import ZoneInfo

from app.domain.conversation.memory import (
    assemble_dual_memory,
    clean_background,
    should_override_memory,
)
from app.ports.outbound.conversation import (
    ConversationRepoPort,
    ConversationWorkflowPort,
    UserProfilePort,
)
from app.ports.dto.conversation import (
    ConversationStreamEvent,
    ConversationTurnResult,
    RunConversationCommand,
    WorkflowContext,
)

logger = logging.getLogger(__name__)

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _now_date_str() -> str:
    """Port of the Dify ``获取当前时间`` tool: ``%Y-%m-%d`` Asia/Shanghai."""
    return datetime.now(_SHANGHAI).strftime("%Y-%m-%d")


class RunConversationUseCase:
    """Orchestrate memory + workflow + persistence for one user query.

    The UseCase is the single place that knows the turn sequence:
    resolve conversation → apply background override → load user profile →
    assemble dual memory → stream the answer → persist messages + dialog line.
    It only depends on Ports + domain pure functions.
    """

    def __init__(
        self,
        repo: ConversationRepoPort,
        user_profile: UserProfilePort,
        workflow: ConversationWorkflowPort,
        now_provider: Optional[Callable[[], str]] = None,
    ):
        self._repo = repo
        self._user_profile = user_profile
        self._workflow = workflow
        self._now_provider = now_provider or _now_date_str

    async def execute(
        self, cmd: RunConversationCommand
    ) -> AsyncIterator[ConversationStreamEvent]:
        # 1. Resolve (or create) the conversation.
        snapshot = await self._repo.get_or_create_conversation(
            owner_id=cmd.user_id,
            conversation_id=cmd.conversation_id,
            query=cmd.query,
        )
        conversation_id = snapshot.conversation_id

        # 2. Background override (判断是否覆盖记忆).
        bg = clean_background(cmd.background)
        if should_override_memory(bg):
            snapshot = await self._repo.override_memory(conversation_id, bg)

        # 3. User profile / habits (获取用户习惯).
        user_profile = await self._resolve_profile(cmd.user_id)

        # 4. Assemble dual-memory context (双通记忆转换).
        memories = assemble_dual_memory(
            snapshot.long_memory, snapshot.recent_dialogs, cmd.query
        )

        # 5. Build the workflow context.
        ctx = WorkflowContext(
            query=cmd.query,
            search_mode=cmd.search_mode,
            current_time=self._now_provider(),
            user_profile=user_profile or "",
            memories=memories,
            cancel_checker=cmd.cancel_checker,
        )

        # 6. Persist the user message before streaming (so it survives a crash).
        try:
            await self._repo.append_message(conversation_id, "user", cmd.query)
        except Exception as e:  # persistence must not block the answer
            logger.warning("Failed to persist user message: %s", e)

        # 7. Stream the answer.
        collected: list[str] = []
        try:
            async for token in self._workflow.stream_answer(ctx):
                collected.append(token)
                yield ConversationStreamEvent(
                    type="token", token=token, conversation_id=conversation_id
                )
        except asyncio.CancelledError:
            await self._finalize(conversation_id, cmd.query, collected)
            yield ConversationStreamEvent(
                type="done",
                conversation_id=conversation_id,
                result=ConversationTurnResult(
                    conversation_id=conversation_id,
                    message_id="",
                    answer="".join(collected),
                ),
            )
            return
        except Exception as e:
            logger.exception("Conversation workflow failed")
            yield ConversationStreamEvent(
                type="error", error=str(e), conversation_id=conversation_id
            )
            return

        # 8. Persist assistant message + dialog line (近期对话存储).
        answer = "".join(collected)
        message_id = ""
        try:
            msg = await self._repo.append_message(conversation_id, "assistant", answer)
            message_id = msg.id
            await self._repo.append_dialog_line(conversation_id, cmd.query, answer)
        except Exception as e:
            logger.warning("Failed to persist assistant turn: %s", e)

        yield ConversationStreamEvent(
            type="done",
            conversation_id=conversation_id,
            result=ConversationTurnResult(
                conversation_id=conversation_id,
                message_id=message_id,
                answer=answer,
            ),
        )

    async def _resolve_profile(self, user_id: str) -> Optional[str]:
        """Fetch user-habits summary; tolerant of sync/async port impls."""
        try:
            summary = self._user_profile.get_latest_summary(user_id)
            if inspect.isawaitable(summary):
                summary = await summary
            return summary
        except Exception as e:
            logger.warning("Failed to load user profile for %s: %s", user_id, e)
            return None

    async def _finalize(
        self, conversation_id: str, query: str, collected: list[str]
    ) -> None:
        """On cancel: persist whatever answer was produced so far."""
        answer = "".join(collected)
        if not answer.strip():
            return
        try:
            await self._repo.append_message(conversation_id, "assistant", answer)
            await self._repo.append_dialog_line(conversation_id, query, answer)
        except Exception as e:
            logger.warning("Failed to finalize cancelled turn: %s", e)
