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
        "score": result.score,
        "extensions": result.output.extensions,
        "is_text": result.output.is_text,
    }


def extract_metadata(file_path: str, file_bytes: bytes, **kwargs) -> dict:
    """Extract file metadata — all keys from exiftool (if available), plus basic stats."""
    ext = os.path.splitext(file_path)[1].lower()
    info: dict = {}
    try:
        stat = os.stat(file_path)
        info["file_size"] = len(file_bytes)
        import datetime
        info["modified"] = datetime.datetime.fromtimestamp(
            stat.st_mtime, tz=datetime.timezone.utc
        ).isoformat()
        info["created"] = datetime.datetime.fromtimestamp(
            stat.st_ctime, tz=datetime.timezone.utc
        ).isoformat()
    except (OSError, ValueError):
        info["file_size"] = len(file_bytes)
    # Try to get page count for PDFs
    if ext == ".pdf":
        try:
            import fitz
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            info["page_count"] = doc.page_count
            doc.close()
        except Exception:
            pass
    # Enrich with exiftool metadata (all keys, no filtering)
    try:
        exiftool_path = kwargs.get("exiftool_path")
        if exiftool_path:
            from .converters._exiftool import exiftool_metadata
            raw = exiftool_metadata(file_bytes, exiftool_path=exiftool_path, file_path=file_path)
            if raw:
                info.update(raw)
    except Exception:
        pass
    return info


def extract_text(file_path: str, file_bytes: bytes, pages_spec_str: Optional[str] = None,
                 enable_ocr: bool = False, **kwargs) -> str:
    """Extract markdown text from document (plain text files).

    Content is limited to 1MB. For files that fail markitdown conversion
    (e.g. unknown type or non-UTF-8 encoding), detects encoding with chardet
    and falls back to plain text extraction.

    Note: when called through run_extraction for ``file_group=="text"``,
    a raw-text path (1MB+chardet) is used instead of this function.
    """
    # Limit content to 1MB
    MAX_TEXT_SIZE = 1_048_576
    if len(file_bytes) > MAX_TEXT_SIZE:
        file_bytes = file_bytes[:MAX_TEXT_SIZE]

    ext = os.path.splitext(file_path)[1].lower()

    # Try normal markitdown conversion first
    try:
        result = route_document(
            file_path=file_path,
            file_bytes=file_bytes,
            extension=ext,
            enable_ocr=enable_ocr,
            pages_spec_str=pages_spec_str,
            **kwargs
        )
        if result and result.strip():
            return result
    except Exception:
        pass

    # Fallback: detect encoding and extract as plain text
    # Handles GBK (Chinese), Shift-JIS (Japanese), and other non-UTF-8 encodings
    return _extract_text_raw(file_bytes)


def _extract_text_raw(file_bytes: bytes) -> str:
    """Raw plain-text extraction: 1MB limit + encoding detection via chardet.

    Used for ``file_group=="text"`` files — bypasses the MarkItDown pipeline
    and directly decodes the raw bytes with automatic encoding detection.
    """
    MAX_TEXT_SIZE = 1_048_576
    if len(file_bytes) > MAX_TEXT_SIZE:
        file_bytes = file_bytes[:MAX_TEXT_SIZE]
    try:
        import chardet
        detection = chardet.detect(file_bytes)
        encoding = detection.get("encoding") or "utf-8"
        return file_bytes.decode(encoding, errors="replace")
    except Exception:
        try:
            return file_bytes.decode("utf-8", errors="replace")
        except Exception:
            return ""


