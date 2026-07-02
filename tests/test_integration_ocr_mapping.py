"""Integration test: PDF → OCR → SpecificationMapping → keywords_payload.

Replicates the exact OCR prefix used by
`ExecuteQuotationPhase1UseCase` (before PDM):

    PDF bytes
      └─► pdf2image.pdf_to_single_image(page_number=1)
            └─► image2url.upload_file_to_minio
                  └─► infoextraction.extract_layout_info(image_url, OCR_MODEL_API_URL)
                        └─► infoextraction.extract_info(content)
                              └─► SpecificationMapping.generate_keywords_payload()

Requires MinIO and the DOTS OCR endpoint (from settings / .env) to be reachable.

Run:

    python tests/test_integration_ocr_mapping.py --pdf /path/to/input.pdf
    python tests/test_integration_ocr_mapping.py --pdf /path/to/input.pdf --save-raw out.json
    python tests/test_integration_ocr_mapping.py --help
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from time import perf_counter
from typing import Any, Dict

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.domain.quotation.specification_mapping import (  # noqa: E402
    KEYWORD_RULES,
    SpecificationMapping,
)
from app.integrations.ocr.image2url import upload_file_to_minio  # noqa: E402
from app.integrations.ocr.infoextraction import (  # noqa: E402
    extract_info,
    extract_layout_info,
)
from app.integrations.ocr.pdf2image import pdf_to_single_image  # noqa: E402


# -----------------------------------------------------------------------------
# Pipeline stages (identical to ExecuteQuotationPhase1 OCR/mapping prefix, before PDM)


def run_ocr_pipeline(
    pdf_bytes: bytes,
    original_filename: str,
    ocr_endpoint: str,
) -> Dict[str, Any]:
    """PDF → page-1 image → MinIO → OCR → extract_info → SpecificationMapping."""
    timings: Dict[str, float] = {}

    t0 = perf_counter()
    image_bytes, _ = pdf_to_single_image(pdf_bytes, dpi=200, quality=85, page_number=1)
    timings["pdf_to_image_s"] = round(perf_counter() - t0, 3)

    t0 = perf_counter()
    temp_image_minio_path = (
        f"temp/quotation/test_{uuid.uuid4().hex}_{original_filename}_page_001.jpg"
    )
    image_url = upload_file_to_minio(image_bytes, temp_image_minio_path)
    timings["minio_upload_s"] = round(perf_counter() - t0, 3)

    t0 = perf_counter()
    content = extract_layout_info(image_url, ocr_endpoint)
    timings["ocr_layout_s"] = round(perf_counter() - t0, 3)

    t0 = perf_counter()
    extracted_info = extract_info(content)
    timings["extract_info_s"] = round(perf_counter() - t0, 3)

    t0 = perf_counter()
    mapping = SpecificationMapping(extracted_info)
    keywords_payload = mapping.generate_keywords_payload(max_retries=3)
    timings["mapping_s"] = round(perf_counter() - t0, 3)

    return {
        "image_url": image_url,
        "temp_image_minio_path": temp_image_minio_path,
        "layout_content": content,
        "extracted_info": extracted_info,
        "keywords_payload": keywords_payload,
        "timings_s": timings,
    }


# -----------------------------------------------------------------------------
# Report helpers


def _print_header(text: str) -> None:
    line = "=" * 78
    print(line)
    print(text)
    print(line)


def _print_sub(text: str) -> None:
    print("-" * 78)
    print(text)
    print("-" * 78)


def _format_attr_value(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    return repr(value) if isinstance(value, str) else str(value)


def print_keywords_payload(keywords_payload: Dict[str, Any]) -> None:
    _print_sub("keywords_payload (mapping result)")
    keywords = keywords_payload.get("keywords") or []
    rule_types = [rule["type"] for rule in KEYWORD_RULES]
    by_type = {entry.get("type"): entry.get("attr") or {} for entry in keywords}

    for type_name in rule_types:
        attr = by_type.get(type_name)
        if attr is None:
            print(f"  [×] {type_name}: (no attrs resolved)")
            continue
        if not attr:
            print(f"  [!] {type_name}: {{}} (empty attr)")
            continue
        parts = ", ".join(f"{k}={_format_attr_value(v)}" for k, v in attr.items())
        print(f"  [✓] {type_name}: {{ {parts} }}")

    # Any types that appear in the payload but are not in the rule list
    extra = [t for t in by_type if t not in rule_types]
    if extra:
        print(f"\n  (extra types not in rules: {extra})")


def print_extracted_info_summary(extracted_info: Dict[str, Any]) -> None:
    _print_sub("extracted_info summary")
    meta = extracted_info.get("meta") or {}
    spec = extracted_info.get("spec") or {}
    print(f"  meta keys   : {sorted(meta.keys())}")
    print(f"  spec keys ({len(spec)}):")
    for idx, (key, payload) in enumerate(spec.items()):
        if isinstance(payload, dict):
            rendered = {k: payload.get(k) for k in ("value", "note", "alt", "discharge") if k in payload}
        else:
            rendered = payload
        print(f"    [{idx:02d}] {key} = {rendered}")


def print_timings(timings: Dict[str, float]) -> None:
    _print_sub("timings")
    total = sum(timings.values())
    for name, value in timings.items():
        print(f"  {name:<20} {value:>8.3f}s")
    print(f"  {'TOTAL':<20} {total:>8.3f}s")


# -----------------------------------------------------------------------------
# CLI


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PDF → OCR → SpecificationMapping integration test",
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        required=True,
        help="Path to the PDF file to feed through the pipeline",
    )
    parser.add_argument(
        "--ocr-endpoint",
        type=str,
        default=None,
        help=f"Override OCR_MODEL_API_URL (default from settings: {settings.OCR_MODEL_API_URL})",
    )
    parser.add_argument(
        "--save-raw",
        type=Path,
        default=None,
        help="If set, write the full pipeline result (extracted_info + keywords_payload + layout_content) to this JSON file",
    )
    parser.add_argument(
        "--save-keywords",
        type=Path,
        default=None,
        help="If set, write keywords_payload only to this JSON file",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Skip the layout_content dump and extracted_info listing",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not args.pdf.is_file():
        print(f"[error] PDF not found: {args.pdf}", file=sys.stderr)
        return 2

    endpoint = args.ocr_endpoint or settings.OCR_MODEL_API_URL

    _print_header(f"Integration test: {args.pdf}")
    print(f"OCR endpoint : {endpoint}")
    print(f"MinIO bucket : (configured via settings)")

    pdf_bytes = args.pdf.read_bytes()
    print(f"PDF size     : {len(pdf_bytes)} bytes")

    try:
        result = run_ocr_pipeline(
            pdf_bytes=pdf_bytes,
            original_filename=args.pdf.stem,
            ocr_endpoint=endpoint,
        )
    except Exception as exc:
        print(f"\n[FAIL] Pipeline raised: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

    print(f"image_url    : {result['image_url']}")

    if not args.quiet:
        print_extracted_info_summary(result["extracted_info"])

    print_keywords_payload(result["keywords_payload"])
    print_timings(result["timings_s"])

    if args.save_raw:
        args.save_raw.parent.mkdir(parents=True, exist_ok=True)
        args.save_raw.write_text(
            json.dumps(
                {
                    "image_url": result["image_url"],
                    "temp_image_minio_path": result["temp_image_minio_path"],
                    "extracted_info": result["extracted_info"],
                    "keywords_payload": result["keywords_payload"],
                    "layout_content": result["layout_content"],
                    "timings_s": result["timings_s"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n[saved] full result → {args.save_raw}")

    if args.save_keywords:
        args.save_keywords.parent.mkdir(parents=True, exist_ok=True)
        args.save_keywords.write_text(
            json.dumps(result["keywords_payload"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[saved] keywords_payload → {args.save_keywords}")

    _print_header("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
