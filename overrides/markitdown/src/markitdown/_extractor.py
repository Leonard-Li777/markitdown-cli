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
import importlib
import io
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from ._router import route_document, lightweight_xlsx_text_extract
from ._thumbnail import extract_thumbnails

_MAGIKA = None


def _get_magika():
    """Lazy-load magika on demand. Returns None if magika is not available
    (excluded from PyInstaller bundle or not installed).

    NOTE: the module name is constructed from parts so PyInstaller's static
    AST scanner CANNOT detect the string literal and will not trace the
    magika → onnxruntime dependency chain, avoiding the hang on Linux.
    """
    global _MAGIKA
    if _MAGIKA is None:
        try:
            _modname = "".join(["m", "a", "g", "i", "k", "a"])
            magika_module = importlib.import_module(_modname)
            _MAGIKA = magika_module.Magika()
        except Exception:
            _MAGIKA = False
    return _MAGIKA if _MAGIKA is not False else None


# ---------------------------------------------------------------------------
# Individual extraction functions
# ---------------------------------------------------------------------------

def extract_magika(file_bytes: bytes) -> dict:
    """Identify file type using magika. Returns empty dict if magika is unavailable."""
    try:
        m = _get_magika()
        if m is None:
            return {}
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
    except Exception:
        return {}


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
        if not exiftool_path:
            import shutil, sys
            meipass = getattr(sys, "_MEIPASS", None)
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            candidates = []
            if meipass:
                candidates.extend([
                    os.path.join(meipass, "exiftool", "exiftool.exe"),
                    os.path.join(meipass, "exiftool", "exiftool"),
                    os.path.join(meipass, "exiftool.exe"),
                    os.path.join(meipass, "exiftool"),
                ])
            candidates.extend([
                os.path.join(exe_dir, "exiftool", "exiftool.exe"),
                os.path.join(exe_dir, "exiftool", "exiftool"),
                os.path.join(exe_dir, "exiftool.exe"),
                os.path.join(exe_dir, "exiftool"),
                "C:\\Program Files\\exiftool\\exiftool.exe",
            ])
            for c in candidates:
                if os.path.isfile(c):
                    exiftool_path = os.path.abspath(c)
                    break
            if not exiftool_path:
                env_p = os.environ.get("EXIFTOOL_PATH")
                if env_p and os.path.isfile(env_p):
                    exiftool_path = env_p
            if not exiftool_path:
                found = shutil.which("exiftool")
                if found:
                    exiftool_path = os.path.abspath(found)

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
    # Limit content to 10KB
    MAX_TEXT_SIZE = 10 * 1024
    if len(file_bytes) > MAX_TEXT_SIZE:
        file_bytes = file_bytes[:MAX_TEXT_SIZE]

    ext = os.path.splitext(file_path)[1].lower()

    # Try normal markitdown conversion first
    try:
        r_kwargs = {k: v for k, v in kwargs.items() if k not in ("enable_ocr", "pages_spec_str")}
        result = route_document(
            file_path=file_path,
            file_bytes=file_bytes,
            extension=ext,
            enable_ocr=enable_ocr,
            pages_spec_str=pages_spec_str,
            **r_kwargs
        )
        if result and result.strip():
            return result
    except Exception:
        pass

    # Fallback: detect encoding and extract as plain text
    # Handles GBK (Chinese), Shift-JIS (Japanese), and other non-UTF-8 encodings
    # But NOT for binary formats (DOCX, PPTX, etc.) — raw bytes are not valid text
    binary_exts = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods", ".pdf"}
    if ext in binary_exts:
        return ""
    return _extract_text_raw(file_bytes)


