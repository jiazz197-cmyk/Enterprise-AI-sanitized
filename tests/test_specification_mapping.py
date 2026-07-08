"""Unit tests for SpecificationMapping → keywords_payload.

The tests feed a hand-crafted `extracted_info` dict that mirrors what
`app.adapters.ocr.infoextraction.extract_info` produces, then check
that `SpecificationMapping.generate_keywords_payload()` maps every
documented (type, attr) pair correctly.

Run with either:

    pytest tests/test_specification_mapping.py -v
    python tests/test_specification_mapping.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# Ensure the project root is on sys.path when the file is executed directly.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.domain.quotation.specification_mapping import (  # noqa: E402
    KEYWORD_RULES,
    SpecificationMapping,
)


def _sample_extracted_info() -> Dict[str, Any]:
    """Build a realistic OCR result that exercises every ATTR_SOURCE_MAP path.

    The keys and shape mirror what `extract_info()` actually produces:
    `meta`, `spec`, `documents`, `regulation`, `name_plate`, `optional_spare_parts`,
    `display_language`, `remarks`. Each `spec.<n>_<slug>` entry follows the
    `{value, note?, alt?, discharge?}` structure.
    """
    return {
        "meta": {
            "date": "2026-04-21",
            "work_no": "WG-20260421-001",
            "model": "adw-a-0314s",
            "controller": "Standard",
            "subsidiary_agent": "TOKYO OFFICE",
            "end_user": "SAMPLE CO.",
            "end_user_country": "India",
            "destination_port": "Mumbai",
            "ex_factory_date": "2026-05-01",
        },
        "documents": {},
        "spec": {
            "2_surface": {"value": "Flat"},
            "3_infeed_funnel": {"value": "single"},
            "5_top_cone": {"value": "single"},
            "6_center_vibrator": {"value": "single"},
            "7_linear_feeder_pan": {"value": "sn"},
            "8_lfp_lip": {"value": "flat", "note": "← flat lip"},
            "10_fb_gate": {"value": "↑ single door"},
            "11_fb_spring": {"value": "Yes"},
            "12_weigh_bucket": {"value": "std"},
            "13_wb_gate": {"value": "↑ single door"},
            "14_wb_spring": {"value": "No"},
            "15_collating_chute": {"value": "50-degree"},
            "16_cc_baffles": {"value": "no"},
            "17_collating_funnel": {"value": "std"},
            "18_cf_baffles": {"value": "no"},
            "19_cf_l_shaped_bracket": {"value": "yes"},
            "21_collection_bucket": {"value": "3L", "discharge": "1-way"},
            "22_cb_gate": {"value": "motor"},
            "24_detergent": {"value": "no"},
            "25_common_bed": {"value": "painted on ss"},
            "26_cable_length": {"value": "8m"},
            "28_regulation": {"value": "india w&m"},
            "c_c": {"value": "flat"},
            "degree": {"value": "50-degree"},
        },
        "regulation": "india w&m",
        "name_plate": {},
        "optional_spare_parts": "",
        "display_language": {},
        "remarks": "",
        "additional_info": [],
    }


# Expected value for each (type, normalized_attr) pair after mapping.
# `True`/`False` are the normalized booleans, strings are already lower-cased
# (SpecificationMapping lowercases non-boolean scalars).
_EXPECTED_TABLE: Dict[str, Dict[str, Any]] = {
    "机架": {
        "model": "ADW-A-0314S",
        "surface": "flat",
        "commonbed": "painted on ss",
        "collating_chute": "50-degree",
        "end_user_country": "india",
        "weigh_bucket": "std",
        "detergent": False,
    },
    "供料漏斗": {
        "model": "ADW-A-0314S",
        "surface": "flat",
        "infeed_funnel": "single",
        "linear_feeder_pan": "sn",
    },
    "顶锥": {
        "model": "ADW-A-0314S",
        "surface": "flat",
        "lfp_lip": "← flat lip",
        "top_cone": "single",
        "detergent": False,
        "linear_feeder_pan": "sn",
    },
    "振动盘": {
        "model": "ADW-A-0314S",
        "linear_feeder_pan": "sn",
        "detergent": False,
        "lfp_lip": "← flat lip",
        "surface": "flat",
    },
    "供料斗": {
        "model": "ADW-A-0314S",
        "fb_spring": True,
        "lfp_lip": "← flat lip",
        "fb_gate": "↑ single door",
        "surface": "flat",
        "detergent": False,
    },
    "计量斗": {
        "model": "ADW-A-0314S",
        "wb_spring": False,
        "surface": "flat",
        "wb_gate": "↑ single door",
        "detergent": False,
    },
    "驱动单元": {
        "model": "ADW-A-0314S",
        "regulation": "india w&m",
    },
    "溜槽": {
        "model": "ADW-A-0314S",
        "collating_chute": "50-degree",
        "surface": "flat",
        "detergent": False,
        "cc_baffles": False,
    },
    "收集锥": {
        "model": "ADW-A-0314S",
        "surface": "flat",
        "collating_chute": "50-degree",
        "cf_baffles": False,
        "cf_l_shaped_bracket": True,
        "c_c": "flat",
    },
    "电气": {"model": "ADW-A-0314S"},
    "配线单元": {
        "model": "ADW-A-0314S",
        "cable_length": "8m",
    },
    "主振动器": {
        "model": "ADW-A-0314S",
        "center_vibrator": "single",
    },
    "线性振动器": {"model": "ADW-A-0314S"},
    "中心柱天板密封罩": {
        "model": "ADW-A-0314S",
        "center_vibrator": "single",
        "detergent": False,
    },
    "供料锥支架": {
        "model": "ADW-A-0314S",
        "top_cone": "single",
    },
    "包装": {
        "model": "ADW-A-0314S",
        "end_user_country": "india",
        "detergent": False,
    },
    "集合斗": {
        "model": "ADW-A-0314S",
        "collection_bucket": "3l",
        "surface": "flat",
        "collating_bucket": "motor",
        "collecting_funnel": "std",
        "degree": "50-degree",
        "detergent": False,
    },
    "记忆斗": {
        "model": "ADW-A-0314S",
        "surface": "flat",
    },
    "防碎": {"model": "ADW-A-0314S"},
    "料层调整圈": {"model": "ADW-A-0314S"},
}


def _payload_to_type_map(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in payload.get("keywords", []):
        type_name = item.get("type")
        attr = item.get("attr") or {}
        if type_name is not None:
            result[type_name] = dict(attr)
    return result


# ---------------------------------------------------------------------------
# Tests


def test_every_rule_type_is_present_in_payload() -> None:
    mapping = SpecificationMapping(_sample_extracted_info())
    payload = mapping.generate_keywords_payload(max_retries=3)
    type_map = _payload_to_type_map(payload)

    rule_types = [rule["type"] for rule in KEYWORD_RULES]
    missing = [t for t in rule_types if t not in type_map]
    assert not missing, f"missing types in payload: {missing}"


def test_each_attr_maps_to_expected_normalized_value() -> None:
    mapping = SpecificationMapping(_sample_extracted_info())
    payload = mapping.generate_keywords_payload(max_retries=3)
    type_map = _payload_to_type_map(payload)

    mismatches: List[str] = []
    for type_name, expected_attrs in _EXPECTED_TABLE.items():
        actual_attrs = type_map.get(type_name, {})
        for attr_key, expected in expected_attrs.items():
            actual = actual_attrs.get(attr_key, "<MISSING>")
            if actual != expected:
                mismatches.append(
                    f"{type_name}.{attr_key}: expected={expected!r}, actual={actual!r}"
                )

    assert not mismatches, "mapping mismatches:\n  " + "\n  ".join(mismatches)


def test_keywords_payload_is_jsonable_and_shape_correct() -> None:
    mapping = SpecificationMapping(_sample_extracted_info())
    payload = mapping.generate_keywords_payload(max_retries=3)
    # Must be JSON-serialisable (no tuples/sets/non-str keys).
    json.dumps(payload, ensure_ascii=False)

    assert isinstance(payload, dict) and "keywords" in payload
    for entry in payload["keywords"]:
        assert isinstance(entry, dict)
        assert isinstance(entry.get("type"), str) and entry["type"]
        assert isinstance(entry.get("attr"), dict)
        attr = entry.get("attr") or {}
        if "model" in attr:
            assert entry.get("model") == attr["model"]


def test_missing_spec_falls_back_to_fuzzy_on_retries() -> None:
    """If the strict path is absent, a later attempt should fall back via fuzzy match."""
    info = _sample_extracted_info()
    # Drop the canonical surface entry; keep only an alt-named one so only a
    # fuzzy search across `spec` can still find `surface`.
    info["spec"].pop("2_surface")
    info["spec"]["surface_main"] = {"value": "flat"}

    mapping = SpecificationMapping(info)
    payload = mapping.generate_keywords_payload(max_retries=3)
    type_map = _payload_to_type_map(payload)

    assert type_map["机架"].get("surface") == "flat", (
        f"expected fuzzy fallback to resolve surface, got {type_map['机架']!r}"
    )


# ---------------------------------------------------------------------------
# CLI entry point: human-readable report


def _print_report() -> int:
    mapping = SpecificationMapping(_sample_extracted_info())
    payload = mapping.generate_keywords_payload(max_retries=3)
    type_map = _payload_to_type_map(payload)

    print("=" * 74)
    print("SpecificationMapping → keywords_payload 参数对应报告")
    print("=" * 74)

    exit_code = 0
    for type_name, expected_attrs in _EXPECTED_TABLE.items():
        actual_attrs = type_map.get(type_name, {})
        print(f"\n[{type_name}]")
        all_keys = sorted(set(expected_attrs) | set(actual_attrs))
        for key in all_keys:
            expected = expected_attrs.get(key, "<not tested>")
            actual = actual_attrs.get(key, "<MISSING>")
            ok = actual == expected
            if not ok:
                exit_code = 1
            mark = "✓" if ok else "✗"
            print(f"  {mark} {key}: actual={actual!r}  expected={expected!r}")

    print("\n" + "=" * 74)
    if exit_code == 0:
        print("全部参数成功对应。")
    else:
        print("存在参数未正确对应，见上方 ✗ 行。")
    print("=" * 74)
    return exit_code


if __name__ == "__main__":
    sys.exit(_print_report())
