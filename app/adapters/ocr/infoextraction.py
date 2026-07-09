import json
import threading
import httpx
from typing import Dict, Any, Optional, List, Callable
import re
import time

from app.core.config import settings
from app.core.http_client import get_http_client
from app.core.logging import get_logger

logger = get_logger("ocr.infoextraction")


# Module-level lazy shared sync httpx.Client for the OCR dotsocr endpoint.
# Previously extract_layout_info opened a new Client (new TCP/TLS pool) per call,
# which under concurrent quotation tasks caused heavy handshake overhead. The
# shared client reuses connections. Created lazily on first use (worker thread);
# guarded by a lock. Per-call timeout still applied via client.post(timeout=...).
_sync_ocr_client: Optional[httpx.Client] = None
_sync_ocr_client_lock = threading.Lock()


def _get_sync_ocr_client() -> httpx.Client:
    global _sync_ocr_client
    if _sync_ocr_client is None:
        with _sync_ocr_client_lock:
            if _sync_ocr_client is None:
                _sync_ocr_client = httpx.Client(
                    limits=httpx.Limits(max_connections=20, max_keepalive=10),
                )
    return _sync_ocr_client


def close_sync_ocr_client() -> None:
    """Close the shared sync OCR client (call on application shutdown)."""
    global _sync_ocr_client
    if _sync_ocr_client is not None:
        try:
            _sync_ocr_client.close()
        except Exception:
            pass
        _sync_ocr_client = None


def _ocr_payload(image_url: str) -> dict:
    prompt_text = (
        "Please output the layout information from the PDF image, including each layout element's bbox, "
        "its category, and the corresponding text content within the bbox.\n\n"
        "1. Bbox format: [x1, y1, x2, y2]\n\n"
        "2. Layout Categories: The possible categories are ['Caption', 'Footnote', 'Formula', 'List-item', "
        "'Page-footer', 'Page-header', 'Picture', 'Section-header', 'Table', 'Text', 'Title'].\n\n"
        "3. Text Extraction & Formatting Rules:\n"
        "    - Picture: For the 'Picture' category, the text field should be omitted.\n"
        "    - Formula: Format its text as LaTeX.\n"
        "    - Table: Format its text as HTML.\n"
        "    - All Others (Text, Title, etc.): Format their text as Markdown.\n\n"
        "4. Constraints:\n"
        "    - The output text must be the original text from the image, with no translation.\n"
        "    - All layout elements must be sorted according to human reading order.\n\n"
        "5. Final Output: The entire output must be a single JSON object.\n"
    )
    return {
        "model": "rednote-hilab/dots.ocr",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
    }


def _parse_ocr_response(response: httpx.Response) -> List[Dict[str, Any]]:
    if not response.is_success:
        detail = (response.text or "").strip()
        if len(detail) > 600:
            detail = detail[:600] + "..."
        raise RuntimeError(f"OCR API {response.status_code} error: {detail}")
    result = response.json()
    content_str = result["choices"][0]["message"]["content"]
    return json.loads(content_str)