def _extract_text_raw(file_bytes: bytes, max_bytes: int | None = 30 * 1024) -> str:
    """Raw plain-text extraction: configurable size limit + encoding detection via chardet.

    Used for ``file_group=="text"`` files — bypasses the MarkItDown pipeline
    and directly decodes the raw bytes with automatic encoding detection.

    ``max_bytes=None``（或 ``<= 0``）表示不限制大小。
    """
    if max_bytes is not None and len(file_bytes) > max_bytes:
        file_bytes = file_bytes[:max_bytes]
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

    For PDFs, uses lightweight fitz get_text() — much cheaper than the
    full markitdown pipeline and avoids the optional [pdf] dependency.
    For Office files, routes through the normal markitdown pipeline.
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

    # For PDF files, use lightweight fitz text extraction instead of the
    # full markitdown pipeline. This avoids the optional [pdf] dependency
    # and is significantly cheaper — just the text layer, no OCR/layout.
    if ext == ".pdf":
        try:
            import fitz
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            total = doc.page_count

            # Resolve page range
            if pages_spec_str:
                from ._page_range import parse_pages, resolve
                spec = parse_pages(pages_spec_str)
                pages = resolve(spec, total) if spec else None
            else:
                pages = None

            if pages is None:
                pages = list(range(1, total + 1))
            else:
                pages = sorted(list(pages))

            parts = []
            for p in pages:
                if 1 <= p <= total:
                    page = doc[p - 1]
                    text = page.get_text()
                    if text and text.strip():
                        parts.append(text.strip())
            doc.close()
            return "\n\n".join(parts)
        except Exception:
            # Fall through to markitdown route below
            pass

    from ._router import route_document
    r_kwargs = {k: v for k, v in kwargs.items() if k not in ("enable_ocr", "pages_spec_str", "max_content_size_kb")}
    return route_document(
        file_path=file_path,
        file_bytes=file_bytes,
        extension=ext,
        enable_ocr=enable_ocr,
        pages_spec_str=pages_spec_str,
        **r_kwargs
    )


def _get_ocr_service(**kwargs):
    ocr_engine = kwargs.get("ocr_engine", "paddleocr")
    ocr_lang = kwargs.get("ocr_lang") or kwargs.get("tesseract_lang") or "eng+chi_sim"
    ocr_model_size = kwargs.get("ocr_model_size")
    tesseract_path = kwargs.get("tesseract_path")
    llm_client = kwargs.get("llm_client")
    llm_model = kwargs.get("llm_model")

    if ocr_engine == "tesseract":
        try:
            from markitdown_ocr._tesseract_service import TesseractOCRService
            svc = TesseractOCRService(
                tesseract_path=tesseract_path,
                lang=ocr_lang,
            )
            if svc.available:
                return svc
        except Exception:
            pass
        return None
    elif ocr_engine == "llm":
        if llm_client and llm_model:
            try:
                from markitdown_ocr._ocr_service import LLMVisionOCRService
                return LLMVisionOCRService(client=llm_client, model=llm_model)
            except Exception:
                pass
        return None
    else:
        # Default: paddleocr (ONNX PP-OCR) with fallback to tesseract
        try:
            from markitdown_ocr._onnx_ocr_service import ONNXPPOCRService
            onnx_svc = ONNXPPOCRService(model_size=ocr_model_size)
            if onnx_svc.available:
                return onnx_svc
        except Exception:
            pass
        try:
            from markitdown_ocr._tesseract_service import TesseractOCRService
            svc = TesseractOCRService(
                tesseract_path=tesseract_path,
                lang=ocr_lang,
            )
            if svc.available:
                return svc
        except Exception:
            pass
        return None


