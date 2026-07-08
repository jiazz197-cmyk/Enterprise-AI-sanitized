"""OCR extraction helpers for quotation (used by OcrStructuredInfoAdapter only)."""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional

from app.domain.quotation.exceptions import QuotationPipelineCancelledError
from app.adapters.ocr.infoextraction import extract_info, extract_layout_info

CancelChecker = Optional[Callable[[], bool]]

_HALLUCINATION_KEEP_RATIO = 0.5


def check_cancel(cancel_checker: CancelChecker) -> None:
    if cancel_checker and cancel_checker():
        raise QuotationPipelineCancelledError("任务已取消")


def is_ocr_result_complete(info: Dict[str, Any]) -> bool:
    meta = info.get("meta")
    spec = info.get("spec")
    return isinstance(meta, dict) and bool(meta) and isinstance(spec, dict) and bool(spec)


def clean_ocr_text(text: str) -> str:
    original = text.strip()
    if not original:
        return ""
    cleaned_chars = [ch for ch in original if 0x20 <= ord(ch) < 0x7F]
    cleaned = re.sub(r"\s+", " ", "".join(cleaned_chars)).strip()
    if not cleaned:
        return ""
    if len(cleaned) / len(original) < _HALLUCINATION_KEEP_RATIO:
        return ""
    return cleaned


def clean_extracted_info(info: Any) -> Any:
    if isinstance(info, dict):
        return {key: clean_extracted_info(value) for key, value in info.items()}
    if isinstance(info, list):
        return [clean_extracted_info(item) for item in info]
    if isinstance(info, str):
        return clean_ocr_text(info)
    return info


def extract_structured_info_with_fallback(
    *,
    image_url: str,
    api_url: str,
    max_retries: int,
    cancel_checker: CancelChecker = None,
) -> Dict[str, Any]:
    retries = max(1, max_retries)
    last_info: Optional[Dict[str, Any]] = None
    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        check_cancel(cancel_checker)
        try:
            content = extract_layout_info(image_url, api_url, cancel_checker=cancel_checker)
            info = extract_info(content)
            last_info = info
            if is_ocr_result_complete(info):
                return info
        except QuotationPipelineCancelledError:
            raise
        except Exception as exc:
            last_error = exc

        if attempt < retries:
            time.sleep(2)

    if last_info is not None:
        return last_info
    if last_error is not None:
        raise last_error
    return {"meta": {}, "spec": {}}
