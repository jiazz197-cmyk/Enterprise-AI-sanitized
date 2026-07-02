"""Integration tests for the remark → field-adjustment stage.

These exercise the REAL ``SpecParseAndConvertAdapter`` orchestration end to end
(parse → collect remark → interpret → validate → apply → convert), using a
fake ``RemarkInterpreterPort`` so no model service is required. The stability
guarantees under test: adjustments are applied when a remark is present, the
interpreter is NOT invoked when there is no remark, and any interpreter
failure leaves the original pipeline behavior untouched.

Two run modes:
    pytest tests/test_remark_integration.py -v        # real env (with deps)
    python3 tests/test_remark_integration.py          # standalone (no deps)
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, Dict, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ── standalone bootstrap: stub the only non-stdlib dep (app.core.config) ─
# In the real env pydantic is installed and `app.core.config` imports cleanly;
# the stubs below are installed only when that import fails, so this file runs
# identically under pytest-with-deps and bare `python3`.
try:
    import app.core.config  # noqa: F401
    _HAS_REAL_CONFIG = True
except Exception:  # pragma: no cover - exercised only in bare envs
    _HAS_REAL_CONFIG = False

if not _HAS_REAL_CONFIG:
    _cfg = types.ModuleType("app.core.config")

    class _StubSettings:
        REMARK_LLM_INTERPRETER_ENABLED = True
    _cfg.settings = _StubSettings()
    sys.modules["app.core.config"] = _cfg
    # Neutralize app.domain.quotation.__init__: its real body cascades into
    # core.logging → config and reads many settings attrs. We only need the
    # pure submodules (exceptions, remark_adjustment, value_objects), so expose
    # __path__ and skip the heavy init.
    _dq = types.ModuleType("app.domain.quotation")
    _dq.__path__ = [str(_PROJECT_ROOT / "app" / "domain" / "quotation")]
    sys.modules["app.domain.quotation"] = _dq

from app.adapters.quotation.spec_parse_convert import (  # noqa: E402
    SpecParseAndConvertAdapter,
)
from app.domain.quotation.remark_adjustment import (  # noqa: E402
    allowed_keys_for,
    validate_and_reorganize,
)
from app.ports.domains.quotation import RemarkInterpreterPort  # noqa: E402


# ── fakes ───────────────────────────────────────────────────────────────

class _FakeRemarkInterpreter(RemarkInterpreterPort):
    """Returns a canned dict; records whether it was invoked and with what."""

    def __init__(self, output: Dict[str, str]):
        self.output = output
        self.invoked = False
        self.last_remark: Optional[str] = None
        self.last_fields: Optional[Dict[str, Any]] = None

    def interpret(self, *, remark_text, current_fields, cancel_checker=None):
        self.invoked = True
        self.last_remark = remark_text
        self.last_fields = current_fields
        return dict(self.output)


class _RaisingRemarkInterpreter(RemarkInterpreterPort):
    def interpret(self, *, remark_text, current_fields, cancel_checker=None):
        raise RuntimeError("model service unavailable")


class _NoneRemarkInterpreter(RemarkInterpreterPort):
    """Returns no adjustments (e.g. model found nothing to change)."""

    def interpret(self, *, remark_text, current_fields, cancel_checker=None):
        return {}


# ── fixtures ────────────────────────────────────────────────────────────

_OCR_WITH_REMARK = (
    "Model ADW-A-0314S\n"
    "Surface Flat\n"
    "Remarks:\n"
    "surface changed to dimple, cable 5m\n"
    "\n"
    "Ver. 1.0"
)

_OCR_NO_REMARK = "Model ADW-A-0314S\nSurface Flat\n"


def _adapter(interpreter) -> SpecParseAndConvertAdapter:
    return SpecParseAndConvertAdapter(remark_interpreter=interpreter, enabled=True)


# ── tests: happy path ───────────────────────────────────────────────────

def test_remark_adjustments_override_parsed_values():
    interp = _FakeRemarkInterpreter({"surface": "dimple"})
    res = _adapter(interp).parse_and_convert(ocr_text=_OCR_WITH_REMARK)
    # original parse yields surface=flat; remark override wins
    assert res["params"]["surface"] == "dimple"
    assert interp.invoked is True
    assert interp.last_remark and "dimple" in interp.last_remark


def test_remark_can_introduce_new_field():
    interp = _FakeRemarkInterpreter({"cable_length": "5m"})
    res = _adapter(interp).parse_and_convert(ocr_text=_OCR_WITH_REMARK)
    assert res["params"]["cable_length"] == "5m"
    # and convert_all picks the new field up into specs attr where relevant
    assert "specs" in res and isinstance(res["specs"], list)


def test_remark_text_passed_to_interpreter_includes_ocr_section():
    interp = _FakeRemarkInterpreter({})
    _adapter(interp).parse_and_convert(ocr_text=_OCR_WITH_REMARK)
    assert "cable 5m" in interp.last_remark


# ── tests: skip behavior ────────────────────────────────────────────────

def test_no_remark_interpreter_not_invoked():
    interp = _FakeRemarkInterpreter({"surface": "dimple"})
    res = _adapter(interp).parse_and_convert(ocr_text=_OCR_NO_REMARK)
    assert interp.invoked is False
    assert res["params"].get("surface") == "flat"


def test_interpreter_returns_empty_keeps_original():
    interp = _NoneRemarkInterpreter()
    res = _adapter(interp).parse_and_convert(ocr_text=_OCR_WITH_REMARK)
    assert res["params"].get("surface") == "flat"


# ── tests: graceful degradation (the stability core) ────────────────────

def test_interpreter_raises_does_not_break_pipeline():
    interp = _RaisingRemarkInterpreter()
    res = _adapter(interp).parse_and_convert(ocr_text=_OCR_WITH_REMARK)
    # original parsed value survives unchanged
    assert res["params"].get("surface") == "flat"
    assert "specs" in res and len(res["specs"]) > 0


def test_interpreter_raises_returns_well_formed_result():
    interp = _RaisingRemarkInterpreter()
    res = _adapter(interp).parse_and_convert(ocr_text=_OCR_WITH_REMARK)
    # the three contract keys are always present
    assert set(res.keys()) >= {"params", "specs", "keywords_payload"}
    assert res["keywords_payload"]["keywords"] is res["specs"]


# ── tests: invalid model output is sanitized before applying ────────────

def test_unknown_field_names_dropped_before_apply():
    # Simulate the model returning a hallucinated field; the adapter's
    # interpreter is responsible for running validate_and_reorganize, but the
    # adapter must still be safe if a misbehaving interpreter returns junk.
    class _Leaky(RemarkInterpreterPort):
        def interpret(self, *, remark_text, current_fields, cancel_checker=None):
            return {"surface": "dimple", "horsepower": "999", "color": "red"}
    res = _adapter(_Leaky()).parse_and_convert(ocr_text=_OCR_WITH_REMARK)
    assert res["params"]["surface"] == "dimple"
    assert "horsepower" not in res["params"]
    assert "color" not in res["params"]


def test_validate_and_reorganize_drops_unknowns():
    allowed = allowed_keys_for({"surface": "flat"})
    raw = '{"surface": "dimple", "ghost": "x"}'
    assert validate_and_reorganize(raw, allowed) == {"surface": "dimple"}


# ── tests: feature flag ─────────────────────────────────────────────────

def test_disabled_flag_skips_interpreter():
    # ALT3: the feature flag is injected via constructor, so tests need not
    # mutate global settings.
    interp = _FakeRemarkInterpreter({"surface": "dimple"})
    ad = SpecParseAndConvertAdapter(remark_interpreter=interp, enabled=False)
    res = ad.parse_and_convert(ocr_text=_OCR_WITH_REMARK)
    assert interp.invoked is False
    assert res["params"].get("surface") == "flat"


def test_canonical_whitelist_covers_convert_all_params_keys():
    # Regression guard: every params key convert_all() reads must be in the
    # canonical whitelist (so a remark can introduce any field the main parse
    # missed). If convert_all() grows a new key, add it to CANONICAL_REMARK_FIELDS.
    import re as _re
    from app.domain.quotation.remark_adjustment import CANONICAL_REMARK_FIELDS
    spec_path = _PROJECT_ROOT / "app" / "integrations" / "pdm_matcher" / "spec_converter.py"
    src = spec_path.read_text(encoding="utf-8")
    # Restrict to the convert_all function body so we don't pick up helper keys.
    fn_src = src[src.index("def convert_all("):]
    keys = set(_re.findall(r'"([a-z_]+)" in params', fn_src))
    keys |= set(_re.findall(r'params\["([a-z_]+)"\]', fn_src))
    keys |= set(_re.findall(r'params\.get\(["\']([a-z_]+)["\']', fn_src))
    # model_full/model_num/_row_supplements are internal bookkeeping, not adjustable.
    keys -= {"model_full", "model_num", "_row_supplements"}
    missing = keys - CANONICAL_REMARK_FIELDS
    assert not missing, f"convert_all keys missing from CANONICAL_REMARK_FIELDS: {sorted(missing)}"


def test_no_interpreter_injected_skips_stage():
    # Adapter constructed without an interpreter: behaves like the original pipeline.
    ad = SpecParseAndConvertAdapter(remark_interpreter=None)
    res = ad.parse_and_convert(ocr_text=_OCR_WITH_REMARK)
    assert res["params"].get("surface") == "flat"


# ── standalone runner ───────────────────────────────────────────────────

def _run_all() -> None:
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
