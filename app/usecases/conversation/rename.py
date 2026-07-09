"""UseCase: rename a conversation."""

from __future__ import annotations

from dataclasses import dataclass

from app.ports.outbound.conversation import ConversationRepoPort
from app.ports.dto.conversation import ConversationListItem


@dataclass
class RenameConversationCommand:
    conversation_id: str
    name: str
    auto_generate: bool = False


class RenameConversationUseCase:
    def __init__(self, repo: ConversationRepoPort):
        self._repo = repo

    async def execute(self, cmd: RenameConversationCommand) -> ConversationListItem:
        return await self._repo.rename_conversation(
            conversation_id=cmd.conversation_id,
            name=cmd.name,
            auto_generate=cmd.auto_generate,
        )
