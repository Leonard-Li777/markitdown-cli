#!/usr/bin/env python3
"""
Render specific page(s) from an Office document to PDF via LibreOffice UNO API.

Usage:
    python render_page.py <input.docx|pptx|xlsx> <page_num> <output.pdf> [--port PORT] [--type auto|writer|impress|calc]

Relies on being executed by LibreOffice's built-in Python interpreter
(e.g. ``C:\\Program Files\\LibreOffice\\program\\python.exe``) so that
the ``uno`` module is available.
"""

import json
import os
import sys
import traceback

try:
    import uno
    from com.sun.star.beans import PropertyValue
    from com.sun.star.connection import NoConnectException
except ImportError:
    print(json.dumps({"status": "error", "error": "uno module not available"}))
    sys.exit(1)


def _make_prop(name: str, value) -> PropertyValue:
    p = PropertyValue()
    p.Name = name
    p.Value = value
    return p


def main():
    if len(sys.argv) < 4:
        print(json.dumps({"status": "error", "error": "Usage: render_page.py <input> <page> <output> [--port PORT] [--type TYPE]"}))
        sys.exit(1)

    input_path = os.path.abspath(sys.argv[1])
    page_num = int(sys.argv[2])
    output_path = os.path.abspath(sys.argv[3])
    port = 2083
    doc_type = "auto"

    i = 4
    while i < len(sys.argv):
        if sys.argv[i] == "--port":
            port = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--type":
            doc_type = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    if not os.path.isfile(input_path):
        print(json.dumps({"status": "error", "error": f"Input not found: {input_path}"}))
        sys.exit(1)

    # Map extension to PDF export filter name
    ext = os.path.splitext(input_path)[1].lower()
    filter_map = {
        ".docx": "writer_pdf_Export",
        ".doc":  "writer_pdf_Export",
        ".odt":  "writer_pdf_Export",
        ".pptx": "impress_pdf_Export",
        ".ppt":  "impress_pdf_Export",
        ".odp":  "impress_pdf_Export",
        ".xlsx": "calc_pdf_Export",
        ".xls":  "calc_pdf_Export",
        ".ods":  "calc_pdf_Export",
    }
    filter_name = filter_map.get(ext, "writer_pdf_Export")

    try:
        # Resolve UNO connection
        local_ctx = uno.getComponentContext()
        resolver = local_ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", local_ctx)
        ctx = resolver.resolve(
            f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext")
        desktop = ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", ctx)

        # Open the document
        file_url = "file:///" + input_path.replace("\\", "/")
        doc = desktop.loadComponentFromURL(file_url, "_blank", 0, ())

        if doc is None:
            print(json.dumps({"status": "error", "error": "Failed to open document"}))
            sys.exit(1)

        # PDF export properties: filter + page range
        props = [
            _make_prop("FilterName", filter_name),
            _make_prop("PageRange", str(page_num)),
        ]

        out_url = "file:///" + output_path.replace("\\", "/")

        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        doc.storeToURL(out_url, tuple(props))
        doc.close(True)

        if os.path.isfile(output_path):
            print(json.dumps({"status": "ok", "output": output_path, "size": os.path.getsize(output_path)}))
        else:
            print(json.dumps({"status": "error", "error": "Output file not created"}))

    except NoConnectException:
        print(json.dumps({"status": "error", "error": f"Cannot connect to LO listener on port {port}"}))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e), "traceback": traceback.format_exc()}))
        sys.exit(1)


if __name__ == "__main__":
    main()
