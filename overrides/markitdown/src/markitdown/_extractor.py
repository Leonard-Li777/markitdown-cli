"""
Parallel extraction engine — extracts multiple indicators from a document
simultaneously using ThreadPoolExecutor.

Supported indicators:
  text       — plain text files (magika group: text)
  document   — PDF / Office documents (magika group: document)
  ocr, html, metadata, magika, thumbnail
"""

from __future__ import annotations

import base64
import io
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

import magika

from ._router import route_document, lightweight_xlsx_text_extract
from ._thumbnail import extract_thumbnails

_MAGIKA = None


def _get_magika():
    global _MAGIKA
    if _MAGIKA is None:
        _MAGIKA = magika.Magika()
    return _MAGIKA


# ---------------------------------------------------------------------------
# Individual extraction functions
# ---------------------------------------------------------------------------

def extract_magika(file_bytes: bytes) -> dict:
    """Identify file type using magika."""
    m = _get_magika()
    result = m.identify_bytes(file_bytes)
    return {
        "label": result.output.label,
        "mime_type": result.output.mime_type,
        "description": result.output.description,
        "group": result.output.group,
        "extensions": result.output.extensions,
    }


def extract_metadata(file_path: str, file_bytes: bytes) -> dict:
    """Extract basic file metadata."""
    info = {"title": None, "author": None, "page_count": None,
            "file_size": len(file_bytes), "created": None, "modified": None}
    try:
        stat = os.stat(file_path)
        import datetime
        info["modified"] = datetime.datetime.fromtimestamp(
            stat.st_mtime, tz=datetime.timezone.utc
        ).isoformat()
        info["created"] = datetime.datetime.fromtimestamp(
            stat.st_ctime, tz=datetime.timezone.utc
        ).isoformat()
    except (OSError, ValueError):
        pass
    # Try to get page count for PDFs
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        try:
            import fitz
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            info["page_count"] = doc.page_count
            doc.close()
        except Exception:
            pass
    return info


def extract_text(file_path: str, file_bytes: bytes, pages_spec_str: Optional[str] = None,
                 enable_ocr: bool = False, **kwargs) -> str:
    """Extract markdown text from document (plain text files)."""
    ext = os.path.splitext(file_path)[1].lower()
    return route_document(
        file_path=file_path,
        file_bytes=file_bytes,
        extension=ext,
        enable_ocr=enable_ocr,
        pages_spec_str=pages_spec_str,
        **kwargs
    )


def extract_document(file_path: str, file_bytes: bytes, pages_spec_str: Optional[str] = None,
                     enable_ocr: bool = False, **kwargs) -> str:
    """Extract markdown text from PDF/Office documents.

    If ``_pre_pdf`` is provided (pre-converted PDF bytes from a sibling
    ``ocr`` extraction), reuse it instead of running native extraction.
    """
    if "_pre_pdf" in kwargs:
        # Reuse the pre-converted PDF — route as PDF without OCR
        return route_document(
            file_path=file_path,
            file_bytes=kwargs["_pre_pdf"],
            extension=".pdf",
            enable_ocr=False,
            pages_spec_str=pages_spec_str,
        )
    ext = os.path.splitext(file_path)[1].lower()
    return route_document(
        file_path=file_path,
        file_bytes=file_bytes,
        extension=ext,
        enable_ocr=enable_ocr,
        pages_spec_str=pages_spec_str,
        **kwargs
    )


def extract_ocr(file_path: str, file_bytes: bytes, pages_spec_str: Optional[str] = None,
                ocr_lang: str = "eng+chi_sim", **kwargs) -> str:
    """Extract text with OCR enabled.

    If ``_pre_pdf`` is provided, reuse it directly instead of going through
    LibreOffice again.
    """
    if "_pre_pdf" in kwargs:
        # Reuse the pre-converted PDF — route as PDF with OCR enabled
        from ._router import route_document
        extra = {"ocr_engine": "tesseract", "tesseract_lang": ocr_lang}
        extra.update({k: v for k, v in kwargs.items() if k in ("tesseract_path", "ocr_engine")})
        return route_document(
            file_path=file_path,
            file_bytes=kwargs["_pre_pdf"],
            extension=".pdf",
            enable_ocr=True,
            pages_spec_str=pages_spec_str,
            **extra,
        )
    kwargs["ocr_engine"] = "tesseract"
    kwargs["tesseract_lang"] = ocr_lang
    return extract_text(file_path, file_bytes, pages_spec_str, enable_ocr=True, **kwargs)


