"""Loss-resistant extraction of NJ DoAS multi-program comparison tables.

The published Side-by-Side PDF has seven logical columns: a field label
followed by six *different* programs. PDF table detection may report more
physical columns when spurious vertical rules divide a logical column. A normal PDF-to-Markdown conversion can discard the
short second header row, leaving unlabeled figures. For this one known source,
use the document's ruled table geometry and write a section per program.

We deliberately fail closed if any page cannot be mapped to six unambiguous
program columns. The team-curated, source-labeled summary is an independent
knowledge source; silently indexing ambiguous numbers is much worse than an
extraction failure. No numeric limits are hard-coded here.
"""

from __future__ import annotations

import re
from itertools import combinations

from careonex_extract.convert import normalise  # circular import avoided: imported at call time in convert.py

# Table headings in the official March 2026 four-page NJ DoAS comparison PDF.
_PROGRAM_SETS: tuple[tuple[str, ...], ...] = (
    ("MLTSS/PACE", "JACC", "SRCP", "AADSP", "CHSP", "OAA"),
    ("PAAD", "Senior Gold", "MSPs (QMB/SLMB/QI)", "Lifeline", "HAAAD/NJHAP", "USF/LIHEAP"),
)


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").casefold())


def _is_program_header(cell: str, name: str) -> bool:
    key = _squash(cell)
    if name == "MLTSS/PACE":
        return "mltsspace" in key
    if name == "MSPs (QMB/SLMB/QI)":
        return "msp" in key and ("qmb" in key or "slmb" in key)
    if name == "HAAAD/NJHAP":
        return "haaad" in key and "njhap" in key
    return _squash(name) in key


def _remove_empty_spacer_columns(rows: list[list[str | None]]) -> tuple[list[list[str | None]], tuple[int, ...]]:
    """Remove only columns entirely empty across *all* rows, never nonempty data.

    PyMuPDF may detect thin ruled spacer columns in the original comparison PDF.
    Keep source column indices so value cells can still be re-read by geometry.
    """
    if not rows or len({len(row) for row in rows}) != 1:
        raise ValueError("inconsistent DoAS table column count")
    used = tuple(j for j in range(len(rows[0]))
                 if any(_safe_cell(row[j]) for row in rows))
    return [[row[j] for j in used] for row in rows], used


def _identify_headers(rows: list[list[str | None]]) -> tuple[tuple[str, ...], int] | None:
    """Verify six aligned program headings, including headings split over rows.

    Return the earliest *complete* header row, so header fragments cannot be
    mistaken for data. Group headings alone never pass this check.
    """
    for start in range(min(8, len(rows))):
        for end in range(start, min(start + 3, len(rows))):
            segment = rows[start:end + 1]
            if any(len(row) != 7 for row in segment):
                continue
            merged = [" ".join(_safe_cell(row[j]) for row in segment if _safe_cell(row[j]))
                      for j in range(7)]
            candidates = [names for names in _PROGRAM_SETS
                          if all(_is_program_header(merged[j], names[j - 1])
                                 for j in range(1, 7))]
            if len(candidates) == 1:
                return candidates[0], end
            if len(candidates) > 1:
                return None
    return None


def _map_logical_columns(
    rows: list[list[str | None]],
) -> tuple[list[list[str]], tuple[tuple[int, ...], ...], tuple[str, ...], int] | None:
    """Prove a seven-column assignment even when PDF rules yield 8/9+ columns.

    The only acceptable coalescing is a *unique* contiguous grouping whose
    header text identifies all six programs in the correct order. This is not
    a blanket trim-to-seven rule: arbitrary, nonempty columns are never
    dropped or assigned to a neighbouring program without a verified header.
    Return groups of *original* physical-column indices for cell geometry.
    """
    cleaned, original_indices = _remove_empty_spacer_columns(rows)
    count = len(original_indices)
    if count < 7 or count > 12:
        return None

    matches: list[tuple[list[list[str]], tuple[tuple[int, ...], ...], tuple[str, ...], int]] = []
    # Seven logical groups = six boundaries. Every original nonempty column
    # must belong to exactly one group; their order can never change.
    for boundaries in combinations(range(1, count), 6):
        edges = (0,) + boundaries + (count,)
        groups = tuple(tuple(original_indices[edges[j]:edges[j + 1]]) for j in range(7))
        logical_rows = [
            [" ".join(_safe_cell(row[i]) for i in group if _safe_cell(row[i]))
             for group in groups]
            for row in rows
        ]
        identified = _identify_headers(logical_rows)
        if identified is not None:
            matches.append((logical_rows, groups, *identified))
        if len(matches) > 1:
            # Ambiguous boundaries could silently move a benefit or limit
            # to the wrong program. Fail closed instead.
            return None
    return matches[0] if len(matches) == 1 else None


