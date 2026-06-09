import os
import shutil
import subprocess
import tempfile
from typing import Set


class PdfConversionError(Exception):
    pass


_SUPPORTED_EXTENSIONS = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods"}
_LIBREOFFICE_NAMES = ["libreoffice", "soffice"]


def _find_libreoffice() -> str:
    for name in _LIBREOFFICE_NAMES:
        path = shutil.which(name)
        if path:
            return path
    raise PdfConversionError(
        "LibreOffice not found. Install from https://libreoffice.org "
        "or ensure 'libreoffice' is on your PATH."
    )


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


def _convert_to_pdf(file_path: str, outdir: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    # Priority: docx2pdf (Word COM) > PPT win32com > LO
    if ext == ".docx":
        result = _try_docx2pdf(file_path, outdir)
        if result:
            return result
    elif ext in (".pptx", ".ppt"):
        result = _try_win32com_pptx(file_path, outdir)
        if result:
            return result

    lo = _find_libreoffice()
    result = subprocess.run(
        [lo, "--headless", "--convert-to", "pdf", "--outdir", outdir, file_path],
        capture_output=True, text=True, timeout=300,
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
        pdf_path = _convert_to_pdf(file_path, tmpdir)

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
    result = subprocess.run(
        [lo, "--headless", "--convert-to", f'png:"PNG":--dpi {dpi}', "--outdir", output_dir, file_path],
        capture_output=True, text=True, timeout=300,
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