def extract_html(file_path: str, file_bytes: bytes, pages_spec_str: Optional[str] = None,
                 **kwargs) -> str:
    """Extract HTML from document."""
    from ._html_output import convert_to_html
    return convert_to_html(
        file_path=file_path,
        file_bytes=file_bytes,
        pages_spec_str=pages_spec_str,
        **kwargs
    )


def extract_thumbnail(file_path: str, file_bytes: bytes,
                      fmt: str = "png", dpi: int = 150):
    """Extract first-page thumbnail as raw image bytes.

    Returns raw bytes on success, or a dict ``{"error": "..."}`` on failure.
    """
    try:
        images = extract_thumbnails(
            file_path=file_path,
            pages_spec="1",
            fmt=fmt,
            dpi=dpi,
        )
        if images:
            key = 1 if 1 in images else next(iter(images))
            return images[key]
        return {"error": "extract_thumbnails returned empty"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Parallel extraction orchestrator
# ---------------------------------------------------------------------------

EXTRACTORS = {
    "magika":   lambda fp, fb, **kw: extract_magika(fb),
    "metadata": lambda fp, fb, **kw: extract_metadata(fp, fb),
    "text":     lambda fp, fb, **kw: extract_text(fp, fb, **kw),
    "document": lambda fp, fb, **kw: extract_document(fp, fb, **kw),
    "ocr":      lambda fp, fb, **kw: extract_ocr(fp, fb, **kw),
    "html":     lambda fp, fb, **kw: extract_html(fp, fb, **kw),
    "thumbnail": lambda fp, fb, **kw: extract_thumbnail(fp, fb, fmt=kw.get("thumbnail_format", "png")),
}


# Magika group → compatible indicators
_GROUP_INDICATORS = {
    "text":     {"text", "html", "magika", "metadata", "thumbnail"},
    "document": {"document", "ocr", "html", "magika", "metadata", "thumbnail"},
    "image":    {"magika", "metadata", "thumbnail"},
    "audio":    {"magika", "metadata"},
    "video":    {"magika", "metadata"},
    "unknown":  {"magika", "metadata"},
}


def run_extraction(
    file_path: str,
    file_bytes: bytes,
    extract_list: list[str],
    pages_spec_str: Optional[str] = None,
    ocr_lang: str = "eng+chi_sim",
    thumbnail_format: str = "png",
    max_workers: int = 4,
) -> dict:
    """
    Run multiple extractors in parallel and return a combined result dict.
    """
    t_start = time.time()
    ext = os.path.splitext(file_path)[1].lower()
    results: dict[str, Any] = {
        "status": "ok",
        "time_ms": 0,
        "file": {
            "name": os.path.basename(file_path),
            "size": len(file_bytes),
        },
        "extract": extract_list,
        "pages": pages_spec_str,
    }

    # File-level info
    if "metadata" in extract_list or ext == ".pdf":
        try:
            import fitz
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            results["file"]["pages"] = doc.page_count
            doc.close()
        except Exception:
            pass

    kwargs = {
        "pages_spec_str": pages_spec_str,
        "ocr_lang": ocr_lang,
        "thumbnail_format": thumbnail_format,
    }

    # Determine file group via magika and filter incompatible indicators
    file_group = "unknown"
    try:
        m = _get_magika()
        r = m.identify_bytes(file_bytes)
        file_group = r.output.group
    except Exception:
        pass

    # Filter extract_list: only keep indicators compatible with this file's group
    _indicator_alias = {}
    for ind in extract_list:
        if ind == "text" and file_group != "text":
            _indicator_alias[ind] = "document"
        elif ind == "document" and file_group == "text":
            _indicator_alias[ind] = "text"

    # Optimisation: when both document and ocr are requested on an Office
    # file, pre-convert to PDF once and reuse the result for both indicators.
    # This avoids running LibreOffice twice.
    ext = os.path.splitext(file_path)[1].lower()
    office_exts = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods"}
    pre_pdf: bytes | None = None
    needs_lo = ("document" in extract_list or "ocr" in extract_list or "html" in extract_list)
    if ext in office_exts and needs_lo:
        try:
            from ._pdf_output import office_to_pdf
            pages_spec = None
            if pages_spec_str:
                from ._page_range import parse_pages, resolve
                spec = parse_pages(pages_spec_str)
                if spec:
                    import fitz
                    doc = fitz.open(stream=file_bytes, filetype=ext.strip(".") if ext != ".pdf" else "pdf")
                    resolved = resolve(spec, doc.page_count)
                    doc.close()
                    if resolved is not None and len(resolved) < doc.page_count * 0.5:
                        pages_spec = resolved
            pre_pdf = office_to_pdf(file_path, pages_spec=pages_spec)
        except Exception:
            pass

    # Parallel execution
    if pre_pdf is not None:
        kwargs["_pre_pdf"] = pre_pdf
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {}
        for name in extract_list:
            extract_fn = EXTRACTORS.get(name)
            if extract_fn:
                future = pool.submit(extract_fn, file_path, file_bytes, **kwargs)
                future_map[future] = name

        # Collect results
        result_data: dict = {}
        magika_data = None
        meta_data = None
        thumb_data = None

        for future in as_completed(future_map):
            name = future_map[future]
            try:
                value = future.result()
            except Exception as e:
                value = {"error": f"Thread '{name}': {type(e).__name__}: {e}"}

            if name == "magika":
                magika_data = value
            elif name == "metadata":
                meta_data = value
            elif name == "thumbnail":
                thumb_data = value
            elif name in ("text", "document", "ocr", "html"):
                result_data[name] = value

    # Assemble response
    if magika_data:
        results["magika"] = magika_data
    if meta_data:
        results["metadata"] = meta_data
    if result_data:
        res = {}
        for name, content in result_data.items():
            if isinstance(content, str):
                res[name] = {"content": content, "length": len(content)}
            else:
                res[name] = content
        if pages_spec_str:
            try:
                from ._page_range import parse_pages, resolve
                spec = parse_pages(pages_spec_str)
                if spec and ext == ".pdf":
                    try:
                        import fitz
                        doc = fitz.open(stream=file_bytes, filetype="pdf")
                        resolved = resolve(spec, doc.page_count)
                        doc.close()
                        if resolved:
                            res["pages_processed"] = len(resolved)
                    except Exception:
                        pass
            except Exception:
                pass
        results["result"] = res

    if thumb_data:
        if isinstance(thumb_data, bytes):
            results["thumbnail"] = {
                "format": thumbnail_format,
                "dpi": 150,
                "data": base64.b64encode(thumb_data).decode("ascii"),
            }
        elif isinstance(thumb_data, dict):
            results["thumbnail"] = thumb_data

    results["time_ms"] = int((time.time() - t_start) * 1000)
    return results


def extract_to_json(
    file_path: str,
    file_bytes: bytes,
    extract_list: list[str],
    pages_spec_str: Optional[str] = None,
    ocr_lang: str = "eng+chi_sim",
    thumbnail_format: str = "png",
    output_paths: Optional[dict[str, str]] = None,
    max_workers: int = 4,
) -> dict:
    """
    Run extraction and apply output-path overrides.
    If an output path is specified for an indicator, the content is written
    to file and the JSON field gets `path=<path>, content=null`.
    """
    result = run_extraction(
        file_path=file_path,
        file_bytes=file_bytes,
        extract_list=extract_list,
        pages_spec_str=pages_spec_str,
        ocr_lang=ocr_lang,
        thumbnail_format=thumbnail_format,
        max_workers=max_workers,
    )

    output_paths = output_paths or {}

    # Apply text/document/ocr/html path overrides
    res = result.get("result", {})
    for key in ("text", "document", "ocr", "html"):
        if key in res and key in output_paths:
            out_path = output_paths[key]
            content = res[key].get("content", "")
            if content:
                os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(content)
                res[key] = {"content": None, "length": len(content), "path": out_path}

    # Apply thumbnail path override
    thumb = result.get("thumbnail")
    if thumb and "thumbnail" in output_paths:
        out_path = output_paths["thumbnail"]
        data = thumb.pop("data", None)
        if data:
            raw = base64.b64decode(data)
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(raw)
            thumb["path"] = out_path
            thumb.pop("data", None)

    # Apply metadata/magika path overrides (write JSON fragment)
    for key in ("metadata", "magika"):
        if key in result and key in output_paths:
            out_path = output_paths[key]
            import json
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result[key], f, ensure_ascii=False, indent=2)
            result[key] = {"path": out_path}

    return result
