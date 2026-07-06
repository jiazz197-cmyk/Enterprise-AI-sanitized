"""SQL Server query adapters."""

from __future__ import annotations

from typing import Any, Callable, Optional

from app.integrations.sqlserver.queries import run_pdm_bom_query, run_pdm_match_query, run_u8_bom_inventory_query
from app.ports.outbound.sqlserver_queries import PdmBomQueryPort, PdmMatchQueryPort, U8BomInventoryQueryPort


class U8BomInventoryQueryAdapter(U8BomInventoryQueryPort):
    def run(
        self,
        payload: Any,
        *,
        cancel_checker: Optional[Callable[[], bool]] = None,
        user_key: Optional[str] = None,
    ) -> Any:
        return run_u8_bom_inventory_query(
            payload, cancel_checker=cancel_checker, user_key=user_key
        )


class PdmBomQueryAdapter(PdmBomQueryPort):
    def run(
        self,
        payload: Any,
        *,
        cancel_checker: Optional[Callable[[], bool]] = None,
    ) -> Any:
        return run_pdm_bom_query(payload, cancel_checker=cancel_checker)


class PdmMatchQueryAdapter(PdmMatchQueryPort):
    def run(
        self,
        payload: Any,
        *,
        cancel_checker: Optional[Callable[[], bool]] = None,
    ) -> Any:
        return run_pdm_match_query(payload, cancel_checker=cancel_checker)
