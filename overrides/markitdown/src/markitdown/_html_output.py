import markdown as _md_lib


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 860px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #1f1f1f; }}
  pre {{ background: #f6f8fa; padding: 16px; border-radius: 6px; overflow-x: auto; }}
  code {{ background: #f6f8fa; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }}
  pre code {{ padding: 0; background: none; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #d0d7de; padding: 8px 12px; text-align: left; }}
  th {{ background: #f6f8fa; }}
  img {{ max-width: 100%; }}
  blockquote {{ margin: 0; padding-left: 16px; border-left: 4px solid #d0d7de; color: #656d76; }}
</style>
<title>{title}</title>
</head>
<body>
{content}
</body>
</html>"""


def convert_to_html(markdown_text: str = "", title: str = "MarkItDown Output", **kwargs) -> str:
    if not markdown_text and kwargs.get("file_bytes"):
        fb = kwargs["file_bytes"]
        if fb:
            try:
                from ._extractor import _extract_text_raw
                markdown_text = _extract_text_raw(fb)
            except Exception:
                markdown_text = ""
    if kwargs.get("file_path") and title == "MarkItDown Output":
        import os
        title = os.path.basename(kwargs["file_path"])
    text = (markdown_text or "").lstrip("\ufeff")
    body = _md_lib.markdown(text, extensions=["fenced_code", "tables", "codehilite", "sane_lists"])
    return HTML_TEMPLATE.format(title=_escape_html(title), content=body)


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
