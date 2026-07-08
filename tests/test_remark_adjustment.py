"""Unit tests for remark-driven field adjustment (pure domain logic).

No model service required — these cover the stability core: remark detection,
output validation, match-reorganize fallback, and whitelist filtering.

Run with:
    pytest tests/test_remark_adjustment.py -v
    python tests/test_remark_adjustment.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load the pure-stdlib module directly by file path. This avoids importing
# app.domain.quotation (whose __init__ pulls in config → pydantic), so the
# stability core can be verified without the project's full dependency stack.
_MOD_PATH = _PROJECT_ROOT / "app" / "domain" / "quotation" / "remark_adjustment.py"
_spec = importlib.util.spec_from_file_location("remark_adjustment", _MOD_PATH)
_ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ra)

CANONICAL_REMARK_FIELDS = _ra.CANONICAL_REMARK_FIELDS
allowed_keys_for = _ra.allowed_keys_for
apply_adjustments = _ra.apply_adjustments
collect_remark_text = _ra.collect_remark_text
validate_and_reorganize = _ra.validate_and_reorganize

ALLOWED = CANONICAL_REMARK_FIELDS


# ── collect_remark_text ────────────────────────────────────────────────

def test_collect_remark_from_params_case_insensitive():
    params = {"Remarks": "surface changed to dimple", "surface": "flat"}
    assert collect_remark_text(params) == "surface changed to dimple"


def test_collect_remark_from_chinese_key():
    params = {"备注": "cable 5m", "surface": "flat"}
    assert collect_remark_text(params) == "cable 5m"


def test_collect_remark_from_ocr_remarks_section():
    ocr = "Surface: Flat\nRemarks:\nDegree 30\ncable 5m\n\nVer. 1.0"
    assert collect_remark_text({}, ocr) == "Degree 30\ncable 5m"


def test_collect_remark_none_present():
    assert collect_remark_text({"surface": "flat"}, "no remarks here") == ""


def test_collect_remark_ignores_internal_keys():
    params = {"_row_supplements": {"x": "y"}, "surface": "flat"}
    assert collect_remark_text(params) == ""


# ── validate_and_reorganize: happy paths ───────────────────────────────

def test_validate_plain_json():
    raw = '{"surface": "dimple", "degree": "30"}'
    assert validate_and_reorganize(raw, ALLOWED) == {"surface": "dimple", "degree": "30"}


def test_validate_quoted_value_with_comma_preserved():
    # A1 regression: comma inside a quoted value must not truncate it.
    raw = '{"material": "stainless, brushed"}'
    assert validate_and_reorganize(raw, ALLOWED) == {"material": "stainless, brushed"}


def test_validate_multi_object_no_corruption():
    # A2 regression: two JSON objects with prose between them. The greedy
    # span must not merge them into garbage; the first object wins.
    raw = '{"surface": "flat"} some prose {"degree": "30"}'
    out = validate_and_reorganize(raw, ALLOWED)
    assert out.get("surface") == "flat"
    assert "degree" not in out  # only the first parseable object is taken
    # and crucially, surface is not corrupted with the interstitial text
    assert "prose" not in out["surface"]


def test_validate_value_with_brace_in_quotes():
    # A2: a `}` inside a quoted value must not break extraction.
    raw = '{"name_plate": "SB1}2"}'
    out = validate_and_reorganize(raw, ALLOWED)
    assert out.get("name_plate") == "SB1}2"


def test_validate_fenced_json():
    raw = '```json\n{"surface": "dimple"}\n```'
    assert validate_and_reorganize(raw, ALLOWED) == {"surface": "dimple"}


def test_validate_strips_thinking_tags():
    raw = '<thinking>let me reason</thinking>\n{"cable_length": "5m"}'
    assert validate_and_reorganize(raw, ALLOWED) == {"cable_length": "5m"}


def test_validate_json_embedded_in_prose():
    raw = 'Here you go: {"degree": "45"} hope that helps'
    assert validate_and_reorganize(raw, ALLOWED) == {"degree": "45"}


# ── validate_and_reorganize: 匹配重组 fallback ─────────────────────────

def test_reorganize_non_json_kv_lines():
    raw = "surface = dimple\ndegree: 45"
    assert validate_and_reorganize(raw, ALLOWED) == {"surface": "dimple", "degree": "45"}


def test_reorganize_quoted_kv():
    raw = '"surface": "dimple", "regulation": "ce"'
    assert validate_and_reorganize(raw, ALLOWED) == {"surface": "dimple", "regulation": "ce"}


def test_reorganize_quoted_value_with_comma_preserved():
    # A1 regression in the reorganize path (no JSON at all): a quoted value
    # containing a comma must survive intact.
    raw = 'material: "stainless, brushed"'
    assert validate_and_reorganize(raw, ALLOWED) == {"material": "stainless, brushed"}


# ── validate_and_reorganize: whitelist + robustness ────────────────────

def test_validate_drops_unknown_fields():
    raw = '{"surface": "dimple", "horsepower": "100", "color": "red"}'
    assert validate_and_reorganize(raw, ALLOWED) == {"surface": "dimple"}


def test_validate_accepts_existing_param_keys():
    # A key not in CANONICAL but present in params is allowed.
    allowed = allowed_keys_for({"custom_field": "x"})
    raw = '{"custom_field": "y"}'
    assert validate_and_reorganize(raw, allowed) == {"custom_field": "y"}


def test_validate_drops_empty_values():
    raw = '{"surface": "", "degree": "30"}'
    assert validate_and_reorganize(raw, ALLOWED) == {"degree": "30"}


def test_validate_empty_input_returns_empty():
    assert validate_and_reorganize("", ALLOWED) == {}
    assert validate_and_reorganize("   ", ALLOWED) == {}


def test_validate_garbage_returns_empty():
    assert validate_and_reorganize("the quick brown fox", ALLOWED) == {}


def test_validate_never_raises_on_bad_json():
    # Malformed JSON that also defeats the KV regex must yield {}, not raise.
    assert validate_and_reorganize('{surface: "dimple"', ALLOWED) in (
        {"surface": "dimple"}, {},
    )


# ── apply_adjustments ──────────────────────────────────────────────────

def test_apply_overrides_existing_field():
    params = {"surface": "flat", "degree": "30"}
    out = apply_adjustments(params, {"surface": "dimple"})
    assert out["surface"] == "dimple"
    assert out["degree"] == "30"
    # original dict not mutated
    assert params["surface"] == "flat"


def test_apply_introduces_new_canonical_field():
    params = {"surface": "flat"}
    out = apply_adjustments(params, {"cable_length": "5m"})
    assert out["cable_length"] == "5m"
    assert out["surface"] == "flat"


def test_apply_preserves_internal_keys():
    params = {"_row_supplements": {"x": "y"}, "surface": "flat"}
    out = apply_adjustments(params, {"surface": "dimple"})
    assert out["_row_supplements"] == {"x": "y"}
    assert out["surface"] == "dimple"


def _run_all() -> None:
    """Allow `python tests/test_remark_adjustment.py` without pytest."""
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"ERROR {name}: {exc!r}")
    print(f"\n{'ALL PASSED' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    _run_all()
