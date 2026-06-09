import sys
import io
import re
from typing import BinaryIO, Any, Set

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._stream_info import StreamInfo
from .._exceptions import MissingDependencyException, MISSING_DEPENDENCY_MESSAGE
from .._page_range import resolve as resolve_pages

PARTIAL_NUMBERING_PATTERN = re.compile(r"^\.\d+$")


def _merge_partial_numbering_lines(text: str) -> str:
    lines = text.split("\n")
    result_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if PARTIAL_NUMBERING_PATTERN.match(stripped):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                next_line = lines[j].strip()
                result_lines.append(f"{stripped} {next_line}")
                i = j + 1
            else:
                result_lines.append(line)
                i += 1
        else:
            result_lines.append(line)
            i += 1
    return "\n".join(result_lines)


_dependency_exc_info = None
try:
    import pdfminer
    import pdfminer.high_level
    import pdfplumber
except ImportError:
    _dependency_exc_info = sys.exc_info()


ACCEPTED_MIME_TYPE_PREFIXES = [
    "application/pdf",
    "application/x-pdf",
]

ACCEPTED_FILE_EXTENSIONS = [".pdf"]


def _to_markdown_table(table: list[list[str]], include_separator: bool = True) -> str:
    if not table:
        return ""
    table = [[cell if cell is not None else "" for cell in row] for row in table]
    table = [row for row in table if any(cell.strip() for cell in row)]
    if not table:
        return ""
    col_widths = [max(len(str(cell)) for cell in col) for col in zip(*table)]

    def fmt_row(row: list[str]) -> str:
        return "|" + "|".join(str(cell).ljust(width) for cell, width in zip(row, col_widths)) + "|"

    if include_separator:
        header, *rows = table
        md = [fmt_row(header)]
        md.append("|" + "|".join("-" * w for w in col_widths) + "|")
        for row in rows:
            md.append(fmt_row(row))
    else:
        md = [fmt_row(row) for row in table]
    return "\n".join(md)


