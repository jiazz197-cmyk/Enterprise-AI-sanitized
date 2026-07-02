"""Primary-LLM adapter: interpret remark text into field-value adjustments.

Calls the OpenAI-compatible primary LLM (configured via PRIMARY_LLM_*)
synchronously (the Phase1 pipeline is synchronous). Graceful degradation is
mandatory: any failure — no model service, timeout, HTML error page,
unparseable output — returns an empty dict and the pipeline continues with the
original params. The whitelist filter in ``validate_and_reorganize`` is the
second line of defense: even a "successful" but hallucinated model output is
reduced to known canonical fields only.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.domain.quotation.remark_adjustment import (
    allowed_keys_for,
    validate_and_reorganize,
)
from app.ports.domains.quotation import CancelChecker, RemarkInterpreterPort

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You interpret free-text remarks on a machine spec sheet and decide which "
    "field values to ADJUST based on the remark. Output ONLY a JSON object "
    "mapping canonical field names to their adjusted values. Field names MUST "
    "be chosen from the allowed field names list. Values MUST be short "
    "lowercase strings matching the spec sheet vocabulary (e.g. surface=flat, "
    "degree=30, cable_length=5m, regulation=india_wm). Omit any field that "
    "needs no change. Output NO prose, NO markdown fences, NO explanation — "
    "only the JSON object."
)

_USER = (
    "Allowed field names: {allowed}\n\n"
    "Current field values (JSON):\n{current}\n\n"
    "Remark text:\n\"\"\"\n{remark}\n\"\"\"\n\n"
    "Return only the JSON object of adjustments."
)

# Soft caps so a pathological remark cannot blow up the prompt.
_MAX_CURRENT_JSON_CHARS = 2000
_MAX_REMARK_CHARS = 4000


def _fit_current_json(cur: Dict[str, Any], limit: int) -> str:
    """Serialize ``cur`` to JSON, trimming keys until it fits under ``limit`` chars.

    A naive ``json.dumps(cur)[:limit]`` can cut through a string literal or an
    escape sequence, leaving the prompt's "Current field values (JSON)" chunk
    unparseable for the model. Here we drop trailing keys one at a time and
    re-serialize, so the result is always valid JSON; worst case it collapses
    to ``{}`` when even a single value exceeds the limit.
    """
    keys = list(cur.keys())
    s = json.dumps(cur, ensure_ascii=False)
    while len(s) > limit and keys:
        keys.pop()
        s = json.dumps({k: cur[k] for k in keys}, ensure_ascii=False)
    return s


class QwenRemarkInterpreter(RemarkInterpreterPort):
    """Interpret remarks via the primary LLM. Never raises — returns {} on failure."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        request_timeout: Optional[float] = None,
    ):
        self.base_url = base_url or settings.PRIMARY_LLM_API_URL
        self.model_name = model_name or settings.PRIMARY_LLM_MODEL
        # Local vLLM leaves the key empty → ChatOpenAI still needs a non-empty
        # value, so fall back to "not-needed". External providers inject the
        # real key via settings.PRIMARY_LLM_API_KEY (or the api_key arg).
        self.api_key = api_key if api_key is not None else (settings.PRIMARY_LLM_API_KEY or "not-needed")
        self.temperature = temperature
        self.max_tokens = max_tokens if max_tokens is not None else settings.REMARK_LLM_MAX_TOKENS
        self.request_timeout = (
            request_timeout if request_timeout is not None else settings.REMARK_LLM_REQUEST_TIMEOUT
        )
        # Lazily constructed on first interpret() call: when the feature is
        # disabled or no remark is present, we never allocate a ChatOpenAI
        # client (and its httpx connection pool).
        self._llm = None

    def _ensure_llm(self):
        if self._llm is None:
            self._llm = ChatOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model_name,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                request_timeout=self.request_timeout,
            )
        return self._llm

    def interpret(
        self,
        *,
        remark_text: str,
        current_fields: Dict[str, Any],
        cancel_checker: CancelChecker = None,
    ) -> Dict[str, str]:
        if not remark_text or not remark_text.strip():
            return {}
        if cancel_checker and cancel_checker():
            return {}

        allowed = sorted(allowed_keys_for(current_fields))
        # Only scalar values are useful context for the model; drop dicts/lists
        # and internal underscore-prefixed bookkeeping keys.
        cur = {
            k: v for k, v in current_fields.items()
            if not k.startswith("_") and not isinstance(v, (dict, list))
        }
        prompt = ChatPromptTemplate.from_messages([("system", _SYSTEM), ("user", _USER)])

        try:
            llm = self._ensure_llm()
            chain = prompt | llm | StrOutputParser()
            raw = chain.invoke({
                "allowed": ", ".join(allowed),
                "current": _fit_current_json(cur, _MAX_CURRENT_JSON_CHARS),
                "remark": remark_text[:_MAX_REMARK_CHARS],
            })
        except Exception as exc:  # noqa: BLE001 — any failure must degrade gracefully
            logger.warning("remark LLM 调用失败，跳过 remark 调整: %s", exc, exc_info=True)
            return {}

        if not isinstance(raw, str):
            logger.warning("remark LLM 返回非字符串 (%s)，跳过", type(raw).__name__)
            return {}

        try:
            return validate_and_reorganize(raw, set(allowed))
        except Exception as exc:  # noqa: BLE001 — validator is pure but stay safe
            logger.warning("remark 输出校验异常，跳过: %s raw=%r", exc, raw[:200], exc_info=True)
            return {}
