"""Adapter: context compression integration."""

from __future__ import annotations

from typing import Any

from app.core.exceptions import ExternalServiceError
from app.integrations.context_compression.context_compressor import (
    LlmEndpointMisconfiguredError,
    compress_context,
)
from app.ports.outbound.context_compression import ContextCompressorPort
from app.ports.outbound.conversation import ConversationRepoPort


class IntegrationContextCompressorAdapter(ContextCompressorPort):
    """Compresses context using the local conversation repo (no Dify)."""

    def __init__(self, conversation_repo: ConversationRepoPort):
        self._conversation_repo = conversation_repo

    async def compress(self, context_data: dict) -> Any:
        try:
            return await compress_context(context_data, self._conversation_repo)
        except LlmEndpointMisconfiguredError as e:
            raise ExternalServiceError("context_compression", str(e)) from e