def extract_document(file_path: str, file_bytes: bytes, pages_spec_str: Optional[str] = None,
                     enable_ocr: bool = False, **kwargs) -> str:
    """Extract markdown text from PDF/Office documents.

    For PDFs (including ``_pre_pdf`` for Office files), only the text layer
    is extracted  — a lightweight fitz get_text() — which is much cheaper
    than the full markitdown pipeline and produces different output from OCR.
    """
    if "_pre_pdf" in kwargs:
        # Reuse pre-converted PDF via markitdown pipeline (not fitz text layer).
        # For PPTX with images, LO→PDF embeds images as PDF pages — OCR will
        # Tesseract them, document won't, so outputs naturally differ.
        from ._router import route_document
        return route_document(
            file_path=file_path,
            file_bytes=kwargs["_pre_pdf"],
            extension=".pdf",
            enable_ocr=False,
            pages_spec_str=pages_spec_str,
        )
    ext = os.path.splitext(file_path)[1].lower()
    from ._router import route_document
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
    LibreOffice again. Routes via the full markitdown OCR pipeline
    (PdfConverterWithOCR) which adds ``*[Image OCR]*`` markers, page headers,
    and interleaves extracted text with OCR'd image text — producing output
    distinct from extract_document's non-OCR pipeline.
    """
    if "_pre_pdf" in kwargs:
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
    # Image files: OCR directly with Tesseract
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".avif"}
    ext = os.path.splitext(file_path)[1].lower()
    if ext in image_exts:
        try:
            from markitdown_ocr._tesseract_service import TesseractOCRService
            from PIL import Image as PILImage
            import io
            svc = TesseractOCRService(
                tesseract_path=kwargs.get("tesseract_path"),
                lang=ocr_lang,
            )
            if not svc.available:
                return ""
            img = PILImage.open(io.BytesIO(file_bytes))
            # Convert to RGB if needed (RGBA → RGB for jpg/webp output compatibility)
            if img.mode not in ("L", "RGB"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            result = svc.extract_text(buf)
            return result.text or ""
        except ImportError:
            # TesseractOCRService not available
            return ""
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
    "metadata": lambda fp, fb, **kw: extract_metadata(fp, fb, **kw),
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
    exiftool_path: Optional[str] = None,
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
    if exiftool_path:
        kwargs["exiftool_path"] = exiftool_path

    # Determine file group via magika
    file_group = "unknown"
    file_is_text = False
    try:
        m = _get_magika()
        r = m.identify_bytes(file_bytes)
        file_group = r.output.group
        file_is_text = r.output.is_text
    except Exception:
        pass

    # If magika says "unknown", try encoding detection — it might be a
    # non-UTF-8 text file (GBK, Shift-JIS, etc.) that magika couldn't label.
    if file_group == "unknown":
        try:
            import chardet
            det = chardet.detect(file_bytes[:min(len(file_bytes), 1_048_576)])
            enc = det.get("encoding")
            conf = det.get("confidence", 0)
            # Accept text encodings with reasonable confidence
            if enc and conf and conf > 0.5:
                file_group = "text"
        except Exception:
            pass

    # Build set of indicators to skip based on file type.
    # text / document are mutually exclusive — only one runs.
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".avif"}
    skip_indicators: set[str] = set()
    if file_group == "document":
        # Document files: run document, skip text
        if "text" in extract_list:
            skip_indicators.add("text")
    elif file_group == "text":
        # Text files: run text (raw 1MB extraction), skip document
        if "document" in extract_list:
            skip_indicators.add("document")
    else:
        # Other files (audio, video, image, binary): skip both text + document
        for skip in ("text", "document"):
            if skip in extract_list:
                skip_indicators.add(skip)
    # Keep ocr only for document files and images; skip for text and other types
    if file_group != "document" and ext not in image_exts and "ocr" in extract_list:
        skip_indicators.add("ocr")
    # Thumbnail only makes sense for document-type files
    if file_group != "document" and "thumbnail" in extract_list:
        skip_indicators.add("thumbnail")

    # Filter extract_list for logging
    filtered_extract = [i for i in extract_list if i not in skip_indicators]
    results["extract"] = filtered_extract  # report what was actually processed

    # Optimisation: when OCR is requested on an Office file, pre-convert to
    # PDF once and reuse for OCR, document (text extraction via PDF), and
    # thumbnail (first-page render).  Without OCR, native extraction is used
    # for all indicators — no LibreOffice needed, full content is returned
    # regardless of --pages (native converters don't support page selection).
    ext = os.path.splitext(file_path)[1].lower()
    office_exts = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods"}
    pre_pdf: bytes | None = None
    needs_lo = "ocr" in extract_list
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
        for name in filtered_extract:
            # For text files (file_group == "text"), use raw text extraction
            # which bypasses the markitdown pipeline — just 1MB + chardet.
            if name == "text" and file_group == "text":
                extract_fn = lambda fp, fb, **kw: _extract_text_raw(fb)
            else:
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
    exiftool_path: Optional[str] = None,
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
        exiftool_path=exiftool_path,
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
