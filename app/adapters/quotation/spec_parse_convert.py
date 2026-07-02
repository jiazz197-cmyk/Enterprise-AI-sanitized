"""Adapter: parse_spec_sheet + convert_all for quotation Phase1.

Optionally applies remark-driven field adjustments between parse and convert:
if a remark is present in the content, a RemarkInterpreterPort (Qwen3.6) is
asked to produce {canonical_field: adjusted_value} pairs, which override the
parsed params. The whole remark stage is wrapped so any failure leaves the
original pipeline behavior untouched.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.core.config import settings
from app.domain.quotation.exceptions import QuotationPipelineCancelledError
from app.domain.quotation.remark_adjustment import (
    allowed_keys_for,
    apply_adjustments,
    collect_remark_text,
)
from app.integrations.pdm_matcher.spec_converter import convert_all, parse_spec_sheet
from app.ports.domains.quotation import (
    CancelChecker,
    RemarkInterpreterPort,
    SpecParseAndConvertPort,
)

logger = logging.getLogger(__name__)


class SpecParseAndConvertAdapter(SpecParseAndConvertPort):
    def __init__(
        self,
        remark_interpreter: Optional[RemarkInterpreterPort] = None,
        enabled: Optional[bool] = None,
    ):
        self._remark_interpreter = remark_interpreter
        # Capture the feature flag at construction (defaults to the global
        # setting) so tests can disable the stage without mutating global state.
        self._enabled = (
            settings.REMARK_LLM_INTERPRETER_ENABLED if enabled is None else enabled
        )

    def parse_and_convert(
        self,
        *,
        ocr_text: str,
        cancel_checker: CancelChecker = None,
    ) -> Dict[str, Any]:
        if cancel_checker and cancel_checker():
            raise QuotationPipelineCancelledError("任务已取消")

        params = parse_spec_sheet(ocr_text)
        params = self._apply_remark_adjustments(params, ocr_text, cancel_checker)

        specs = convert_all(params)
        keywords_payload = {"keywords": specs}

        return {
            "params": params,
            "specs": specs,
            "keywords_payload": keywords_payload,
        }

    def _apply_remark_adjustments(
        self,
        params: Dict[str, Any],
        ocr_text: str,
        cancel_checker: CancelChecker,
    ) -> Dict[str, Any]:
        """Run the remark-LLM stage. Never breaks Phase1 — logs and skips on error."""
        if self._remark_interpreter is None or not self._enabled:
            return params
        try:
            remark_text = collect_remark_text(params, ocr_text)
            if not remark_text:
                return params
            # Re-check cancellation right before the (potentially slow) LLM
            # call — the entry check in parse_and_convert may have gone stale
            # while parse_spec_sheet was running. The interpreter's own
            # cancel_checker() check degrades to {} rather than raising, so
            # without this guard a cancel that lands during the LLM call would
            # be swallowed and the pipeline would continue to convert_all.
            if cancel_checker and cancel_checker():
                raise QuotationPipelineCancelledError("任务已取消")
            adjustments = self._remark_interpreter.interpret(
                remark_text=remark_text,
                current_fields=params,
                cancel_checker=cancel_checker,
            )
            # And again after the LLM call, so a cancel that landed mid-call
            # propagates instead of running convert_all on a (possibly empty)
            # adjustments dict.
            if cancel_checker and cancel_checker():
                raise QuotationPipelineCancelledError("任务已取消")
            if adjustments:
                # Defense-in-depth: the interpreter contract (RemarkInterpreterPort)
                # already promises whitelist-filtered output, but we re-filter here so
                # a misbehaving/swapped implementation can NEVER inject unknown fields
                # or corrupt params. Keys lowercased; allowed set lowercased to match
                # case-insensitively. Non-string/empty values dropped.
                allowed = {str(k).strip().lower() for k in allowed_keys_for(params)}
                clean = {
                    str(k).strip().lower(): str(v).strip()
                    for k, v in adjustments.items()
                    if str(k).strip().lower() in allowed
                    and v is not None
                    and not isinstance(v, (dict, list))
                    and str(v).strip()
                }
                if clean:
                    logger.info("remark 调整应用: %s", clean)
                    return apply_adjustments(params, clean)
        except QuotationPipelineCancelledError:
            # Cancellation must propagate, not be swallowed by the generic catch.
            raise
        except Exception as exc:  # noqa: BLE001 — remark stage must never break Phase1
            logger.warning("remark 调整阶段异常，跳过: %s", exc, exc_info=True)
        return params
