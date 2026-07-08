"""Shared quotation task purge: delegates to port."""

from __future__ import annotations

from typing import Any, Dict

from app.ports.outbound.quotation import QuotationTaskPurgePort


class PurgeQuotationTaskUseCase:
    """Purge a quotation task and all associated resources."""

    def __init__(self, purge_port: QuotationTaskPurgePort):
        self._purge = purge_port

    async def execute(self, task_id: str, *, allow_non_terminal: bool = False) -> Dict[str, Any]:
        return await self._purge.purge_task(task_id, allow_non_terminal=allow_non_terminal)


async def purge_quotation_task(
    task_id: str,
    *,
    purge_port: QuotationTaskPurgePort,
    allow_non_terminal: bool = False,
) -> Dict[str, Any]:
    """Convenience function that runs the purge via an injected port."""
    return await purge_port.purge_task(task_id, allow_non_terminal=allow_non_terminal)
