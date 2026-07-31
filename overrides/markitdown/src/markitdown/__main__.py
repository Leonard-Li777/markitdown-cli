import shutil
import argparse
import os
import sys
import codecs
from typing import Any, Dict
from textwrap import dedent
from importlib.metadata import entry_points
from .__about__ import __version__
from ._markitdown import MarkItDown, StreamInfo, DocumentConverterResult


def _thumbnail_command():
    parser = argparse.ArgumentParser(
        description="Extract a thumbnail/preview image from PDF or Office files.",
        prog="markitdown thumbnail",
    )
    parser.add_argument("file", help="Path to the input file")
    parser.add_argument("-o", "--output", required=True, help="Output image path (e.g., preview.png)")
    parser.add_argument(
        "--pages",
        type=str,
        help="Page selection. Examples: '1' (single), '1,3,5' (multiple), '1-5' (range). "
        "For multiple pages, output files are named like 'output_1.png', 'output_3.png'.",
    )
    parser.add_argument("--dpi", type=int, default=150, help="DPI for PDF rendering (default: 150)")
    parser.add_argument(
        "--format", type=str, choices=["png", "jpeg", "webp"],
        help="Output image format. Defaults to auto-detect from output file extension.",
    )
    args = parser.parse_args(sys.argv[2:])

    try:
        from ._page_range import parse_pages
        from ._thumbnail import extract_thumbnails, ThumbnailError, _normalize_fmt

        fmt = args.format
        if not fmt:
            _, ext = os.path.splitext(args.output)
            fmt = ext.lstrip(".") or "png"
        fmt = _normalize_fmt(fmt)

        pages_spec = parse_pages(args.pages) if args.pages else None
        thumbnails = extract_thumbnails(args.file, pages_spec=pages_spec, dpi=args.dpi, fmt=fmt)

        ext = "." + fmt
        if fmt == "jpeg":
            ext = ".jpg"

        if len(thumbnails) == 1:
            page_num, data = next(iter(thumbnails.items()))
            with open(args.output, "wb") as f:
                f.write(data)
        else:
            base = os.path.splitext(args.output)[0]
            for page_num in sorted(thumbnails):
                path = f"{base}_{page_num}{ext}"
                with open(path, "wb") as f:
                    f.write(thumbnails[page_num])
                print(f"  {path}", file=sys.stderr)

    except ThumbnailError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _html_command():
    parser = argparse.ArgumentParser(
        description="Convert a document to HTML using MarkItDown.",
        prog="markitdown html",
    )
    parser.add_argument("file", nargs="?", help="Path to the input file (reads from stdin if omitted)")
    parser.add_argument("-o", "--output", help="Output HTML file path (default: stdout)")
    parser.add_argument(
        "--pages",
        type=str,
        help="Page selection. Examples: '1' (single), '1,3,5' (multiple), '1-5' (range), "
        "'-5' (pages 1-5), '5-' (pages 5 to end).",
    )
    parser.add_argument(
        "--dpi", type=int, default=150, help="DPI for PDF rendering when converting Office files (default: 150)"
    )
    args = parser.parse_args(sys.argv[2:])

    try:
        from ._page_range import parse_pages, resolve

        pages_spec = parse_pages(args.pages) if args.pages else None
        convert_file = args.file

        if pages_spec and args.file:
            ext = os.path.splitext(args.file)[1].lower()
            is_office = ext in {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods"}
            if is_office:
                from ._pdf_output import _convert_to_pdf, PdfConversionError
                import fitz
                import tempfile
                tmpdir = tempfile.mkdtemp()
                try:
                    pdf_path = _convert_to_pdf(args.file, tmpdir)
                    doc = fitz.open(pdf_path)
                    pages = resolve(pages_spec, doc.page_count) or set(range(1, doc.page_count + 1))
                    selected = fitz.open()
                    for idx in sorted(pages):
                        if 1 <= idx <= doc.page_count:
                            selected.insert_pdf(doc, from_page=idx - 1, to_page=idx - 1)
                    tmp_pdf = os.path.join(tmpdir, "_filtered.pdf")
                    selected.save(tmp_pdf)
                    selected.close()
                    doc.close()
                    convert_file = tmp_pdf
                except PdfConversionError:
                    pass

        md_kwargs: Dict[str, Any] = {}
        if pages_spec and convert_file == args.file:
            md_kwargs["pages"] = pages_spec

        markitdown = MarkItDown(**md_kwargs)

        if args.file is None:
            result = markitdown.convert_stream(sys.stdin.buffer)
        else:
            result = markitdown.convert(convert_file)

        from ._html_output import convert_to_html
        html = convert_to_html(result.markdown, title=result.title or "MarkItDown Output")

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(html)
        else:
            print(html)

    except Exception as e:
        # Avoid obscure tracebacks for common errors
        msg = str(e)
        if not msg:
            msg = type(e).__name__
        print(f"ERROR: {msg}", file=sys.stderr)
        sys.exit(1)


def _pdf_command():
    parser = argparse.ArgumentParser(
        description="Convert an Office document to PDF using LibreOffice.",
        prog="markitdown pdf",
    )
    parser.add_argument("file", help="Path to the Office file (DOCX, PPTX, XLSX, etc.)")
    parser.add_argument("-o", "--output", required=True, help="Output PDF path")
    parser.add_argument(
        "--pages",
        type=str,
        help="Page selection. Examples: '1' (single), '1,3,5' (multiple), '1-5' (range), "
        "'-5' (pages 1-5), '5-' (pages 5 to end).",
    )
    args = parser.parse_args(sys.argv[2:])

    try:
        from ._page_range import parse_pages, resolve
        from ._pdf_output import office_to_pdf, PdfConversionError

        pages_spec = parse_pages(args.pages) if args.pages else None

        pdf_bytes = office_to_pdf(args.file, pages_spec=pages_spec)
        with open(args.output, "wb") as f:
            f.write(pdf_bytes)

    except PdfConversionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _server_command():
    """Start the MarkItDown HTTP API server (--extract equivalent over HTTP)."""
    parser = argparse.ArgumentParser(
        description="Start MarkItDown HTTP API server for document extraction.",
        prog="markitdown server",
    )
    parser.add_argument("--port", type=int, default=5052,
                        help="Port to listen on (default 5052; auto-increments if taken). Use 0 for OS-assigned.")
    parser.add_argument("--host", type=str, default="127.0.0.1",
                        help="Host to bind to (default 127.0.0.1)")
    parser.add_argument("--port-file", type=str, default=None,
                        help="Write the actual port number to this file")
    args = parser.parse_args(sys.argv[2:])

    from ._server import run_server
    run_server(host=args.host, port=args.port, port_file=args.port_file)


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("version", "--version", "-v"):
        print(f"markitdown {__version__}")
        return
    if len(sys.argv) > 1 and sys.argv[1] == "thumbnail":
        _thumbnail_command()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "html":
        _html_command()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "pdf":
        _pdf_command()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        _server_command()
        return

    parser = argparse.ArgumentParser(
        description="Convert various file formats to markdown.",
        prog="markitdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage=dedent(
            """
            SYNTAX:

                markitdown <OPTIONAL: FILENAME>
                If FILENAME is empty, markitdown reads from stdin.

            EXAMPLE:

                markitdown example.pdf

                OR

                cat example.pdf | markitdown

                OR

                markitdown < example.pdf

                OR to save to a file use

                markitdown example.pdf -o example.md

                OR

                markitdown example.pdf > example.md
            """
        ).strip(),
    )

    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="show the version number and exit",
    )

    parser.add_argument(
        "-o",
        "--output",
        help="Output file name. If not provided, output is written to stdout.",
    )

    parser.add_argument(
        "-x",
        "--extension",
        help="Provide a hint about the file extension (e.g., when reading from stdin).",
    )

    parser.add_argument(
        "-m",
        "--mime-type",
        help="Provide a hint about the file's MIME type.",
    )

    parser.add_argument(
        "-c",
        "--charset",
        help="Provide a hint about the file's charset (e.g, UTF-8).",
    )

    cloud_group = parser.add_mutually_exclusive_group()
    cloud_group.add_argument(
        "-d",
        "--use-docintel",
        action="store_true",
        help="Use Document Intelligence to extract text instead of offline conversion. Requires a valid Document Intelligence Endpoint.",
    )

    cloud_group.add_argument(
        "--use-cu",
        "--use-content-understanding",
        action="store_true",
        dest="use_cu",
        help="Use Azure Content Understanding to extract text. Requires --cu-endpoint.",
    )

    parser.add_argument(
        "-e",
        "--endpoint",
        type=str,
        help="Document Intelligence Endpoint. Required if using Document Intelligence.",
    )

    parser.add_argument(
        "--cu-endpoint",
        type=str,
        help="Content Understanding Endpoint. Required if using --use-cu.",
    )

    parser.add_argument(
        "--cu-analyzer",
        type=str,
        help="Content Understanding analyzer ID. If not specified, auto-selects by file type.",
    )

    parser.add_argument(
        "--cu-file-types",
        type=str,
        help="Comma-separated list of file types to route to Content Understanding (e.g., pdf,jpeg,mp4). If omitted, all supported types are routed.",
    )

    parser.add_argument(
        "-p",
        "--use-plugins",
        action="store_true",
        help="Use 3rd-party plugins to convert files. Use --list-plugins to see installed plugins.",
    )

    parser.add_argument(
        "--list-plugins",
        action="store_true",
        help="List installed 3rd-party plugins. Plugins are loaded when using the -p or --use-plugin option.",
    )

    parser.add_argument(
        "--keep-data-uris",
        action="store_true",
        help="Keep data URIs (like base64-encoded images) in the output. By default, data URIs are truncated.",
    )

    ocr_group = parser.add_argument_group("OCR options")
    ocr_group.add_argument(
        "--use-ocr",
        action="store_true",
        help="Enable OCR for images in PDF, DOCX, PPTX, and XLSX files. Uses Tesseract by default, or LLM Vision if --llm-model is specified.",
    )

    ocr_group.add_argument(
        "--ocr-engine",
        type=str,
        choices=["paddleocr", "tesseract", "llm"],
        default="paddleocr",
        help="OCR engine to use: 'paddleocr' (default, ONNX PP-OCR), 'tesseract', or 'llm'.",
    )

    ocr_group.add_argument(
        "--ocr-model-size",
        "--ocr-size",
        type=str,
        choices=["tiny", "small", "medium"],
        default=None,
        help="ONNX PP-OCR model size: 'tiny', 'small', or 'medium'. If omitted, searches from small to large (tiny -> small -> medium).",
    )

    ocr_group.add_argument(
        "--tesseract-path",
        type=str,
        help="Path to the Tesseract executable. If not specified, searches common locations.",
    )

    ocr_group.add_argument(
        "--tesseract-lang",
        type=str,
        default="eng",
        help="Tesseract OCR language(s), e.g. 'eng', 'chi_sim', or 'eng+chi_sim'. Default: 'eng'.",
    )

    ocr_group.add_argument(
        "--llm-model",
        type=str,
        help="LLM model for OCR (e.g., 'gpt-4o', 'gemini-2.0-flash'). Required when --ocr-engine=llm.",
    )

    parser.add_argument(
        "--pages",
        type=str,
        help="Page selection for PDF files. Supports individual pages, ranges, and combinations. "
        "Examples: '1' (single page), '1,3,5' (multiple), '1-5' (range), "
        "'1,3,5-7,10-12' (mixed), '-5' (pages 1-5), '5-' (pages 5 to end).",
    )

    parser.add_argument(
        "--libreoffice-path",
        type=str,
        help="Path to the LibreOffice executable (soffice). If provided, "
        "skips auto-detection and uses this path directly.",
    )

    # Multi-indicator extraction
    extract_group = parser.add_argument_group("Multi-indicator extraction")
    extract_group.add_argument(
        "--extract",
        type=str,
        help="Comma-separated indicators to extract: text,document,ocr,html,metadata,magika,thumbnail. "
        "When multiple indicators are specified, output is JSON.",
    )
    for _ind in ("text", "document", "ocr", "html", "metadata", "magika", "thumbnail"):
        extract_group.add_argument(
            f"--{_ind}-out",
            type=str,
            metavar="FILE",
            dest=f"{_ind}_out",
            help=f"Path to save {_ind} output (when omitted, returned inline in JSON).",
        )

    parser.add_argument(
        "--with-metadata",
        action="store_true",
        help="Include file metadata (ExifTool fields, EPUB info, email headers) in the output. "
        "By default, metadata is suppressed for cleaner markdown output.",
    )

    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Output only the file metadata (title + structured fields), without the content body. "
        "Mutually exclusive with --with-metadata.",
    )

    parser.add_argument("filename", nargs="?")
    args = parser.parse_args()

    # Parse the extension hint
    extension_hint = args.extension
    if extension_hint is not None:
        extension_hint = extension_hint.strip().lower()
        if len(extension_hint) > 0:
            if not extension_hint.startswith("."):
                extension_hint = "." + extension_hint
        else:
            extension_hint = None

    # Parse the mime type
    mime_type_hint = args.mime_type
    if mime_type_hint is not None:
        mime_type_hint = mime_type_hint.strip()
        if len(mime_type_hint) > 0:
            if mime_type_hint.count("/") != 1:
                _exit_with_error(f"Invalid MIME type: {mime_type_hint}")
        else:
            mime_type_hint = None

    # Parse the charset
    charset_hint = args.charset
    if charset_hint is not None:
        charset_hint = charset_hint.strip()
        if len(charset_hint) > 0:
            try:
                charset_hint = codecs.lookup(charset_hint).name
            except LookupError:
                _exit_with_error(f"Invalid charset: {charset_hint}")
        else:
            charset_hint = None

    stream_info = None
    if (
        extension_hint is not None
        or mime_type_hint is not None
        or charset_hint is not None
    ):
        stream_info = StreamInfo(
            extension=extension_hint, mimetype=mime_type_hint, charset=charset_hint
        )

    if args.list_plugins:
        # List installed plugins, then exit
        print("Installed MarkItDown 3rd-party Plugins:\n")
        plugin_entry_points = list(entry_points(group="markitdown.plugin"))
        if len(plugin_entry_points) == 0:
            print("  * No 3rd-party plugins installed.")
            print(
                "\nFind plugins by searching for the hashtag #markitdown-plugin on GitHub.\n"
            )
        else:
            for entry_point in plugin_entry_points:
                print(f"  * {entry_point.name:<16}\t(package: {entry_point.value})")
            print(
                "\nUse the -p (or --use-plugins) option to enable 3rd-party plugins.\n"
            )
        sys.exit(0)

    # Prepare kwargs for MarkItDown
    md_kwargs: Dict[str, Any] = {}

    if args.use_docintel:
        if args.endpoint is None:
            _exit_with_error(
                "Document Intelligence Endpoint is required when using Document Intelligence."
            )
        elif args.filename is None:
            _exit_with_error("Filename is required when using Document Intelligence.")

        md_kwargs["docintel_endpoint"] = args.endpoint
    elif args.use_cu:
        if args.cu_endpoint is None:
            _exit_with_error(
                "Content Understanding Endpoint (--cu-endpoint) is required when using --use-cu."
            )
        elif args.filename is None:
            _exit_with_error("Filename is required when using Content Understanding.")

        md_kwargs["cu_endpoint"] = args.cu_endpoint
        if args.cu_analyzer is not None:
            md_kwargs["cu_analyzer_id"] = args.cu_analyzer
        if args.cu_file_types is not None:
            from .converters import ContentUnderstandingFileType

            type_names = [
                t.strip().lower() for t in args.cu_file_types.split(",") if t.strip()
            ]
            cu_types = []
            for name in type_names:
                try:
                    cu_types.append(ContentUnderstandingFileType(name))
                except ValueError:
                    _exit_with_error(f"Unknown file type: {name}")
            md_kwargs["cu_file_types"] = cu_types

    # Handle OCR options
    use_plugins = args.use_plugins
    if args.use_ocr:
        use_plugins = True
        md_kwargs["use_ocr"] = True
        md_kwargs["ocr_engine"] = args.ocr_engine
        md_kwargs["use_tesseract"] = args.ocr_engine == "tesseract"
        md_kwargs["ocr_model_size"] = getattr(args, "ocr_model_size", None)

        if args.ocr_engine == "tesseract":
            if args.tesseract_path:
                md_kwargs["tesseract_path"] = args.tesseract_path
            md_kwargs["tesseract_lang"] = args.tesseract_lang
        elif args.ocr_engine == "llm":
            if not args.llm_model:
                _exit_with_error(
                    "--llm-model is required when --ocr-engine=llm. "
                    "Example: --llm-model=gpt-4o"
                )
            from openai import OpenAI

            md_kwargs["llm_client"] = OpenAI()
            md_kwargs["llm_model"] = args.llm_model

    md_kwargs["enable_plugins"] = use_plugins

    # Page range
    if args.pages:
        from ._page_range import parse_pages
        md_kwargs["pages"] = parse_pages(args.pages)

    # Metadata mode (default: no metadata, --with-metadata to include, --metadata-only for just metadata)
    md_kwargs["with_metadata"] = args.with_metadata
    md_kwargs["metadata_only"] = args.metadata_only

    if args.metadata_only and args.with_metadata:
        _exit_with_error("--metadata-only and --with-metadata are mutually exclusive.")

    # Read file bytes for the router
    if args.filename is not None:
        with open(args.filename, "rb") as f:
            file_bytes = f.read()
        file_path = args.filename
        extension = os.path.splitext(args.filename)[1]
    else:
        file_bytes = sys.stdin.buffer.read()
        file_path = "stdin"
        extension = extension_hint

    # LibreOffice path override
    if args.libreoffice_path:
        from ._libreoffice_detect import setLibreOfficePath
        try:
            setLibreOfficePath(args.libreoffice_path)
        except FileNotFoundError as e:
            _exit_with_error(str(e))

    # Build router arguments
    route_kwargs = {
        "enable_plugins": use_plugins,
        "with_metadata": args.with_metadata,
        "metadata_only": args.metadata_only,
        "keep_data_uris": args.keep_data_uris,
    }

    if args.use_docintel:
        route_kwargs["docintel_endpoint"] = args.endpoint
    elif args.use_cu:
        route_kwargs["cu_endpoint"] = args.cu_endpoint
        if args.cu_analyzer is not None:
            route_kwargs["cu_analyzer_id"] = args.cu_analyzer
        if args.cu_file_types is not None:
            route_kwargs["cu_file_types"] = md_kwargs.get("cu_file_types")

    if args.use_ocr:
        route_kwargs["ocr_engine"] = args.ocr_engine
        route_kwargs["ocr_model_size"] = getattr(args, "ocr_model_size", None)
        if args.ocr_engine == "tesseract":
            if args.tesseract_path:
                route_kwargs["tesseract_path"] = args.tesseract_path
            route_kwargs["tesseract_lang"] = args.tesseract_lang
        elif args.ocr_engine == "llm":
            if args.llm_model:
                route_kwargs["llm_client"] = md_kwargs.get("llm_client")
                route_kwargs["llm_model"] = args.llm_model

    from ._router import route_document

    # ---- Multi-indicator extraction ----
    if args.extract:
        extract_list = [s.strip() for s in args.extract.split(",") if s.strip()]
        from ._extractor import extract_to_json

        # Auto-enable OCR when 'ocr' is in extract list
        if "ocr" in extract_list:
            args.use_ocr = True

        output_paths = {}
        for ind in ("text", "document", "ocr", "html", "metadata", "magika", "thumbnail"):
            val = getattr(args, f"{ind}_out", None)
            if val:
                output_paths[ind] = val

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

        ocr_lang = getattr(args, "tesseract_lang", "eng+chi_sim")
        ocr_engine = getattr(args, "ocr_engine", "paddleocr")
        ocr_model_size = getattr(args, "ocr_model_size", None)
        result_json = extract_to_json(
            file_path=file_path,
            file_bytes=file_bytes,
            extract_list=extract_list,
            pages_spec_str=args.pages,
            ocr_engine=ocr_engine,
            ocr_lang=ocr_lang,
            ocr_model_size=ocr_model_size,
            thumbnail_format="png",
            exiftool_path=exiftool_path,
            output_paths=output_paths,
        )

        import json
        output = json.dumps(result_json, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
        else:
            print(output)
        return

    # ---- Single extraction (original behavior) ----
    markdown_text = route_document(
        file_path=file_path,
        file_bytes=file_bytes,
        extension=extension,
        enable_ocr=args.use_ocr,
        pages_spec_str=args.pages,
        **route_kwargs
    )

    result = DocumentConverterResult(markdown=markdown_text)
    _handle_output(args, result)


def _handle_output(args, result: DocumentConverterResult):
    """Handle output to stdout or file"""
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result.markdown)
    else:
        # Handle stdout encoding errors more gracefully
        print(
            result.markdown.encode(sys.stdout.encoding, errors="replace").decode(
                sys.stdout.encoding
            )
        )


def _exit_with_error(message: str):
    print(message)
    sys.exit(1)


if __name__ == "__main__":
    main()