def extract_ocr(file_path: str, file_bytes: bytes, pages_spec_str: Optional[str] = None,
                ocr_lang: str = "eng+chi_sim", **kwargs) -> str:
    """Extract text with OCR enabled."""
    kwargs["ocr_lang"] = ocr_lang

    # If Office file pre-converted to PDF bytes, use those PDF bytes directly
    if "_pre_pdf" in kwargs and kwargs["_pre_pdf"]:
        file_bytes = kwargs["_pre_pdf"]
        ext = ".pdf"
    else:
        ext = os.path.splitext(file_path)[1].lower()

    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".avif"}
    office_exts = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods"}

    # 1. Image files: OCR directly
    if ext in image_exts:
        svc = _get_ocr_service(**kwargs)
        if svc:
            try:
                import io
                buf = io.BytesIO(file_bytes)
                res = svc.extract_text(buf)
                if res and res.text:
                    return res.text
            except Exception:
                pass
        return ""

    # 2. Office files: convert to PDF first if not pre-converted
    if ext in office_exts:
        try:
            from ._pdf_output import office_to_pdf
            from ._page_range import parse_pages
            pages_spec = parse_pages(pages_spec_str) if pages_spec_str else None
            file_bytes = office_to_pdf(file_path, pages_spec=pages_spec)
            ext = ".pdf"
        except Exception:
            return ""

    # 3. PDF files (including converted Office files): render page-by-page and run OCR
    if ext == ".pdf":
        svc = _get_ocr_service(**kwargs)
        if svc:
            try:
                import fitz
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                total = doc.page_count

                if pages_spec_str:
                    from ._page_range import parse_pages, resolve
                    spec = parse_pages(pages_spec_str)
                    pages = resolve(spec, total) if spec else None
                else:
                    pages = None

                if pages is None:
                    pages = list(range(1, total + 1))
                else:
                    pages = sorted(list(pages))

                ocr_parts = []
                for page_num in pages:
                    if 1 <= page_num <= total:
                        page = doc[page_num - 1]
                        pix = page.get_pixmap(dpi=150)
                        import io
                        buf = io.BytesIO(pix.tobytes("png"))
                        res = svc.extract_text(buf)
                        if res and res.text and res.text.strip():
                            ocr_parts.append(f"--- Page {page_num} ---\n{res.text.strip()}")
                doc.close()
                return "\n\n".join(ocr_parts)
            except Exception:
                pass
        return ""

    return ""


def extract_html(file_path: str, file_bytes: bytes, pages_spec_str: Optional[str] = None,
                 **kwargs) -> str:
    """Extract HTML output using MarkItDown HTML converter."""
    from ._html_output import convert_to_html
    ext = os.path.splitext(file_path)[1].lower()

    r_kwargs = {k: v for k, v in kwargs.items() if k not in ("enable_ocr", "pages_spec_str", "max_content_size_kb")}
    try:
        md_text = route_document(file_path, file_bytes, ext, enable_ocr=False, pages_spec_str=pages_spec_str, **r_kwargs)
    except Exception:
        md_text = ""

    if not md_text and file_bytes:
        md_text = _extract_text_raw(file_bytes)

    title = os.path.basename(file_path) if file_path else "MarkItDown Output"
    return convert_to_html(md_text, title=title)


