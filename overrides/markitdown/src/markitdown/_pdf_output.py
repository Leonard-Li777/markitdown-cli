import os
import platform
import subprocess
import sys
import tempfile
from typing import Set


class PdfConversionError(Exception):
    pass


_SUPPORTED_EXTENSIONS = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods"}


def _run_libreoffice(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """
    Run LibreOffice with the given arguments, suppressing console windows
    and stdin prompts on Windows (avoids the 'Press Enter to continue...' dialog).
    """
    kwargs: dict = {
        "args": args,
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "stdin": subprocess.DEVNULL,  # Prevent blocking on "Press Enter to continue..."
    }
    if platform.system() == "Windows":
        # Hide the console window that LibreOffice spawns
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
    return subprocess.run(**kwargs)


def _find_libreoffice() -> str:
    """Locate the LibreOffice executable using the comprehensive detection module."""
    from ._libreoffice_detect import findLibreOfficePath
    try:
        return findLibreOfficePath()
    except FileNotFoundError as exc:
        raise PdfConversionError(str(exc))


def _try_docx2pdf(file_path: str, outdir: str) -> str | None:
    try:
        import docx2pdf
    except ImportError:
        return None
    try:
        docx2pdf.convert(file_path, outdir)
        expected = os.path.join(outdir, os.path.splitext(os.path.basename(file_path))[0] + ".pdf")
        if os.path.exists(expected):
            return expected
    except Exception:
        pass
    return None


def _try_win32com_pptx(file_path: str, outdir: str) -> str | None:
    try:
        import win32com.client
        import platform
        if platform.system() != "Windows":
            return None
        app = win32com.client.Dispatch("PowerPoint.Application")
        app.Visible = False
        pres = app.Presentations.Open(file_path, WithWindow=False)
        pdf_path = os.path.join(outdir, os.path.splitext(os.path.basename(file_path))[0] + ".pdf")
        pres.ExportAsFixedFormat(pdf_path, 2)  # 2 = ppFixedFormatTypePDF
        pres.Close()
        app.Quit()
        return pdf_path if os.path.exists(pdf_path) else None
    except Exception:
        return None


def _convert_to_pdf(file_path: str, outdir: str, pages_spec: set[int] | None = None) -> str:
    """
    Convert an Office file to PDF.

    For PPTX/PPT with page selection, tries UNO first (single-page render).
    If UNO fails, PPTX returns empty (too slow via CLI); other formats fall
    back to the standard LibreOffice CLI path.
    """
    ext = os.path.splitext(file_path)[1].lower()

    # Try docx2pdf / win32com shortcuts first (existing behaviour)
    if ext == ".docx":
        result = _try_docx2pdf(file_path, outdir)
        if result:
            return result
    elif ext in (".pptx", ".ppt"):
        result = _try_win32com_pptx(file_path, outdir)
        if result:
            return result

    # ---- UNO path (page-selective, fast for single pages) ----
    # Only worth attempting when we only need a subset of pages
    if pages_spec is not None:
        pdf_bytes = _convert_to_pdf_uno(file_path, pages_spec)
        if pdf_bytes is not None:
            # Write the returned bytes to outdir
            out_name = os.path.splitext(os.path.basename(file_path))[0] + ".pdf"
            out_path = os.path.join(outdir, out_name)
            with open(out_path, "wb") as f:
                f.write(pdf_bytes)
            return out_path

        # UNO failed → fall through to CLI path (for all formats, including PPTX)
        # The CLI does a full conversion; page filtering happens in office_to_pdf

    # ---- CLI fallback (LibreOffice --convert-to pdf) ----
    lo = _find_libreoffice()
    result = _run_libreoffice(
        [lo, "--headless", "--convert-to", "pdf", "--outdir", outdir, file_path],
    )
    if result.returncode != 0:
        raise PdfConversionError(
            f"LibreOffice conversion failed:\n{result.stderr.strip()}"
        )
    expected = os.path.join(outdir, os.path.splitext(os.path.basename(file_path))[0] + ".pdf")
    if not os.path.exists(expected):
        raise PdfConversionError("LibreOffice did not produce the expected PDF output")
    return expected


def office_to_pdf(file_path: str, pages_spec=None) -> bytes:
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        raise PdfConversionError(
            f"Unsupported format: {ext}. Supported: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = _convert_to_pdf(file_path, tmpdir, pages_spec=pages_spec)

        if pages_spec is not None:
            from ._page_range import resolve
            import fitz
            doc = fitz.open(pdf_path)
            pages = resolve(pages_spec, doc.page_count)
            if pages is None:
                with open(pdf_path, "rb") as f:
                    return f.read()
            selected = fitz.open()
            try:
                for idx in sorted(pages):
                    if 1 <= idx <= doc.page_count:
                        selected.insert_pdf(doc, from_page=idx - 1, to_page=idx - 1)
                pdf_bytes = selected.tobytes()
            finally:
                doc.close()
                selected.close()
            return pdf_bytes
        else:
            with open(pdf_path, "rb") as f:
                return f.read()


def office_to_images(file_path: str, output_dir: str, dpi: int = 150) -> list[str]:
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        raise PdfConversionError(
            f"Unsupported format: {ext}. Supported: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}"
        )

    lo = _find_libreoffice()
    os.makedirs(output_dir, exist_ok=True)
    result = _run_libreoffice(
        [lo, "--headless", "--convert-to", f'png:"PNG":--dpi {dpi}', "--outdir", output_dir, file_path],
    )
    if result.returncode != 0:
        raise PdfConversionError(
            f"LibreOffice image conversion failed:\n{result.stderr.strip()}"
        )

    base = os.path.splitext(os.path.basename(file_path))[0]
    images = sorted(
        os.path.join(output_dir, f) for f in os.listdir(output_dir)
        if f.startswith(base) and f.lower().endswith(".png")
    )
    return images


# --------------------------------------------------------------------------
# UNO listener management — page-selective PDF export
# --------------------------------------------------------------------------
_UNO_LISTENER_PORT = 2083
_UNO_LISTENER_PROC = None
_UNO_CLEANUP_REGISTERED = False


def _uno_python() -> str | None:
    """Return the path to LO's built-in Python (with ``uno`` module)."""
    from ._libreoffice_detect import findLibreOfficePython
    return findLibreOfficePython()


def _uno_cleanup():
    """Ensure the UNO listener is killed on process exit."""
    global _UNO_LISTENER_PROC
    if _UNO_LISTENER_PROC is not None:
        try:
            _UNO_LISTENER_PROC.kill()
            _UNO_LISTENER_PROC.wait(timeout=5)
        except Exception:
            pass
        _UNO_LISTENER_PROC = None


def _try_connect_uno(port: int, timeout: float = 4.0) -> bool:
    """Try to connect to a running UNO listener on the given port."""
    lo_py = _uno_python()
    if lo_py is None:
        return False
    # Locate probe_uno.py — next to executable or in scripts/
    probe = None
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else None
    if exe_dir:
        probe = os.path.join(exe_dir, "probe_uno.py")
    if not probe or not os.path.isfile(probe):
        probe = os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts", "probe_uno.py")
    if not os.path.isfile(probe):
        return False
    try:
        r = subprocess.run([lo_py, probe, str(port)], capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0 and "ok" in r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def _start_uno_listener() -> bool:
    """Start the LibreOffice UNO listener as a background process.

    Ensures cleanup via ``atexit`` so that no orphan ``soffice.bin``
    processes are left behind.
    """
    global _UNO_LISTENER_PROC, _UNO_CLEANUP_REGISTERED

    # Already running?
    if _UNO_LISTENER_PROC is not None and _UNO_LISTENER_PROC.poll() is None:
        return True

    # Already a listener on this port (e.g. from a previous run)?
    if _try_connect_uno(_UNO_LISTENER_PORT, timeout=1.0):
        return True

    lo = _find_libreoffice()
    try:
        _UNO_LISTENER_PROC = subprocess.Popen(
            [lo, "--headless", f"--accept=socket,host=127.0.0.1,port={_UNO_LISTENER_PORT};urp;"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )

        # Register cleanup exactly once
        if not _UNO_CLEANUP_REGISTERED:
            import atexit
            atexit.register(_uno_cleanup)
            _UNO_CLEANUP_REGISTERED = True

        import time
        time.sleep(2)  # give it time to initialise
        return _UNO_LISTENER_PROC.poll() is None
    except OSError:
        return False


def _stop_uno_listener():
    """Stop the UNO listener if running."""
    _uno_cleanup()


def convert_via_uno(file_path: str, pages_spec: set[int] | None = None) -> bytes | None:
    """
    Render selected pages from an Office file to PDF via UNO.
    Only a single page is rendered at a time; if *pages_spec* contains
    multiple pages they are merged in memory afterwards.

    Returns ``None`` if UNO is not available or fails.
    """
    lo_py = _uno_python()
    if lo_py is None:
        return None

    if not _start_uno_listener():
        return None

    # Locate render_page.py — next to the executable or in scripts/ next to the repo
    render_script = None
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else None
    candidates = []
    if exe_dir:
        candidates.append(os.path.join(exe_dir, "render_page.py"))
    # In development mode, look relative to this file
    candidates.append(os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts", "render_page.py"))
    candidates.append(os.path.join(os.path.dirname(__file__), "scripts", "render_page.py"))
    for c in candidates:
        if os.path.isfile(c):
            render_script = c
            break
    if render_script is None:
        return None

    pages = sorted(pages_spec) if pages_spec else [1]
    merged = None

    for page_num in pages:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            out_path = tmp.name

        try:
            result = subprocess.run(
                [lo_py, render_script, file_path, str(page_num), out_path,
                 "--port", str(_UNO_LISTENER_PORT)],
                capture_output=True, text=True, timeout=120,
            )
            import json
            data = json.loads(result.stdout)
            if data.get("status") != "ok":
                return None

            with open(out_path, "rb") as f:
                page_bytes = f.read()

            if merged is None:
                merged = page_bytes
            else:
                # Merge subsequent pages into the accumulated PDF
                import fitz
                merged_doc = fitz.open(stream=merged, filetype="pdf")
                page_doc = fitz.open(stream=page_bytes, filetype="pdf")
                merged_doc.insert_pdf(page_doc)
                merged = merged_doc.tobytes()
                merged_doc.close()
                page_doc.close()
        except Exception:
            return None
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    return merged


def _convert_to_pdf_uno(file_path: str, pages_spec: set[int] | None = None) -> bytes | None:
    """Convert an Office file to PDF using UNO (only first page if pages_spec is set)."""
    # For UNO path, only render page 1 if partial pages are requested
    uno_pages = {1} if pages_spec else None
    return convert_via_uno(file_path, pages_spec=uno_pages)