def _safe_cell(s: str | None) -> str:
    return " ".join((s or "").replace("\u2022", " - ").split())


def _verified_cell_text(page, cell_bbox, extracted: str | None) -> str:
    """Use the PDF's own text spans when table extraction distorts punctuation.

    PyMuPDF's table.extract() sometimes returns e.g. "$2123 ," or "20years"
    on Windows even though the source text says "$2,123" and "20 years".
    Read the *same geometric cell* independently; use it only when the entire
    text matches after removing whitespace and commas ONLY, so decimals,
    eligibility numbers and meaningful punctuation cannot change silently.
    """
    import pymupdf

    original = _safe_cell(extracted)
    if cell_bbox is None or not original:
        return original
    alternative = _safe_cell(page.get_text("text", clip=pymupdf.Rect(cell_bbox), sort=True))
    # Preserve decimals, ranges, inequality signs, percentages and currency
    # symbols exactly: ignoring *only* whitespace and comma placement avoids
    # changing a number's meaning while repairing split thousands separators.
    comparison_key = lambda value: re.sub(r"[,\s]", "", value.casefold())
    if alternative and comparison_key(alternative) == comparison_key(original):
        return alternative
    return original


def doas_side_by_side_to_markdown(data: bytes) -> str:
    """Reconstruct every row as `## Program` / `**field**: value` with explicit ownership.

    Raises ValueError rather than mixing eligibility or dollar limits from
    unrelated programs when the PDF table geometry cannot be established.
    """
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    if len(doc) != 4:
        raise ValueError(f"unexpected DoAS Side-by-Side page count ({len(doc)}); manual verification required")

    extracted: list[str] = ["# NJ Division of Aging Services Programs Side-by-Side (2026)"]
    for page_index, page in enumerate(doc):
        expected = _PROGRAM_SETS[0 if page_index < 2 else 1]
        found = None
        # Verified on the original March 2026 four-page PDF: `lines` treats
        # background rectangles as spurious column borders (9 physical columns
        # on pages 1-2). `lines_strict` ignores those fill-only graphics and
        # reconstructs the 7 actual ruled columns on all four pages.
        for table in page.find_tables(strategy="lines_strict").tables:
            raw_rows = table.extract()
            try:
                mapping = _map_logical_columns(raw_rows)
            except ValueError:
                continue
            if mapping is None:
                continue
            rows, physical_groups, names, header_index = mapping
            if names == expected:
                found = (table, raw_rows, rows, physical_groups, header_index)
                break
        if found is None:
            raise ValueError(
                f"DoAS comparison page {page_index + 1}: program columns could not be verified. "
                "Do not index this page as unlabeled Markdown; review the PDF layout."
            )
        table, raw_rows, rows, physical_groups, header_index = found
        by_program: dict[str, list[tuple[str, str]]] = {name: [] for name in expected}
        prev_label = ""
        for row_index, row in enumerate(rows[header_index + 1:], start=header_index + 1):
            if len(row) != 7:
                raise ValueError(f"DoAS comparison page {page_index + 1}: inconsistent program column layout")
            # Re-read each physical slice before combining a split logical
            # column. Never widen one bbox over multiple ruled cells: that can
            # inadvertently pull text from a neighboring program.
            cells = table.rows[row_index].cells
            logical_values = [
                " ".join(_verified_cell_text(page, cells[j], raw_rows[row_index][j])
                         for j in group if _safe_cell(raw_rows[row_index][j]))
                for group in physical_groups
            ]
            label = _safe_cell(logical_values[0])
            # Wrapped first-column labels may span two adjacent PDF rows.
            if label:
                prev_label = label
            if not prev_label:
                continue
            for i, name in enumerate(expected, 1):
                value = _safe_cell(logical_values[i])
                if value:
                    by_program[name].append((prev_label, value))
        if not any(by_program.values()):
            raise ValueError(f"DoAS comparison page {page_index + 1}: no labeled table entries found")
        extracted.append(f"\n## Page {page_index + 1} (March 2026)")
        for name, fields in by_program.items():
            if not fields:
                raise ValueError(f"DoAS comparison page {page_index + 1}: missing data for {name}")
            extracted.append(f"\n### {name}")
            extracted.extend(f"- **{label}:** {value}" for label, value in fields)
        # The notes stay attached to the page, not to a particular program.
        footer = page.get_text("text")
        footnotes = [line.strip() for line in footer.splitlines() if line.strip().startswith("*")]
        if footnotes:
            extracted.append("\n### Page notes")
            extracted.extend("- " + n for n in footnotes)
    return normalise("\n".join(extracted))
