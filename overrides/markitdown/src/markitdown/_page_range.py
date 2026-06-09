import re
from typing import Set


def parse_pages(spec: str | None) -> Set[int] | None:
    """Parse a page range specification like '1,3,5-7,10-12' into a set of 1-indexed page numbers.

    Returns None when spec is empty/None (meaning "all pages").
    Open-ended ranges:
      '-N'  → pages 1 through N
      'N-'  → pages N through sentinel MAX (resolved at runtime via resolve_open_end)
      '-N,M-' (mixed) is also supported.
    """
    if not spec or not spec.strip():
        return None

    result: Set[int] = set()
    open_end_start: int | None = None    # e.g. "5-" means pages ≥ 5
    open_start_end: int | None = None    # e.g. "-5" means pages ≥ 1 ∧ ≤ 5
    has_open_end = False
    has_open_start = False

    parts = [p.strip() for p in spec.split(",") if p.strip()]
    for part in parts:
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            if start > end:
                start, end = end, start
            result.update(range(start, end + 1))
            continue

        m = re.fullmatch(r"(\d+)\s*-", part)
        if m:
            open_end_start = int(m.group(1))
            has_open_end = True
            continue

        m = re.fullmatch(r"-\s*(\d+)", part)
        if m:
            open_start_end = int(m.group(1))
            has_open_start = True
            result.update(range(1, open_start_end + 1))
            continue

        m = re.fullmatch(r"(\d+)", part)
        if m:
            result.add(int(m.group(1)))
            continue

        raise ValueError(f"Invalid page range: '{part}'")

    if has_open_end and not result and not has_open_start:
        # Only open-end spec like "5-" → store as sentinel
        return open_end_start

    if has_open_end:
        if open_end_start is not None:
            return _PageRangeExplicit(result, open_end_start)
        return result

    return result if result else None


class _PageRangeExplicit:
    """Internal wrapper that carries an open-end segment alongside explicit pages."""
    def __init__(self, pages: set[int], open_end_start: int):
        self.explicit = pages
        self.open_end_start = open_end_start

    def resolve(self, total_pages: int) -> set[int]:
        s = set(self.explicit)
        s.update(range(self.open_end_start, total_pages + 1))
        return s

    def __repr__(self):
        return f"<pages={self.explicit}, from={self.open_end_start}-end>"


def resolve(pages_spec: Set[int] | int | _PageRangeExplicit | None, total_pages: int) -> set[int] | None:
    """Resolve a pages spec against the actual total page count.
    Returns a concrete set of 1-indexed pages, or None meaning all pages.
    """
    if pages_spec is None:
        return None
    if isinstance(pages_spec, int):
        # bare open-end like {5}
        return set(range(pages_spec, total_pages + 1))
    if isinstance(pages_spec, _PageRangeExplicit):
        return pages_spec.resolve(total_pages)
    if isinstance(pages_spec, set):
        return pages_spec
    return pages_spec