def _extract_form_content_from_words(page: Any) -> str | None:
    words = page.extract_words(keep_blank_chars=True, x_tolerance=3, y_tolerance=3)
    if not words:
        return None
    y_tolerance = 5
    rows_by_y: dict[float, list[dict]] = {}
    for word in words:
        y_key = round(word["top"] / y_tolerance) * y_tolerance
        if y_key not in rows_by_y:
            rows_by_y[y_key] = []
        rows_by_y[y_key].append(word)
    sorted_y_keys = sorted(rows_by_y.keys())
    page_width = page.width if hasattr(page, "width") else 612
    row_info: list[dict] = []
    for y_key in sorted_y_keys:
        row_words = sorted(rows_by_y[y_key], key=lambda w: w["x0"])
        if not row_words:
            continue
        first_x0 = row_words[0]["x0"]
        last_x1 = row_words[-1]["x1"]
        line_width = last_x1 - first_x0
        combined_text = " ".join(w["text"] for w in row_words)
        x_positions = [w["x0"] for w in row_words]
        x_groups: list[float] = []
        for x in sorted(x_positions):
            if not x_groups or x - x_groups[-1] > 50:
                x_groups.append(x)
        is_paragraph = line_width > page_width * 0.55 and len(combined_text) > 60
        has_partial_numbering = False
        if row_words:
            first_word = row_words[0]["text"].strip()
            if PARTIAL_NUMBERING_PATTERN.match(first_word):
                has_partial_numbering = True
        row_info.append({"y_key": y_key, "words": row_words, "text": combined_text, "x_groups": x_groups, "is_paragraph": is_paragraph, "num_columns": len(x_groups), "has_partial_numbering": has_partial_numbering})
    all_table_x_positions: list[float] = []
    for info in row_info:
        if info["num_columns"] >= 3 and not info["is_paragraph"]:
            all_table_x_positions.extend(info["x_groups"])
    if not all_table_x_positions:
        return None
    all_table_x_positions.sort()
    gaps = []
    for i in range(len(all_table_x_positions) - 1):
        gap = all_table_x_positions[i + 1] - all_table_x_positions[i]
        if gap > 5:
            gaps.append(gap)
    if gaps and len(gaps) >= 3:
        sorted_gaps = sorted(gaps)
        percentile_70_idx = int(len(sorted_gaps) * 0.70)
        adaptive_tolerance = sorted_gaps[percentile_70_idx]
        adaptive_tolerance = max(25, min(50, adaptive_tolerance))
    else:
        adaptive_tolerance = 35
    global_columns: list[float] = []
    for x in all_table_x_positions:
        if not global_columns or x - global_columns[-1] > adaptive_tolerance:
            global_columns.append(x)
    if len(global_columns) > 1:
        content_width = global_columns[-1] - global_columns[0]
        avg_col_width = content_width / len(global_columns)
        if avg_col_width < 30:
            return None
        columns_per_inch = len(global_columns) / (content_width / 72)
        if columns_per_inch > 10:
            return None
        adaptive_max_columns = int(20 * (page_width / 612))
        adaptive_max_columns = max(15, adaptive_max_columns)
        if len(global_columns) > adaptive_max_columns:
            return None
    else:
        return None
    for info in row_info:
        if info["is_paragraph"]:
            info["is_table_row"] = False
            continue
        if info["has_partial_numbering"]:
            info["is_table_row"] = False
            continue
        aligned_columns: set[int] = set()
        for word in info["words"]:
            word_x = word["x0"]
            for col_idx, col_x in enumerate(global_columns):
                if abs(word_x - col_x) < 40:
                    aligned_columns.add(col_idx)
                    break
        info["is_table_row"] = len(aligned_columns) >= 2
    table_regions: list[tuple[int, int]] = []
    i = 0
    while i < len(row_info):
        if row_info[i]["is_table_row"]:
            start_idx = i
            while i < len(row_info) and row_info[i]["is_table_row"]:
                i += 1
            end_idx = i
            table_regions.append((start_idx, end_idx))
        else:
            i += 1
    total_table_rows = sum(end - start for start, end in table_regions)
    if len(row_info) > 0 and total_table_rows / len(row_info) < 0.2:
        return None
    result_lines: list[str] = []
    num_cols = len(global_columns)

    def extract_cells(info: dict) -> list[str]:
        cells: list[str] = ["" for _ in range(num_cols)]
        for word in info["words"]:
            word_x = word["x0"]
            assigned_col = num_cols - 1
            for col_idx in range(num_cols - 1):
                col_end = global_columns[col_idx + 1]
                if word_x < col_end - 20:
                    assigned_col = col_idx
                    break
            if cells[assigned_col]:
                cells[assigned_col] += " " + word["text"]
            else:
                cells[assigned_col] = word["text"]
        return cells

    idx = 0
    while idx < len(row_info):
        info = row_info[idx]
        table_region = None
        for start, end in table_regions:
            if idx == start:
                table_region = (start, end)
                break
        if table_region:
            start, end = table_region
            table_data: list[list[str]] = []
            for table_idx in range(start, end):
                cells = extract_cells(row_info[table_idx])
                table_data.append(cells)
            if table_data:
                col_widths = [max(len(row[col]) for row in table_data) for col in range(num_cols)]
                col_widths = [max(w, 3) for w in col_widths]
                header = table_data[0]
                header_str = "| " + " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(header)) + " |"
                result_lines.append(header_str)
                separator = "| " + " | ".join("-" * col_widths[i] for i in range(num_cols)) + " |"
                result_lines.append(separator)
                for row in table_data[1:]:
                    row_str = "| " + " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"
                    result_lines.append(row_str)
            idx = end
        else:
            in_table = False
            for start, end in table_regions:
                if start < idx < end:
                    in_table = True
                    break
            if not in_table:
                result_lines.append(info["text"])
            idx += 1
    return "\n".join(result_lines)


