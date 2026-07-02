import os
import zipfile
import shutil
import subprocess
import tempfile
import platform


class ThumbnailError(Exception):
    pass


_OFFICE_EXTS = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods"}
_IMG_FORMATS = {"png", "jpeg", "jpg", "webp"}


def _find_libreoffice() -> str | None:
    """Locate LibreOffice using the comprehensive detection module."""
    try:
        from ._libreoffice_detect import findLibreOfficePath
        return findLibreOfficePath()
    except (ImportError, FileNotFoundError):
        pass
    return None


def _normalize_fmt(fmt: str) -> str:
    fmt = fmt.lower().replace(".", "")
    if fmt == "jpg":
        return "jpeg"
    if fmt not in _IMG_FORMATS:
        raise ThumbnailError(
            f"Unsupported image format: {fmt}. Supported: {', '.join(sorted(_IMG_FORMATS))}"
        )
    return fmt


def extract_thumbnails(file_path: str, pages_spec=None, dpi: int = 150, fmt: str = "png") -> dict[int, bytes]:
    fmt = _normalize_fmt(fmt)
    ext = os.path.splitext(file_path)[1].lower()

    # Parse string pages_spec (e.g. "1", "1-3") into a set of ints
    if isinstance(pages_spec, str):
        from ._page_range import parse_pages, resolve
        parsed = parse_pages(pages_spec)
        # resolve needs total page count — we don't have it yet, so keep raw
        # and resolve at the per-format handler
        pages_spec = parsed

    if ext == ".pdf":
        return _pdf_thumbnails(file_path, pages_spec, dpi, fmt)

    if ext in _OFFICE_EXTS:
        # Priority: win32com (Windows + Office) > LibreOffice > embedded
        if ext == ".pptx":
            try:
                return _pptx_via_win32com(file_path, pages_spec, fmt)
            except ThumbnailError:
                pass

        lo = _find_libreoffice()
        if lo:
            return _office_thumbnails_via_lo(file_path, pages_spec, dpi, fmt, lo)

        if ext == ".pptx":
            return _pptx_embedded_thumbnail(file_path)
        if ext == ".docx":
            return _docx_embedded_thumbnail(file_path)
        raise ThumbnailError(
            f"No embedded image found and no renderer available. "
            f"Install LibreOffice (https://libreoffice.org) or Microsoft Office."
        )

    raise ThumbnailError(
        f"Unsupported file format: {ext}. Supported: .pdf, .docx, .pptx, .xlsx, .doc, .ppt, .xls"
    )


def _pdf_thumbnails(file_path: str, pages_spec, dpi: int, fmt: str) -> dict[int, bytes]:
    try:
        import fitz
    except ImportError:
        raise ThumbnailError("PyMuPDF (fitz) is required for PDF thumbnails")

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        raise ThumbnailError(f"Cannot open PDF: {e}")

    if doc.page_count == 0:
        doc.close()
        raise ThumbnailError("PDF has no pages")

    from ._page_range import resolve
    pages = resolve(pages_spec, doc.page_count)
    if pages is None:
        pages = {1}

    result = {}
    try:
        for idx in sorted(pages):
            if 1 <= idx <= doc.page_count:
                page = doc[idx - 1]
                pix = page.get_pixmap(dpi=dpi)
                if fmt == "webp":
                    from PIL import Image
                    import io
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    buf = io.BytesIO()
                    img.save(buf, format="WEBP")
                    result[idx] = buf.getvalue()
                else:
                    result[idx] = pix.tobytes(fmt)
    finally:
        doc.close()

    if not result:
        raise ThumbnailError("No valid pages to render")
    return result


