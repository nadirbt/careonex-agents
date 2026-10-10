"""Loss-resistant extraction of NJ DoAS multi-program comparison tables.

The published Side-by-Side PDF has seven columns: a field label followed by
six *different* programs. A normal PDF-to-Markdown conversion can discard the
short second header row, leaving unlabeled figures. For this one known source,
use the document's ruled table geometry and write a section per program.

We deliberately fail closed if any page cannot be mapped to six unambiguous
program columns. The team-curated, source-labeled summary is an independent
knowledge source; silently indexing ambiguous numbers is much worse than an
extraction failure. No numeric limits are hard-coded here.
"""

from __future__ import annotations

import re

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
        return "haaad" in key or "njhap" in key
    return _squash(name) in key


def _identify_headers(rows: list[list[str | None]]) -> tuple[tuple[str, ...], int] | None:
    """Locate the six *column-specific* headers, never a merged grouping row."""
    for i, row in enumerate(rows[:8]):
        if len(row) != 7:
            continue
        for names in _PROGRAM_SETS:
            if all(_is_program_header(str(row[j] or ""), names[j - 1]) for j in range(1, 7)):
                return names, i
    return None


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
        for table in page.find_tables(strategy="lines").tables:
            rows = table.extract()
            identified = _identify_headers(rows)
            if identified and identified[0] == expected:
                found = (table, rows, identified[1])
                break
        if found is None:
            raise ValueError(
                f"DoAS comparison page {page_index + 1}: program columns could not be verified. "
                "Do not index this page as unlabeled Markdown; review the PDF layout."
            )
        table, rows, header_index = found
        by_program: dict[str, list[tuple[str, str]]] = {name: [] for name in expected}
        prev_label = ""
        for row_index, row in enumerate(rows[header_index + 1:], start=header_index + 1):
            if len(row) != 7:
                continue
            # The verified ruled table supplies both column ownership and
            # clip rectangles for a faithful, independently checked re-read.
            cell_boxes = table.rows[row_index].cells
            label = _verified_cell_text(page, cell_boxes[0], row[0])
            # Wrapped first-column labels may span two adjacent PDF rows.
            if label:
                prev_label = label
            if not prev_label:
                continue
            for i, name in enumerate(expected, 1):
                value = _verified_cell_text(page, cell_boxes[i], row[i])
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