def _extract_tables_from_words(page: Any) -> list[list[list[str]]]:
    words = page.extract_words(keep_blank_chars=True, x_tolerance=3, y_tolerance=3)
    if not words:
        return []
    y_tolerance = 5
    rows_by_y: dict[float, list[dict]] = {}
    for word in words:
        y_key = round(word["top"] / y_tolerance) * y_tolerance
        if y_key not in rows_by_y:
            rows_by_y[y_key] = []
        rows_by_y[y_key].append(word)
    sorted_y_keys = sorted(rows_by_y.keys())
    all_x_positions = []
    for words_in_row in rows_by_y.values():
        for word in words_in_row:
            all_x_positions.append(word["x0"])
    if not all_x_positions:
        return []
    all_x_positions.sort()
    x_tolerance_col = 20
    column_starts: list[float] = []
    for x in all_x_positions:
        if not column_starts or x - column_starts[-1] > x_tolerance_col:
            column_starts.append(x)
    if len(column_starts) < 3 or len(column_starts) > 10:
        return []
    table_rows = []
    for y_key in sorted_y_keys:
        words_in_row = sorted(rows_by_y[y_key], key=lambda w: w["x0"])
        row_data = [""] * len(column_starts)
        for word in words_in_row:
            best_col = 0
            min_dist = float("inf")
            for i, col_x in enumerate(column_starts):
                dist = abs(word["x0"] - col_x)
                if dist < min_dist:
                    min_dist = dist
                    best_col = i
            if row_data[best_col]:
                row_data[best_col] += " " + word["text"]
            else:
                row_data[best_col] = word["text"]
        non_empty = sum(1 for cell in row_data if cell.strip())
        if non_empty >= 2:
            table_rows.append(row_data)
    if len(table_rows) < 3:
        return []
    long_cell_count = 0
    total_cell_count = 0
    for row in table_rows:
        for cell in row:
            if cell.strip():
                total_cell_count += 1
                if len(cell.strip()) > 30:
                    long_cell_count += 1
    if total_cell_count > 0 and long_cell_count / total_cell_count > 0.3:
        return []
    return [table_rows]


class PdfConverter(DocumentConverter):
    def accepts(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> bool:
        mimetype = (stream_info.mimetype or "").lower()
        extension = (stream_info.extension or "").lower()
        if extension in ACCEPTED_FILE_EXTENSIONS:
            return True
        for prefix in ACCEPTED_MIME_TYPE_PREFIXES:
            if mimetype.startswith(prefix):
                return True
        return False

    def convert(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> DocumentConverterResult:
        if _dependency_exc_info is not None:
            raise MissingDependencyException(
                MISSING_DEPENDENCY_MESSAGE.format(converter=type(self).__name__, extension=".pdf", feature="pdf")
            ) from _dependency_exc_info[1].with_traceback(_dependency_exc_info[2])

        assert isinstance(file_stream, io.IOBase)
        pdf_bytes = io.BytesIO(file_stream.read())

        # Resolve page selection
        pages_spec = kwargs.get("pages")
        try:
            with pdfplumber.open(pdf_bytes) as pdf:
                total = len(pdf.pages)
                selected = resolve_pages(pages_spec, total)
        except Exception:
            selected = None

        try:
            markdown_chunks: list[str] = []
            form_page_count = 0
            plain_page_indices: list[int] = []

            with pdfplumber.open(pdf_bytes) as pdf:
                for page_idx, page in enumerate(pdf.pages):
                    page_num_1 = page_idx + 1
                    if selected is not None and page_num_1 not in selected:
                        page.close()
                        continue

                    page_content = _extract_form_content_from_words(page)

                    if page_content is not None:
                        form_page_count += 1
                        if page_content.strip():
                            markdown_chunks.append(page_content)
                    else:
                        plain_page_indices.append(page_idx)
                        text = page.extract_text()
                        if text and text.strip():
                            markdown_chunks.append(text.strip())

                    page.close()

            if form_page_count == 0:
                pdf_bytes.seek(0)
                markdown = pdfminer.high_level.extract_text(pdf_bytes)
            else:
                markdown = "\n\n".join(markdown_chunks).strip()

        except Exception:
            pdf_bytes.seek(0)
            markdown = pdfminer.high_level.extract_text(pdf_bytes)

        if not markdown:
            pdf_bytes.seek(0)
            markdown = pdfminer.high_level.extract_text(pdf_bytes)

        markdown = _merge_partial_numbering_lines(markdown)
        return DocumentConverterResult(markdown=markdown)
