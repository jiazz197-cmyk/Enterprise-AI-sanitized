"""Standalone FastAPI test server for OCR + SpecificationMapping.

Exposes endpoints that exercise the exact OCR prefix used by
`ExecuteQuotationPhase1UseCase` (PDF page 1 → MinIO → OCR → mapping), plus a lightweight
SpecificationMapping-only endpoint for replaying an existing extracted_info.

Runs on its own port (default 8765) so it does not collide with the main app.

Start:

    python tests/main.py                  # default 0.0.0.0:8765
    python tests/main.py --port 9000
    uvicorn tests.main:app --port 8765 --reload

Endpoints:

    GET  /health
    GET  /rules
    POST /ocr-mapping        (multipart: file=<pdf>)                → 完整 PDF → OCR → mapping
    POST /mapping            (multipart: file=<json extracted_info>) → 只跑 mapping
    POST /ocr-only           (multipart: file=<pdf>)                → 只跑 PDF → OCR → extract_info
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from time import perf_counter
from typing import Any, Dict

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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


app = FastAPI(
    title="OCR + SpecificationMapping Test Server",
    description=(
        "Tiny standalone server for trying the OCR → SpecificationMapping "
        "pipeline end-to-end. Mirrors ExecuteQuotationPhase1 OCR/mapping prefix."
    ),
    version="0.1.0",
)


# -----------------------------------------------------------------------------
# Schemas


class HealthResponse(BaseModel):
    ocr_endpoint: str
    minio_bucket: str
    rules_count: int


class RuleEntry(BaseModel):
    type: str
    attrs: list[str]


class RulesResponse(BaseModel):
    total: int
    rules: list[RuleEntry]


class MappingResponse(BaseModel):
    keywords_payload: Dict[str, Any]
    mapping_elapsed_s: float
    source_filename: str
    used_extracted_info: bool


class OcrMappingResponse(BaseModel):
    image_url: str
    temp_image_minio_path: str
    extracted_info: Dict[str, Any]
    keywords_payload: Dict[str, Any]
    timings_s: Dict[str, float]


class OcrOnlyResponse(BaseModel):
    image_url: str
    temp_image_minio_path: str
    extracted_info: Dict[str, Any]
    layout_content: Any
    timings_s: Dict[str, float]


# -----------------------------------------------------------------------------
# Helpers


def _pdf_to_image_bytes(pdf_bytes: bytes) -> bytes:
    image_bytes, _ = pdf_to_single_image(pdf_bytes, dpi=200, quality=85, page_number=1)
    return image_bytes


def _upload_image(image_bytes: bytes, stem: str) -> tuple[str, str]:
    minio_path = f"temp/quotation/test_{uuid.uuid4().hex}_{stem}_page_001.jpg"
    url = upload_file_to_minio(image_bytes, minio_path)
    return url, minio_path


async def _read_json_upload(file: UploadFile) -> Any:
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="file has no filename",
        )
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="uploaded JSON file is empty",
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"JSON file is not UTF-8 decodable: {exc}",
            ) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid JSON: {exc}",
        ) from exc


async def _read_pdf_upload(file: UploadFile) -> bytes:
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="file has no filename",
        )
    ctype = (file.content_type or "").lower()
    name_ok = file.filename.lower().endswith(".pdf")
    if "pdf" not in ctype and not name_ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"expected a PDF upload, got content_type={file.content_type!r}",
        )
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="uploaded PDF is empty",
        )
    return data


# -----------------------------------------------------------------------------
# Routes


@app.get("/health", response_model=HealthResponse, summary="Show server config")
def health() -> HealthResponse:
    from app.core.storage import MINIO_BUCKET_NAME  # local import to avoid early init

    return HealthResponse(
        ocr_endpoint=settings.OCR_MODEL_API_URL,
        minio_bucket=MINIO_BUCKET_NAME,
        rules_count=len(KEYWORD_RULES),
    )


@app.get("/rules", response_model=RulesResponse, summary="List keyword rules")
def list_rules() -> RulesResponse:
    entries = [RuleEntry(type=rule["type"], attrs=list(rule["attrs"])) for rule in KEYWORD_RULES]
    return RulesResponse(total=len(entries), rules=entries)


@app.post(
    "/mapping",
    response_model=MappingResponse,
    summary="Run SpecificationMapping against an uploaded JSON file",
)
async def run_mapping(
    file: UploadFile = File(
        ...,
        description=(
            "JSON file containing either the extracted_info dict directly "
            "(keys: meta/spec/...) or an envelope {\"extracted_info\": {...}}"
        ),
    ),
    max_retries: int = Form(3, ge=1, le=5),
) -> MappingResponse:
    raw = await _read_json_upload(file)

    # Accept either the raw extracted_info dict or an envelope {extracted_info: {...}}.
    if isinstance(raw, dict) and isinstance(raw.get("extracted_info"), dict):
        extracted_info = raw["extracted_info"]
        used_envelope = True
    elif isinstance(raw, dict):
        extracted_info = raw
        used_envelope = False
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="JSON root must be an object (extracted_info or {extracted_info: ...})",
        )

    t0 = perf_counter()
    mapping = SpecificationMapping(extracted_info)
    keywords_payload = mapping.generate_keywords_payload(max_retries=max_retries)
    return MappingResponse(
        keywords_payload=keywords_payload,
        mapping_elapsed_s=round(perf_counter() - t0, 3),
        source_filename=file.filename or "",
        used_extracted_info=used_envelope,
    )


@app.post(
    "/ocr-only",
    response_model=OcrOnlyResponse,
    summary="PDF → image → MinIO → OCR → extract_info (no mapping)",
)
async def run_ocr_only(file: UploadFile = File(..., description="PDF file")) -> OcrOnlyResponse:
    pdf_bytes = await _read_pdf_upload(file)
    stem = Path(file.filename or "document").stem

    timings: Dict[str, float] = {}
    try:
        t0 = perf_counter()
        image_bytes = _pdf_to_image_bytes(pdf_bytes)
        timings["pdf_to_image_s"] = round(perf_counter() - t0, 3)

        t0 = perf_counter()
        image_url, minio_path = _upload_image(image_bytes, stem)
        timings["minio_upload_s"] = round(perf_counter() - t0, 3)

        t0 = perf_counter()
        content = extract_layout_info(image_url, settings.OCR_MODEL_API_URL)
        timings["ocr_layout_s"] = round(perf_counter() - t0, 3)

        t0 = perf_counter()
        extracted_info = extract_info(content)
        timings["extract_info_s"] = round(perf_counter() - t0, 3)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    return OcrOnlyResponse(
        image_url=image_url,
        temp_image_minio_path=minio_path,
        extracted_info=extracted_info,
        layout_content=content,
        timings_s=timings,
    )


@app.post(
    "/ocr-mapping",
    response_model=OcrMappingResponse,
    summary="PDF → OCR → extract_info → SpecificationMapping (full pipeline prefix)",
)
async def run_ocr_mapping(file: UploadFile = File(..., description="PDF file")) -> OcrMappingResponse:
    pdf_bytes = await _read_pdf_upload(file)
    stem = Path(file.filename or "document").stem

    timings: Dict[str, float] = {}
    try:
        t0 = perf_counter()
        image_bytes = _pdf_to_image_bytes(pdf_bytes)
        timings["pdf_to_image_s"] = round(perf_counter() - t0, 3)

        t0 = perf_counter()
        image_url, minio_path = _upload_image(image_bytes, stem)
        timings["minio_upload_s"] = round(perf_counter() - t0, 3)

        t0 = perf_counter()
        content = extract_layout_info(image_url, settings.OCR_MODEL_API_URL)
        timings["ocr_layout_s"] = round(perf_counter() - t0, 3)

        t0 = perf_counter()
        extracted_info = extract_info(content)
        timings["extract_info_s"] = round(perf_counter() - t0, 3)

        t0 = perf_counter()
        keywords_payload = SpecificationMapping(extracted_info).generate_keywords_payload(
            max_retries=3
        )
        timings["mapping_s"] = round(perf_counter() - t0, 3)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    timings["total_s"] = round(sum(timings.values()), 3)

    return OcrMappingResponse(
        image_url=image_url,
        temp_image_minio_path=minio_path,
        extracted_info=extracted_info,
        keywords_payload=keywords_payload,
        timings_s=timings,
    )


@app.exception_handler(HTTPException)
async def _http_exc_handler(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status_code": exc.status_code},
    )


# -----------------------------------------------------------------------------
# CLI


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OCR + mapping test server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn autoreload")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    args = _parse_args(argv)
    uvicorn.run(
        "tests.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
