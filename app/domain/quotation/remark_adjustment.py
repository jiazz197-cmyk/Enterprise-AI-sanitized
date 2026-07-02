"""Remark-driven field adjustment: collect remark text, validate LLM output, apply.

Pure domain logic — no I/O, stdlib only. This module is the single source of
truth for two constants reused across the pipeline:

- :data:`REMARKS_KEYS` — field names that carry free-text remarks (imported by
  ``keyword_normalizer``). Extended locally via :data:`_REMARKS_EXTRA_ALIASES`
  for remark *collection* only, so the shared set stays aligned with the
  feed-bucket capacity parser and cannot over-match there.
- :data:`RAW_REMARKS_RE` — the ``Remarks:`` section extractor (imported by
  ``spec_converter``), so the OCR-side parse and the remark-collection side
  cannot drift.

The LLM call lives in the adapter; this module guarantees that whatever the
model returns is reduced to a clean, whitelisted dict of
``{canonical_field: adjusted_value}`` before it touches pipeline params. The
whitelist is the stability boundary: even if the model hallucinates field
names, emits prose, or returns multiple objects, the worst case here is an
empty dict (no adjustment), never a corrupted params dict.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, Set

# Keys (compared case-insensitively) that carry free-text remarks in the
# matched content. Single source of truth — keyword_normalizer imports this.
# Keep the EXACT set in sync with feed-bucket capacity parsing; do NOT add
# short aliases here (they could over-match in keyword_normalizer).
REMARKS_KEYS: Set[str] = {
    "remarks", "othersremarks", "others/remarks", "otherremarks",
    "备注", "其他备注",
}

# Extra aliases recognized ONLY by collect_remark_text (singular "remark" /
# bare "其他"). Not shared with keyword_normalizer, which uses REMARKS_KEYS
# for feed-bucket capacity extraction and must not over-match.
_REMARKS_EXTRA_ALIASES: Set[str] = {"remark", "其他"}

# Canonical field names the model is allowed to adjust. This is the union of
# every key convert_all() / _apply_remark_param() reads from params, so a
# remark can both override an existing field and introduce a brand-new one the
# main parse missed. If convert_all() grows a new params key, add it here.
CANONICAL_REMARK_FIELDS: Set[str] = {
    # 机架 / 通用
    "surface", "material", "degree", "end_user_country", "detergent",
    "cable_length", "regulation", "name_plate", "application",
    # 斗 / 漏斗
    "capacity", "fb_spring", "fb_gate", "leak_proof", "pim",
    "collection_bucket_capacity", "collection_direction",
    "feed_bucket", "weigh_bucket", "infeed_funnel",
    # 振动盘 / 线性
    "linear_feeder_pan", "lfp_lip",
    # 挡板 / 整理
    "cc_baffles", "cf_baffles", "collating_chute", "collating_degree",
    "collating_funnel", "duck_mouth",
    # 传感器 / 电源
    "photoelectric_model", "sensor_type", "center_vibrator", "power_hz",
    # 其他部件
    "wb_gate", "wb_spring", "top_cone", "product_stopper",
}

# Raw "Remarks:" section in OCR text. Single source of truth —
# spec_converter._parse_remarks imports this so the two extraction sites
# cannot drift.
RAW_REMARKS_RE = re.compile(
    r'Remarks\s*:\s*\n(.+?)(?:\n\s*\n|\n\s*Ver\.|\Z)',
    re.DOTALL | re.IGNORECASE,
)

# Qwen3 may emit <think>...</think> blocks; strip them before parsing.
_THINKING_RE = re.compile(
    r'<think(?:ing)?>.*?</think(?:ing)?>', re.DOTALL | re.IGNORECASE,
)
# Non-greedy {...} : matches the first balanced-ish object; used to handle
# outputs with multiple JSON objects separated by prose without over-matching.
_JSON_BLOCK_NG_RE = re.compile(r'\{.*?\}', re.DOTALL)
# Greedy {...} : fallback for a `}` appearing inside a quoted value.
_JSON_BLOCK_RE = re.compile(r'\{.*\}', re.DOTALL)
# Loose key:value / key=value line patterns for the match-reorganize fallback.
# Quoted values (single or double) capture their full content including
# embedded separators; unquoted values run to the next comma/newline.
_KV_LINE_RE = re.compile(
    r'["\']?(?P<key>[A-Za-z_][A-Za-z0-9_]*)["\']?\s*[:=]\s*'
    r'(?:"(?P<dq>[^"]*)"'
    r"|'(?P<sq>[^']*)'"
    r'|(?P<bare>[^,\n]+?))'
    r'\s*(?:,|\n|$)'
)


def collect_remark_text(params: Dict[str, Any], ocr_text: str = "") -> str:
    """Gather free-text remarks from params keys + the OCR ``Remarks:`` section.

    The remark field name is matched case-insensitively against
    :data:`REMARKS_KEYS` ∪ :data:`_REMARKS_EXTRA_ALIASES` (so "Remark",
    "REMARKS", "备注" all match).
    """
    recognized = REMARKS_KEYS | _REMARKS_EXTRA_ALIASES
    chunks: list[str] = []
    for k, v in params.items():
        if k.startswith("_"):
            continue
        if k.strip().lower() in recognized and isinstance(v, str) and v.strip():
            chunks.append(v.strip())
    if ocr_text:
        m = RAW_REMARKS_RE.search(ocr_text)
        if m:
            rt = m.group(1).strip()
            if rt and not rt.startswith("Ver."):
                chunks.append(rt)
    return "\n".join(chunks).strip()


def allowed_keys_for(params: Dict[str, Any]) -> Set[str]:
    """Field names the model may adjust: canonical set ∪ existing params keys."""
    return CANONICAL_REMARK_FIELDS | {
        k for k in params.keys() if not k.startswith("_")
    }


def _strip_wrapper(raw: str) -> str:
    """Remove <think> blocks and markdown code fences."""
    raw = _THINKING_RE.sub('', raw)
    lines = [ln for ln in raw.splitlines() if ln.strip() not in ("```", "```json")]
    return "\n".join(lines).strip()


def _try_json(raw: str) -> Dict[str, Any] | None:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _extract_first_json(text: str) -> Dict[str, Any] | None:
    """Find the first parseable JSON object in ``text``.

    Try each non-greedy ``{...}`` span first (handles multiple objects with
    prose between them — a greedy match would span the whole thing and fail).
    Fall back to the greedy full span, which handles a ``}`` appearing inside
    a quoted value (e.g. ``{"note": "a}b"}``).
    """
    for m in _JSON_BLOCK_NG_RE.finditer(text):
        obj = _try_json(m.group(0))
        if obj is not None:
            return obj
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return _try_json(m.group(0))
    return None


def _reorganize(raw: str) -> Dict[str, str]:
    """匹配重组: scan for key:value / key=value pairs and rebuild a dict.

    Used when JSON parsing fails — e.g. the model wrapped the object in prose
    or used non-JSON separators. Quoted values preserve embedded commas/colons;
    only well-formed ``identifier: value`` pairs are captured.
    """
    out: Dict[str, str] = {}
    for m in _KV_LINE_RE.finditer(raw):
        key = m.group("key").strip().lower()
        val = m.group("dq")
        if val is None:
            val = m.group("sq")
        if val is None:
            val = m.group("bare")
        if val is None:
            continue
        val = val.strip()
        if key and val:
            out[key] = val
    return out


def validate_and_reorganize(raw_output: str, allowed_keys: Iterable[str]) -> Dict[str, str]:
    """Reduce arbitrary LLM output to a clean ``{field: value}`` dict.

    Pipeline: strip wrapper → try JSON → try first ``{...}`` block →
    匹配重组 regex scan → whitelist-filter (case-insensitive, non-empty
    strings only). Always returns a dict; never raises on bad input.
    """
    if not raw_output or not raw_output.strip():
        return {}

    allowed_lower = {str(k).strip().lower() for k in allowed_keys}
    cleaned = _strip_wrapper(raw_output)

    obj = _try_json(cleaned)
    if obj is None:
        obj = _extract_first_json(cleaned)
    if obj is None:
        obj = _reorganize(cleaned)

    result: Dict[str, str] = {}
    for k, v in obj.items():
        kl = str(k).strip().lower()
        if kl not in allowed_lower:
            continue
        if v is None or isinstance(v, (dict, list)):
            continue
        sv = str(v).strip()
        if not sv:
            continue
        result[kl] = sv
    return result


def _resolve_target_key(params: Dict[str, Any], lower_key: str) -> str:
    """Map a lowercase canonical key back to the casing already in params,
    so we override rather than duplicate a key. Falls back to the lowercase
    canonical name (which convert_all() reads) for brand-new fields."""
    for pk in params.keys():
        if pk.lower() == lower_key:
            return pk
    return lower_key


def apply_adjustments(params: Dict[str, Any], adjustments: Dict[str, str]) -> Dict[str, Any]:
    """Return a copy of ``params`` with validated adjustments applied.

    ``adjustments`` must already be whitelist-filtered (keys are lowercase
    canonical field names). Brand-new canonical fields are added so a remark
    can introduce a value the main parse missed (e.g. cable_length).
    """
    out = dict(params)
    for k, v in adjustments.items():
        out[_resolve_target_key(out, k)] = v
    return out
