"""
HTTP API server for MarkItDown — provides REST endpoints for document extraction.

Endpoints:
  GET  /health        — health check + component status
  POST /extract       — extract indicators from uploaded file
  GET  /extract/:id   — poll result (async)
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional

from ._extractor import extract_to_json


# ---------------------------------------------------------------------------
# In-memory async result store
# ---------------------------------------------------------------------------

_ASYNC_RESULTS: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    """HTTP request handler for the MarkItDown API."""

    # Silence default logging per-request (we log manually)
    def log_message(self, format, *args):
        if len(args) >= 2 and args[0] in ("200", "202", "400", "404", "500"):
            pass  # suppress
        else:
            super().log_message(format, *args)

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: int, message: str):
        self._send_json({"status": "error", "error": {"code": code, "message": message}}, code)

    def do_GET(self):
        if self.path == "/health":
            self._handle_health()
            return
        m = re.match(r"^/extract/([a-f0-9\-]+)$", self.path)
        if m:
            self._handle_poll(m.group(1))
            return
        self._send_error(404, "Not found. Available: GET /health, POST /extract")

    def do_POST(self):
        if self.path == "/extract":
            self._handle_extract()
            return
        self._send_error(404, "Not found")

    # ---- Health ---------------------------------------------------------

    def _handle_health(self):
        from ._libreoffice_detect import detectLibreOffice, getLibreOfficeVersion
        lo_result = detectLibreOffice()
        lo_info = {"detected": lo_result.installed}
        if lo_result.installed:
            lo_info["version"] = getLibreOfficeVersion()
            lo_info["mode"] = "cli"

        tess_info = {"detected": False}
        try:
            from markitdown_ocr._tesseract_service import TesseractOCRService
            svc = TesseractOCRService()
            tess_info["detected"] = svc.available
        except ImportError:
            pass

        from .__about__ import __version__ as _ver
        self._send_json({
            "status": "ok",
            "version": _ver,
            "uptime_sec": int(getattr(self.server, "_uptime", 0)),
            "libreoffice": lo_info,
            "tesseract": tess_info,
            "magika": {"detected": True},
        })

    # ---- Extract --------------------------------------------------------

    def _handle_extract(self):
        content_type = self.headers.get("Content-Type", "")

        file_data = None
        file_name = "upload"
        extract_str = ""
        pages = None
        ocr_lang = "eng+chi_sim"
        thumb_fmt = "png"

        if "application/json" in content_type:
            # JSON mode: receives file_path instead of file upload
            content_length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_error(400, "Invalid JSON body")
                return

            file_path = body.get("file_path", "")
            extract_str = body.get("extract", "")
            pages = body.get("pages")
            ocr_engine = body.get("ocr_engine", "paddleocr")
            ocr_lang = body.get("ocr_lang", "eng+chi_sim")
            ocr_model_size = body.get("ocr_model_size") or body.get("ocr_size")
            thumb_fmt = body.get("thumbnail_format")
            # Auto-detect format from thumbnail_out extension if not explicitly set
            thumbnail_out = body.get("thumbnail_out")
            if not thumb_fmt and thumbnail_out:
                ext_map = {".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".webp": "webp", ".bmp": "bmp", ".tiff": "tiff", ".tif": "tiff", ".gif": "gif"}
                thumb_fmt = ext_map.get(os.path.splitext(thumbnail_out)[1].lower(), "png")
            if not thumb_fmt:
                thumb_fmt = "png"
            text_out = body.get("text_out")
            document_out = body.get("document_out")
            ocr_out = body.get("ocr_out")
            html_out = body.get("html_out")
            metadata_out = body.get("metadata_out")
            magika_out = body.get("magika_out")

            enable_ocr = body.get("enable_ocr") or body.get("use_ocr")
            if isinstance(enable_ocr, str):
                enable_ocr = enable_ocr.lower() in ("true", "1", "yes")

            if isinstance(extract_str, list):
                extract_str = ",".join(extract_str)

            if not file_path or not os.path.isfile(file_path):
                self._send_error(400, f"File not found: {file_path}")
                return

            file_name = os.path.basename(file_path)
            with open(file_path, "rb") as f:
                file_bytes = f.read()

        elif "multipart/form-data" in content_type:
            # Multipart mode: file upload
            boundary = None
            for part in content_type.split(";"):
                part = part.strip()
                if part.startswith("boundary="):
                    boundary = part[len("boundary="):].strip('"')
                    break
            if not boundary:
                self._send_error(400, "No boundary in Content-Type")
                return

            content_length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_length)

            fields = self._parse_multipart(raw, boundary)
            file_data = fields.get("file", {}).get("data")
            file_name = fields.get("file", {}).get("name", "upload")
            extract_str = fields.get("extract", "")
            pages = fields.get("pages")
            ocr_engine = fields.get("ocr_engine", "paddleocr")
            ocr_lang = fields.get("ocr_lang", "eng+chi_sim")
            ocr_model_size = fields.get("ocr_model_size") or fields.get("ocr_size")
            enable_ocr = fields.get("enable_ocr") or fields.get("use_ocr")
            if isinstance(enable_ocr, str):
                enable_ocr = enable_ocr.lower() in ("true", "1", "yes")
            thumb_fmt = fields.get("thumbnail_format", "png")
            thumbnail_out = fields.get("thumbnail_out")
            text_out = fields.get("text_out")
            document_out = fields.get("document_out")
            ocr_out = fields.get("ocr_out")
            html_out = fields.get("html_out")
            metadata_out = fields.get("metadata_out")
            magika_out = fields.get("magika_out")

            if not file_data:
                self._send_error(400, "No file uploaded")
                return
            file_bytes = file_data

        else:
            self._send_error(400, "Expected application/json or multipart/form-data")
            return

        if not extract_str:
            self._send_error(400, "Missing 'extract' field")
            return

        extract_list = [s.strip() for s in extract_str.split(",") if s.strip()]
        # Validate
        valid = {"text", "document", "ocr", "html", "metadata", "magika", "thumbnail"}
        for e in extract_list:
            if e not in valid:
                self._send_error(400, f"Unknown extract indicator: {e}")
                return

        # Enable OCR if explicitly requested via enable_ocr/use_ocr OR if 'ocr' is in extract_list
        if enable_ocr is None:
            enable_ocr = "ocr" in extract_list
        else:
            enable_ocr = bool(enable_ocr) or ("ocr" in extract_list)

        # Save to temp file for processors that need a path
        ext = os.path.splitext(file_name)[1] or ".bin"
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            # Auto-detect exiftool path
            exiftool_path = None
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

            if exiftool_path is None:
                env_path = os.environ.get("EXIFTOOL_PATH")
                if env_path and os.path.isfile(env_path):
                    exiftool_path = env_path
            if exiftool_path is None:
                found = shutil.which("exiftool")
                if found:
                    exiftool_path = os.path.abspath(found)

            output_paths = {}
            for key, val in [("text", text_out), ("document", document_out),
                             ("ocr", ocr_out), ("html", html_out),
                             ("metadata", metadata_out), ("magika", magika_out),
                             ("thumbnail", thumbnail_out)]:
                if val:
                    output_paths[key] = val
            result = extract_to_json(
                file_path=tmp_path,
                file_bytes=file_bytes,
                extract_list=extract_list,
                pages_spec_str=pages,
                ocr_engine=ocr_engine,
                ocr_lang=ocr_lang,
                ocr_model_size=ocr_model_size,
                enable_ocr=enable_ocr,
                thumbnail_format=thumb_fmt,
                exiftool_path=exiftool_path,
                output_paths=output_paths if output_paths else None,
            )
            result["file"]["name"] = file_name
            self._send_json(result)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ---- Async poll -----------------------------------------------------

    def _handle_poll(self, file_id: str):
        result = _ASYNC_RESULTS.get(file_id)
        if result is None:
            self._send_error(404, f"Unknown file_id: {file_id}")
            return
        self._send_json(result)

    # ---- Multipart parser (minimal, no external deps) -------------------

    @staticmethod
    def _parse_multipart(raw: bytes, boundary: str) -> dict:
        """Parse multipart/form-data into {field_name: {name?, data}}."""
        bboundary = f"--{boundary}".encode("utf-8")
        parts = raw.split(bboundary)
        fields: dict = {}

        for part in parts:
            if not part.strip() or part.strip() == b"--":
                continue
            # Split headers from body
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            headers_raw = part[:header_end].decode("utf-8", errors="replace")
            body = part[header_end + 4:]
            # Remove trailing \r\n--
            if body.endswith(b"\r\n"):
                body = body[:-2]
            if body.endswith(b"\r\n--"):
                body = body[:-4]

            # Extract name from Content-Disposition
            name = None
            filename = None
            for line in headers_raw.split("\r\n"):
                if line.lower().startswith("content-disposition:"):
                    m = re.search(r'name="([^"]*)"', line)
                    if m:
                        name = m.group(1)
                    m = re.search(r'filename="([^"]*)"', line)
                    if m:
                        filename = m.group(1)

            if name:
                if filename:
                    fields[name] = {"name": filename, "data": body}
                else:
                    fields[name] = body.decode("utf-8", errors="replace").strip()

        return fields


# ---------------------------------------------------------------------------
# Threaded HTTP server
# ---------------------------------------------------------------------------

class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def find_free_port(start: int = 5052) -> int:
    """Find the first free port starting from *start*."""
    import socket
    port = start
    while port < start + 100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    raise RuntimeError(f"No free port found in range {start}-{start+99}")


def run_server(host: str = "127.0.0.1", port: int = 5052,
               port_file: Optional[str] = None):
    """
    Start the MarkItDown API server.

    If *port* == 0, let the OS assign a free port (printed to stdout).
    Otherwise, if *port* is taken, auto-increment until a free port is found.
    """
    if port == 0:
        port = find_free_port(5052)
    else:
        actual = find_free_port(port)
        if actual != port:
            port = actual

    server = _ThreadedHTTPServer((host, port), _Handler)
    server._uptime = 0

    # Print port for consumers (first line = PORT=xxxxx)
    # flush=True: in non-TTY environments stdout is block-buffered, so without
    # an explicit flush the PORT= line may never reach the consumer before
    # serve_forever() blocks indefinitely.
    from .__about__ import __version__
    print(f"PORT={port}", flush=True)
    print(f"[{__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S}] "
          f"Server started on http://{host}:{port} (v{__version__})", flush=True)

    if port_file:
        os.makedirs(os.path.dirname(os.path.abspath(port_file)), exist_ok=True)
        with open(port_file, "w") as f:
            f.write(str(port))

    import threading

    def _watch_parent():
        """当父进程退出或管道 EOF 时自动自我终止，防止孤儿进程残留"""
        try:
            sys.stdin.read()
        except Exception:
            pass
        os._exit(0)

    t = threading.Thread(target=_watch_parent, daemon=True)
    t.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()