def extract_thumbnail(file_path: str, file_bytes: bytes,
                      fmt: str = "png", dpi: int = 150, **kwargs):
    """Extract first-page thumbnail as raw image bytes.

    Returns raw bytes on success, or a dict ``{"error": "..."}`` on failure.
    """
    try:
        if "_pre_pdf" in kwargs and kwargs["_pre_pdf"]:
            try:
                import fitz
                doc = fitz.open(stream=kwargs["_pre_pdf"], filetype="pdf")
                if doc.page_count > 0:
                    page = doc[0]
                    pix = page.get_pixmap(dpi=dpi)
                    img_bytes = pix.tobytes("png" if fmt not in ("png", "jpeg", "jpg", "webp") else fmt)
                    doc.close()
                    return img_bytes
            except Exception:
                pass
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
    "thumbnail": lambda fp, fb, **kw: extract_thumbnail(fp, fb, fmt=kw.get("thumbnail_format", "png"), **kw),
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
    ocr_engine: str = "paddleocr",
    ocr_lang: str = "eng+chi_sim",
    ocr_model_size: Optional[str] = None,
    enable_ocr: bool = False,
    thumbnail_format: str = "png",
    exiftool_path: Optional[str] = None,
    max_content_size_kb: int = 30,
    max_workers: int = 4,
) -> dict:
    """
    Run multiple extractors in parallel and return a combined result dict.
    """
    t_start = time.time()
    benchmarks: dict[str, int] = {}
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

    is_ocr_enabled = enable_ocr or ("ocr" in extract_list)
    kwargs = {
        "pages_spec_str": pages_spec_str,
        "ocr_engine": ocr_engine,
        "ocr_lang": ocr_lang,
        "ocr_model_size": ocr_model_size,
        "enable_ocr": is_ocr_enabled,
        "thumbnail_format": thumbnail_format,
        "max_content_size_kb": max_content_size_kb,
    }
    if exiftool_path:
        kwargs["exiftool_path"] = exiftool_path

    # Determine file group via magika
    file_group = "unknown"
    file_is_text = False
    try:
        m = _get_magika()
        if m is not None:
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
    # text / document are mutually exclusive — only one runs if both are requested.
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".avif"}
    office_exts = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods"}
    
    # Treat office formats as document group regardless of magika output, to prevent skipping document/ocr/thumbnail
    is_office_or_pdf = (file_group == "document") or (ext == ".pdf") or (ext in office_exts)
    
    skip_indicators: set[str] = set()
    if is_office_or_pdf:
        # Document files: run document, skip text if document is also requested
        if "text" in extract_list and "document" in extract_list:
            skip_indicators.add("text")
    elif file_group == "text" or file_group == "code":
        # Text/code files: run text, skip document (document is for PDF/Office only)
        # "code" group covers HTML, XML, CSS, JS, JSON, YAML, etc. — all text-based.
        if "document" in extract_list:
            skip_indicators.add("document")
    else:
        # Other files (audio, video, image, binary): skip both text + document
        for skip in ("text", "document"):
            if skip in extract_list:
                skip_indicators.add(skip)
    # Keep ocr only for document files and images; skip for text and other types
    if not is_office_or_pdf and ext not in image_exts and "ocr" in extract_list:
        skip_indicators.add("ocr")
    # Thumbnail only makes sense for document-type files
    if not is_office_or_pdf and "thumbnail" in extract_list:
        skip_indicators.add("thumbnail")

    # Filter extract_list for logging
    filtered_extract = [i for i in extract_list if i not in skip_indicators]
    results["extract"] = filtered_extract  # report what was actually processed

    # Optimisation: when OCR, thumbnail, or document with page range selection is requested on an Office file,
    # pre-convert to PDF once and reuse for OCR, document (text extraction via PDF), and thumbnail (first-page render).
    pre_pdf: bytes | None = None
    needs_lo = "ocr" in extract_list or "thumbnail" in extract_list or (pages_spec_str and "document" in extract_list)
    if ext in office_exts and needs_lo:
        t0_lo = time.time()
        pre_pdf_error: Optional[str] = None
        try:
            from ._pdf_output import office_to_pdf
            pages_spec = None
            if pages_spec_str:
                from ._page_range import parse_pages, resolve
                spec = parse_pages(pages_spec_str)
                if spec:
                    import fitz
                    doc = fitz.open(stream=file_bytes, filetype=ext.strip(".") if ext != ".pdf" else "pdf")
                    total_pages = doc.page_count
                    resolved = resolve(spec, total_pages)
                    doc.close()
                    if resolved is not None and len(resolved) < total_pages * 0.5:
                        pages_spec = resolved
            pre_pdf = office_to_pdf(file_path, pages_spec=pages_spec)
        except Exception as e:
            # Do NOT swallow silently: if pre-conversion fails, each extractor
            # falls back to converting on its own, and that time is charged to
            # thumbnail/ocr/document instead of office_pre_pdf_ms. Surface the
            # failure so the tiny office_pre_pdf_ms value is explainable.
            pre_pdf_error = f"{type(e).__name__}: {e}"
            import warnings
            warnings.warn(
                f"Office pre-conversion to PDF failed ({pre_pdf_error}); "
                f"extractors will convert independently, so office_pre_pdf_ms "
                f"reflects only the failed attempt.",
                RuntimeWarning,
            )
        benchmarks["office_pre_pdf_ms"] = int((time.time() - t0_lo) * 1000)

    # Helper function for timed execution
    def _timed_worker(fn, fp, fb, kw):
        t0 = time.time()
        res = fn(fp, fb, **kw)
        dt = int((time.time() - t0) * 1000)
        return res, dt

    # Parallel execution
    if pre_pdf is not None:
        kwargs["_pre_pdf"] = pre_pdf
    max_bytes = None if max_content_size_kb <= 0 else max_content_size_kb * 1024
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {}
        for name in filtered_extract:
            # For text files (file_group == "text"), use raw text extraction
            if name == "text" and file_group == "text":
                extract_fn = lambda fp, fb, **kw: _extract_text_raw(fb, max_bytes=max_bytes)
            else:
                extract_fn = EXTRACTORS.get(name)
            if extract_fn:
                future = pool.submit(_timed_worker, extract_fn, file_path, file_bytes, kwargs)
                future_map[future] = name

        # Collect results
        result_data: dict = {}
        magika_data = None
        meta_data = None
        thumb_data = None

        for future in as_completed(future_map):
            name = future_map[future]
            try:
                value, elapsed_ms = future.result()
                benchmarks[f"{name}_ms"] = elapsed_ms
            except Exception as e:
                value = {"error": f"Thread '{name}': {type(e).__name__}: {e}"}
                benchmarks[f"{name}_ms"] = 0

            if name == "magika":
                magika_data = value
            elif name == "metadata":
                meta_data = value
            elif name == "thumbnail":
                thumb_data = value
            elif name in ("text", "document", "ocr", "html"):
                result_data[name] = value

    # Assemble response
    if magika_data is not None:
        results["magika"] = magika_data
    if meta_data is not None:
        results["metadata"] = meta_data
    if thumb_data is not None:
        results["thumbnail"] = thumb_data
    if result_data:
        res = {}
        for name, val in result_data.items():
            if isinstance(val, str):
                text_str = val[:max_bytes] if (max_bytes is not None and len(val) > max_bytes) else val
                res[name] = {"content": text_str, "length": len(text_str)}
            elif isinstance(val, dict):
                content = val.get("content")
                if isinstance(content, str) and max_bytes is not None and len(content) > max_bytes:
                    val["content"] = content[:max_bytes]
                    val["length"] = len(val["content"])
                res[name] = val
            else:
                res[name] = val
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

    if thumb_data and isinstance(thumb_data, bytes):
        results["thumbnail"] = {
            "format": thumbnail_format,
            "dpi": 150,
            "data": base64.b64encode(thumb_data).decode("ascii"),
        }

    total_ms = int((time.time() - t_start) * 1000)
    results["time_ms"] = total_ms
    results["benchmark"] = {
        "total_ms": total_ms,
        **benchmarks,
    }
    return results


def extract_to_json(
    file_path: str,
    file_bytes: bytes,
    extract_list: list[str],
    pages_spec_str: Optional[str] = None,
    ocr_engine: str = "paddleocr",
    ocr_lang: str = "eng+chi_sim",
    ocr_model_size: Optional[str] = None,
    enable_ocr: bool = False,
    thumbnail_format: str = "png",
    exiftool_path: Optional[str] = None,
    output_paths: Optional[dict[str, str]] = None,
    max_content_size_kb: int = 30,
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
        ocr_engine=ocr_engine,
        ocr_lang=ocr_lang,
        ocr_model_size=ocr_model_size,
        enable_ocr=enable_ocr,
        thumbnail_format=thumbnail_format,
        exiftool_path=exiftool_path,
        max_content_size_kb=max_content_size_kb,
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
