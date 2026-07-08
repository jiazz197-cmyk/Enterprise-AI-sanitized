"""Specification mapping and structured keyword payload builder (moved out of the workers package; was SpecificationMapping)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


RAW_TYPE_ATTR_RULES: List[Tuple[str, List[str]]] = [
    ("机架", ["Model", "Surface", "Commonbed", "collating chute", "End-user contry", "Weigh bucket", "Detergent"]),
    ("供料漏斗", ["Model", "Surface", "Infeed funnel", "Linear feeder pan"]),
    ("顶锥", ["Model", "Surface", "LFP lip", "Top cone", "Detergent", "Linear feeder pan"]),
    ("振动盘", ["Model", "Linera feeder pan", "Detergent", "LFP lip", "Surface"]),
    ("供料斗", ["Model", "FB spring", "LFP lip", "FB gate", "Surface", "Detergent", "Remarks"]),
    ("计量斗", ["Model", "WB spring", "Surface", "WB gate", "Detergent", "Remarks"]),
    ("驱动单元", ["Model", "Regulation"]),
    ("溜槽", ["Model", "Collating chute", "Surface", "Detergent", "CC baffles"]),
    ("收集锥", ["Model", "Surface", "Collating chute", "CF baffles", "CF L-shaped bracket", "C-C"]),
    ("电气", ["Model"]),
    ("配线单元", ["Model", "Cable length"]),
    ("主振动器", ["Model", "Center vibrator"]),
    ("线性振动器", ["Model"]),
    ("中心柱天板密封罩", ["Model", "Center vibrator", "Detergent"]),
    ("供料锥支架", ["Model", "Top cone"]),
    ("包装", ["Model", "End-user country", "Detergent"]),
    ("集合斗", ["Model", "Collection bucket", "Surface", "Collating bucket", "Surface", "Collecting funnel", "Degreee", "Detergent"]),
    ("记忆斗", ["Model", "Surface"]),
    ("防碎", ["Model"]),
    ("料层调整圈", ["Model"]),
]

ATTR_SOURCE_MAP: Dict[str, List[str]] = {
    "model": ["meta.model"],
    "surface": ["spec.2_surface.value", "spec.2_surface.note", "spec.2_surface.alt"],
    "commonbed": ["spec.25_common_bed.value", "spec.25_common_bed.note"],
    "collating_chute": ["spec.15_collating_chute.value", "spec.degree.value"],
    "end_user_country": ["meta.end_user_country"],
    "weigh_bucket": ["spec.12_weigh_bucket.value", "spec.12_welgh_bucket.value"],
    "detergent": ["spec.24_detergent.value", "spec.24_detergent.note"],
    "infeed_funnel": ["spec.3_infeed_funnel.value", "spec.3_infeed_funnel.note"],
    "linear_feeder_pan": ["spec.7_linear_feeder_pan.value", "spec.7_linear_feeder_pan.note"],
    "lfp_lip": ["spec.8_lfp_lip.note", "spec.8_lfp_lip.value"],
    "top_cone": ["spec.5_top_cone.value", "spec.5_top_cone.note"],
    "fb_spring": ["spec.11_fb_spring.value"],
    "fb_gate": ["spec.10_fb_gate.value", "spec.10_fb_gate.note"],
    "wb_spring": ["spec.14_wb_spring.value"],
    "wb_gate": ["spec.13_wb_gate.value", "spec.13_wb_gate.note"],
    "regulation": ["spec.28_regulation.value", "regulation"],
    "cc_baffles": ["spec.16_cc_baffles.value"],
    "cf_baffles": ["spec.18_cf_baffles.value"],
    "cf_l_shaped_bracket": ["spec.19_cf_l_shaped_bracket.value", "spec.19_cf_l_shaped_bracket.note"],
    "c_c": ["spec.c_c.value", "spec.c_c.note"],
    "cable_length": ["spec.26_cable_length.value"],
    "center_vibrator": ["spec.6_center_vibrator.value", "spec.6_center_vibrato.value", "spec.6_center_vibrator.note"],
    "collection_bucket": ["spec.21_collection_bucket.value", "spec.21_collection_bucket.discharge"],
    "collating_bucket": ["spec.22_cb_gate.value", "spec.21_collection_bucket.discharge"],
    "collecting_funnel": ["spec.17_collating_funnel.value"],
    "degree": ["spec.degree.value", "spec.15_collating_chute.value"],
    "remarks": ["remarks"],
}

BOOLEAN_ATTRS = {
    "detergent",
    "fb_spring",
    "wb_spring",
    "cc_baffles",
    "cf_baffles",
    "cf_l_shaped_bracket",
}
YES_VALUES = {"yes", "true", "y", "1", "是"}
NO_VALUES = {"no", "false", "n", "0", "否", "无"}


def _normalize_attr_key(raw_attr: str) -> str:
    fixes = {
        "end-user contry": "end_user_country",
        "end-user country": "end_user_country",
        "linera feeder pan": "linear_feeder_pan",
        "degreee": "degree",
        "c-c": "c_c",
    }
    lowered = raw_attr.strip().lower()
    if lowered in fixes:
        return fixes[lowered]
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    return normalized


KEYWORD_RULES: List[Dict[str, Any]] = [
    {"type": part_type, "attrs": [_normalize_attr_key(attr) for attr in raw_attrs]}
    for part_type, raw_attrs in RAW_TYPE_ATTR_RULES
]


class SpecificationMapping:
    """Map extracted OCR data to structured keywords."""

    def __init__(self, json_data: Dict[str, Any]):
        self.raw_data = json_data or {}
        self.meta = self.raw_data.get("meta", {})
        self.documents = self.raw_data.get("documents", {})
        self.spec = self.raw_data.get("spec", {})
        self.regulation = self.raw_data.get("regulation", "")
        self.name_plate = self.raw_data.get("name_plate", {})
        self.optional_spare_parts = self.raw_data.get("optional_spare_parts", "")
        self.display_language = self.raw_data.get("display_language", {})
        self.remarks = self.raw_data.get("remarks", "")

    def _get_value(self, source: str) -> Any:
        parts = source.split(".")
        value: Any = self.raw_data
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            elif isinstance(value, list) and part.isdigit():
                idx = int(part)
                value = value[idx] if 0 <= idx < len(value) else None
            else:
                return None
            if value is None:
                return None
        return value

    def _fuzzy_search_spec_value(self, attr_key: str) -> Optional[Any]:
        if not isinstance(self.spec, dict):
            return None
        if attr_key in {"c_c", "model"}:
            return None
        tokens = [token for token in attr_key.split("_") if len(token) >= 3 and token not in {"user"}]
        if not tokens:
            return None
        for spec_key, payload in self.spec.items():
            lowered_key = str(spec_key).lower()
            if all(token in lowered_key for token in tokens):
                if isinstance(payload, dict):
                    for value_key in ("value", "note", "alt", "discharge"):
                        raw_value = payload.get(value_key)
                        if raw_value not in (None, ""):
                            return raw_value
                elif payload not in (None, ""):
                    return payload
        return None

    def _normalize_scalar(self, attr_key: str, value: Any) -> Optional[Any]:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip()
        if not text:
            return None

        lowered = text.lower()
        if attr_key in BOOLEAN_ATTRS:
            if lowered in YES_VALUES:
                return True
            if lowered in NO_VALUES:
                return False

        if attr_key == "surface":
            if "flat" in lowered:
                return "flat"
        if attr_key == "end_user_country":
            if "india" in lowered:
                return "india"
            if "china" in lowered:
                return "china"

        if attr_key == "model":
            return text.upper()
        return re.sub(r"\s+", " ", lowered)

    def _resolve_attr_value(self, attr_key: str, attempt: int) -> Optional[Any]:
        candidates = ATTR_SOURCE_MAP.get(attr_key, [f"meta.{attr_key}", f"spec.{attr_key}.value"])
        search_paths = candidates[:1] if attempt == 0 else candidates

        for path in search_paths:
            raw_value = self._get_value(path)
            if isinstance(raw_value, dict):
                for key in ("value", "note", "alt", "discharge"):
                    normalized = self._normalize_scalar(attr_key, raw_value.get(key))
                    if normalized is not None:
                        return normalized
            else:
                normalized = self._normalize_scalar(attr_key, raw_value)
                if normalized is not None:
                    return normalized

        if attempt >= 2:
            fallback = self._fuzzy_search_spec_value(attr_key)
            normalized = self._normalize_scalar(attr_key, fallback)
            if normalized is not None:
                return normalized
        return None

    def _build_keywords_for_attempt(self, attempt: int) -> Tuple[Dict[str, Any], int]:
        keywords: List[Dict[str, Any]] = []
        missing_count = 0

        for rule in KEYWORD_RULES:
            type_name = rule["type"]
            attrs = rule["attrs"]
            attr_payload: Dict[str, Any] = {}

            for attr_key in attrs:
                value = self._resolve_attr_value(attr_key, attempt)
                if value is None:
                    missing_count += 1
                    continue
                attr_payload[attr_key] = value

            if attr_payload:
                keyword_entry: Dict[str, Any] = {"type": type_name, "attr": attr_payload}
                model_value = attr_payload.get("model")
                if model_value is not None:
                    keyword_entry["model"] = model_value
                keywords.append(keyword_entry)

        return {"keywords": keywords}, missing_count

    def generate_keywords_payload(self, max_retries: int = 3) -> Dict[str, Any]:
        retries = max(1, max_retries)
        best_payload: Dict[str, Any] = {"keywords": []}
        best_resolved = -1
        for attempt in range(retries):
            payload, missing_count = self._build_keywords_for_attempt(attempt)
            resolved_count = sum(
                len(entry.get("attr") or {}) for entry in payload.get("keywords", [])
            )
            if resolved_count > best_resolved:
                best_resolved = resolved_count
                best_payload = payload
            if missing_count == 0:
                return payload
        return best_payload

    def generate_output_mapping(self) -> Dict[str, str]:
        payload = self.generate_keywords_payload(max_retries=3)
        outputs: Dict[str, str] = {}
        for item in payload.get("keywords", []):
            type_name = str(item.get("type") or "").strip()
            attrs = item.get("attr") or {}
            if not type_name:
                continue
            part_values = [f"{key}:{value}" for key, value in attrs.items()]
            outputs[type_name] = f"{type_name}（{'/'.join(part_values)}）"
        return outputs

    def to_dict(self) -> Dict[str, Any]:
        return {
            "meta": self.meta,
            "documents": self.documents,
            "spec": self.spec,
            "regulation": self.regulation,
            "name_plate": self.name_plate,
            "optional_spare_parts": self.optional_spare_parts,
            "display_language": self.display_language,
            "remarks": self.remarks,
        }

    @classmethod
    def from_json_string(cls, json_string: str) -> "SpecificationMapping":
        return cls(json.loads(json_string))

    @classmethod
    def from_json_file(cls, file_path: str) -> "SpecificationMapping":
        with open(file_path, "r", encoding="utf-8") as fp:
            return cls(json.load(fp))