def _office_thumbnails_via_lo(file_path: str, pages_spec, dpi: int, fmt: str, lo: str) -> dict[int, bytes]:
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [lo, "--headless", "--convert-to", "pdf", "--outdir", tmpdir, file_path],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise ThumbnailError(
                f"LibreOffice PDF conversion failed:\n{result.stderr.strip()}"
            )

        base = os.path.splitext(os.path.basename(file_path))[0] + ".pdf"
        pdf_path = os.path.join(tmpdir, base)
        if not os.path.exists(pdf_path):
            raise ThumbnailError("LibreOffice did not produce expected PDF output")

        return _pdf_thumbnails(pdf_path, pages_spec, dpi, fmt)


def _pptx_via_win32com(file_path: str, pages_spec, fmt: str) -> dict[int, bytes]:
    if platform.system() != "Windows":
        raise ThumbnailError("win32com PPTX export is only available on Windows")

    try:
        import win32com.client
    except ImportError:
        raise ThumbnailError("pywin32 (win32com) not available")

    fmt_map = {"png": 2, "jpeg": 3, "jpg": 3, "webp": 3}
    pp_fmt = fmt_map.get(fmt, 2)

    from ._page_range import resolve

    try:
        app = win32com.client.Dispatch("PowerPoint.Application")
        app.Visible = False
        pres = app.Presentations.Open(file_path, WithWindow=False)
    except Exception as e:
        try:
            app.Quit()
        except Exception:
            pass
        raise ThumbnailError(f"PowerPoint COM failed to open file: {e}")

    try:
        total = pres.Slides.Count
        pages = resolve(pages_spec, total)
        if pages is None:
            pages = {1}

        with tempfile.TemporaryDirectory() as tmpdir:
            result = {}
            for idx in sorted(pages):
                if 1 <= idx <= total:
                    slide = pres.Slides(idx)
                    # PowerPoint Export format: 2=PNG, 3=JPEG, 5=WEBP (not always available)
                    # Use PNG then convert via Pillow for format conversion
                    out_path = os.path.join(tmpdir, f"slide_{idx}.png")
                    slide.Export(out_path, "PNG")
                    if fmt == "png":
                        with open(out_path, "rb") as f:
                            result[idx] = f.read()
                    else:
                        from PIL import Image
                        im = Image.open(out_path)
                        save_fmt = "JPEG" if fmt in ("jpeg", "jpg") else "WEBP"
                        import io
                        buf = io.BytesIO()
                        im.save(buf, format=save_fmt)
                        result[idx] = buf.getvalue()
            return result
    finally:
        try:
            pres.Close()
            app.Quit()
        except Exception:
            pass


def _pptx_embedded_thumbnail(file_path: str) -> dict[int, bytes]:
    if not zipfile.is_zipfile(file_path):
        raise ThumbnailError("Cannot open PPTX: not a valid ZIP archive")

    zf = None
    try:
        zf = zipfile.ZipFile(file_path, "r")
    except Exception as e:
        raise ThumbnailError(f"Cannot open PPTX: {e}")

    candidates = ["docProps/thumbnail.jpeg", "docProps/thumbnail.jpg"]
    try:
        for name in candidates:
            try:
                data = zf.read(name)
                return {1: data}
            except KeyError:
                continue
    finally:
        zf.close()

    raise ThumbnailError(
        "No embedded thumbnail found in PPTX. "
        "PowerPoint only saves a thumbnail when 'Save thumbnail' is enabled in Save options. "
        "Install LibreOffice or Microsoft Office for full document rendering."
    )


def _docx_embedded_thumbnail(file_path: str) -> dict[int, bytes]:
    if not zipfile.is_zipfile(file_path):
        raise ThumbnailError("Cannot open DOCX: not a valid ZIP archive")

    zf = None
    try:
        zf = zipfile.ZipFile(file_path, "r")
    except Exception as e:
        raise ThumbnailError(f"Cannot open DOCX: {e}")

    media_files = sorted(
        [n for n in zf.namelist() if n.startswith("word/media/")],
    )
    if not media_files:
        zf.close()
        raise ThumbnailError(
            "No embedded images found in DOCX. "
            "Install LibreOffice for full document rendering."
        )

    try:
        data = zf.read(media_files[0])
        return {1: data}
    finally:
        zf.close()