async def async_extract_layout_info(
    image_url: str,
    api_url: str = "http://localhost:80/ocr/dotsocr/v1/chat/completions",
    cancel_checker: Optional[Callable[[], bool]] = None,
    connect_timeout: Optional[float] = None,
    read_timeout: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """POST chat/completions; parse message content JSON to layout list. Uses HTTP timeouts; cancel_checker runs before the request only."""
    if cancel_checker and cancel_checker():
        from app.domain.quotation.exceptions import QuotationPipelineCancelledError

        raise QuotationPipelineCancelledError("任务已取消")

    ct = connect_timeout if connect_timeout is not None else settings.OCR_HTTP_CONNECT_TIMEOUT
    rt = read_timeout if read_timeout is not None else settings.OCR_HTTP_READ_TIMEOUT

    headers = {"Content-Type": "application/json"}
    payload = _ocr_payload(image_url)

    try:
        client = await get_http_client()
        response = await client.post(
            api_url, headers=headers, json=payload, timeout=httpx.Timeout(rt, connect=ct)
        )
    except httpx.TimeoutException as e:
        raise RuntimeError(
            f"OCR API request timed out (connect={ct}s, read={rt}s): {e}"
        ) from e
    return _parse_ocr_response(response)


def extract_layout_info(
    image_url: str,
    api_url: str = "http://localhost:80/ocr/dotsocr/v1/chat/completions",
    cancel_checker: Optional[Callable[[], bool]] = None,
    connect_timeout: Optional[float] = None,
    read_timeout: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Sync wrapper for worker threads — uses sync httpx.Client to avoid cross-loop issues."""
    if cancel_checker and cancel_checker():
        from app.domain.quotation.exceptions import QuotationPipelineCancelledError

        raise QuotationPipelineCancelledError("任务已取消")

    ct = connect_timeout if connect_timeout is not None else settings.OCR_HTTP_CONNECT_TIMEOUT
    rt = read_timeout if read_timeout is not None else settings.OCR_HTTP_READ_TIMEOUT

    headers = {"Content-Type": "application/json"}
    payload = _ocr_payload(image_url)

    try:
        client = _get_sync_ocr_client()
        response = client.post(
            api_url, headers=headers, json=payload,
            timeout=httpx.Timeout(rt, connect=ct),
        )
    except httpx.TimeoutException as e:
        raise RuntimeError(
            f"OCR API request timed out (connect={ct}s, read={rt}s): {e}"
        ) from e
    return _parse_ocr_response(response)


def extract_info(content: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Parse first Table HTML into meta/documents/spec and scrape non-table blocks."""
    from bs4 import BeautifulSoup
    
    table_element = None
    for element in content:
        if element.get("category") == "Table" and "text" in element:
            table_element = element
            break
    
    if not table_element:
        raise ValueError("No table found in content")
    
    soup = BeautifulSoup(table_element["text"], "html.parser")
    rows = soup.find_all("tr")
    
    result = {
        "meta": {},
        "documents": {},
        "spec": {},
        "regulation": "",
        "name_plate": {},
        "optional_spare_parts": "",
        "display_language": {},
        "remarks": "",
        "additional_info": []
    }
    
    # Track state for parsing
    in_spec_section = False
    display_lang_count = 0
    
    for i, row in enumerate(rows):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        
        cell_texts = [cell.get_text(strip=True) for cell in cells]
        
        if i == 0 and "Date" in cell_texts:
            result["meta"]["date"] = cell_texts[1] if len(cell_texts) > 1 else ""
            if len(cell_texts) > 2 and "Number of documents" in cell_texts:
                result["documents"]["number_of_documents"] = {
                    "to": cell_texts[3] if len(cell_texts) > 3 else "",
                    "ship_with_dw": cell_texts[4] if len(cell_texts) > 4 else "",
                    "note": cell_texts[5] if len(cell_texts) > 5 else ""
                }
            continue
        
        if "Work No." in cell_texts:
            result["meta"]["work_no"] = cell_texts[1] if len(cell_texts) > 1 else ""
        elif "Model" in cell_texts and "Work No." not in cell_texts:
            result["meta"]["model"] = cell_texts[1] if len(cell_texts) > 1 else ""
        elif "Controller" in cell_texts:
            result["meta"]["controller"] = cell_texts[1] if len(cell_texts) > 1 else ""
        elif "Subsidiary/ Agent" in cell_texts or "Subsidiary/Agent" in cell_texts:
            result["meta"]["subsidiary_agent"] = cell_texts[1] if len(cell_texts) > 1 else ""
        elif "End-user" in cell_texts and "country" not in cell_texts[0].lower():
            result["meta"]["end_user"] = cell_texts[1] if len(cell_texts) > 1 else ""
        elif "End-user country" in cell_texts:
            result["meta"]["end_user_country"] = cell_texts[1] if len(cell_texts) > 1 else ""
        elif "Destination port" in cell_texts:
            result["meta"]["destination_port"] = cell_texts[1] if len(cell_texts) > 1 else ""
        elif "Ex-factory date" in cell_texts:
            result["meta"]["ex_factory_date"] = cell_texts[1] if len(cell_texts) > 1 else ""
        
        elif "Final drawing" in cell_texts:
            result["documents"]["final_drawing"] = {
                "required": cell_texts[2] if len(cell_texts) > 2 else "",
                "ship_with_dw": cell_texts[3] if len(cell_texts) > 3 else ""
            }
        elif "Parts (EN.)" in cell_texts:
            result["documents"]["parts_en"] = {
                "format": cell_texts[2] if len(cell_texts) > 2 else "",
                "delivery": cell_texts[3] if len(cell_texts) > 3 else ""
            }
        elif "Check data sheet" in cell_texts:
            result["documents"]["check_data_sheet"] = {
                "required": cell_texts[2] if len(cell_texts) > 2 else ""
            }
        elif "Installation (EN.)" in cell_texts:
            result["documents"]["installation_en"] = {
                "required": cell_texts[3] if len(cell_texts) > 3 else ""
            }
        elif "Setup/ Operation" in cell_texts or "Setup/Operation" in cell_texts:
            if "setup_operation" not in result["documents"]:
                result["documents"]["setup_operation"] = {
                    "required": cell_texts[3] if len(cell_texts) > 3 else ""
                }
        
        elif len(cell_texts) > 0 and cell_texts[0].isdigit():
            in_spec_section = True
            idx = cell_texts[0]
            key_name = cell_texts[1] if len(cell_texts) > 1 else ""
            key = f"{idx}_{_normalize_key(key_name)}"
            
            value_data = {"value": cell_texts[2] if len(cell_texts) > 2 else ""}
            
            if len(cell_texts) > 3 and cell_texts[3]:
                if "↔" in cell_texts[3]:
                    value_data["alt"] = cell_texts[3]
                elif cell_texts[3].lower() not in ["", "no", "yes"]:
                    value_data["note"] = cell_texts[3]
                else:
                    value_data["note"] = cell_texts[3]
            if len(cell_texts) > 5 and cell_texts[5]:
                value_data["discharge"] = cell_texts[5]
            
            result["spec"][key] = value_data
        
        elif in_spec_section and len(cell_texts) > 0 and cell_texts[0] == "" and len(cell_texts) > 1:
            key_name = cell_texts[1]
            if key_name and key_name not in ["Software", "Regulation", "Display language(s)"]:
                key = _normalize_key(key_name)
                value_data = {"value": cell_texts[2] if len(cell_texts) > 2 else ""}
                result["spec"][key] = value_data
        
        elif "Regulation" in cell_texts:
            if len(cell_texts) > 2 and cell_texts[2] and not result["regulation"]:
                result["regulation"] = cell_texts[2]
        
        elif "Name plate" in cell_texts:
            result["name_plate"]["code"] = cell_texts[2] if len(cell_texts) > 2 else ""
        
        elif "Optional spare parts" in cell_texts:
            result["optional_spare_parts"] = cell_texts[2] if len(cell_texts) > 2 else ""
        
        elif "Display language(s)" in cell_texts:
            if display_lang_count == 0:
                result["display_language"]["primary"] = cell_texts[2] if len(cell_texts) > 2 else ""
            elif display_lang_count == 1:
                result["display_language"]["secondary"] = cell_texts[2] if len(cell_texts) > 2 else ""
            display_lang_count += 1
        
        elif "Printer" in cell_texts:
            result["name_plate"]["whole"] = cell_texts[2] if len(cell_texts) > 2 else ""
        
        elif "Operation" in cell_texts and len(cell_texts) > 2:
            result["display_language"]["secondary_required"] = cell_texts[2] if len(cell_texts) > 2 else "No"
    
    if "primary" in result["display_language"]:
        result["name_plate"]["language"] = result["display_language"]["primary"]
    
    for element in content:
        category = element.get("category")
        if category and category not in ["Table", "Picture"]:
            element_info = {
                "category": category,
                "bbox": element.get("bbox", [])
            }
            if "text" in element and element["text"]:
                element_info["text"] = element["text"]
            
            if "text" in element_info:
                result["additional_info"].append(element_info)
                
                text = element["text"]
                if any(keyword in text for keyword in ["同种规格做", "台", "WG", "规格"]):
                    if result["remarks"]:
                        result["remarks"] += " " + text
                    else:
                        result["remarks"] = text
    
    return result


def _normalize_key(text: str) -> str:
    """Lowercase, strip punctuation, spaces to underscores."""
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s-]+', '_', text)
    return text


def validate_extracted_info(info: Dict[str, Any]) -> tuple[bool, List[str]]:
    """Require non-empty meta and spec dicts."""
    empty_sections = []
    
    if "meta" not in info:
        empty_sections.append("meta (section missing)")
    elif not info["meta"] or not isinstance(info["meta"], dict):
        empty_sections.append("meta (section is empty or invalid)")
    
    if "spec" not in info:
        empty_sections.append("spec (section missing)")
    elif not info["spec"] or not isinstance(info["spec"], dict):
        empty_sections.append("spec (section is empty or invalid)")
    
    is_valid = len(empty_sections) == 0
    return is_valid, empty_sections


def extract_info_with_retry(image_url: str, api_url: str = "http://localhost:80/ocr/dotsocr/v1/chat/completions", max_retries: int = 3) -> Dict[str, Any]:
    """Retry until validate_extracted_info passes or raise ValueError with Chinese message."""
    attempt = 0
    last_empty_sections = []
    
    while attempt < max_retries:
        if attempt > 0:
            time.sleep(3)
        
        attempt += 1
        
        try:
            content = extract_layout_info(image_url, api_url)
            
            info = extract_info(content)
            
            is_valid, empty_sections = validate_extracted_info(info)
            
            if is_valid:
                return info
            else:
                last_empty_sections = empty_sections
                if attempt >= max_retries:
                    break
        
        except Exception as e:
            if attempt >= max_retries:
                raise
            logger.debug(
                "extract_info_with_retry 中间失败 attempt=%s/%s err=%s",
                attempt, max_retries, e,
            )
    
    error_msg = f"无法提取完整内容，请重新上传重试。经过 {max_retries} 次尝试后仍有以下部分缺失或为空: {', '.join(last_empty_sections)}"
    raise ValueError(error_msg)

def main():
    """Local smoke test."""
    image_url = "http://minio:9000/yamatodev/temp/61e64fdc2a464919805ff49d9ae108a8_2026-3-41111_page_001.jpg"
    
    try:
        info = extract_info_with_retry(image_url, max_retries=3)
        print(json.dumps(info, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    main()