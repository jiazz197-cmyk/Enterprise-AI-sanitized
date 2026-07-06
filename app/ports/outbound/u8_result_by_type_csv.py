"""Port for exporting ``u8_result_by_type`` (grouped U8 BOM) to CSV and Excel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Protocol


@dataclass(frozen=True)
class U8ResultByTypeCsvExport:
    """CSV text per product type (one logical sub-table / sheet).

    Keys are safe table identifiers derived from each group's ``type`` label.
    Values are UTF-8 CSV documents (no BOM), including a header row when there
    is at least one data row.
    """

    tables: Dict[str, str]


@dataclass(frozen=True)
class U8ResultByTypeXlsxExport:
    """Single ``.xlsx`` workbook: one worksheet per ``type`` (same key order as ``tables``)."""

    content: bytes


class U8ResultByTypeCsvPort(Protocol):
    """Convert grouped U8 BOM JSON to per-type CSV strings and/or one multi-sheet workbook."""

    def export_csv_tables(self, u8_result_by_type: Mapping[str, Any]) -> U8ResultByTypeCsvExport:
        ...

    def export_xlsx_workbook(self, u8_result_by_type: Mapping[str, Any]) -> U8ResultByTypeXlsxExport:
        ...
