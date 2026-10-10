"""Section-safe, structure-first Markdown chunking without AWS calls.

Every chunk belongs to one heading path. Tables retain column headers; long paragraphs
split on sentences then words. Optional overlap repeats *prose only* from the previous
piece of the same section. Never merge different program headings to meet a size floor.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
CHUNKER_VERSION = "4"  # section-safe segmentation plus labelled wide-table row splitting


@dataclass
class Block:
    kind: str  # para | table
    text: str


@dataclass
class Section:
    heading_path: list[str]
    blocks: list[Block] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(len(b.text) + 2 for b in self.blocks)


@dataclass
class Chunk:
    order: int
    heading_path: list[str]
    text: str
    chars: int
    sha256: str

    @property
    def chunk_id(self) -> str:
        return f"{self.order:04d}-{self.sha256[:12]}"


def parse_sections(md: str) -> list[Section]:
    stack: list[tuple[int, str]] = []
    sections: list[Section] = [Section(heading_path=[])]
    para: list[str] = []
    table: list[str] = []

    def flush_para() -> None:
        nonlocal para
        if para:
            text = "\n".join(para).strip()
            if text:
                sections[-1].blocks.append(Block("para", text))
            para = []

    def flush_table() -> None:
        nonlocal table
        if table:
            sections[-1].blocks.append(Block("table", "\n".join(table).strip()))
            table = []

    for line in md.splitlines():
        match = HEADING_RE.match(line)
        if match:
            flush_para()
            flush_table()
            level, title = len(match.group(1)), match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            sections.append(Section(heading_path=[name for _, name in stack]))
        elif TABLE_ROW_RE.match(line):
            flush_para()
            table.append(line.rstrip())
        else:
            flush_table()
            if not line.strip():
                flush_para()
            else:
                para.append(line.rstrip())
    flush_para()
    flush_table()
    return [s for s in sections if s.blocks]


def _header(path: list[str]) -> str:
    return " > ".join(path) + "\n\n" if path else ""


def _split_words(text: str, limit: int) -> list[str]:
    """Bound long sentences, preserving every word in order; never silently truncate."""
    out: list[str] = []
    cur = ""
    for token in text.split():
        if len(token) > limit:
            if cur:
                out.append(cur)
                cur = ""
            # Overlong unbroken tokens are retained losslessly across pieces.
            out.extend(token[i:i + limit] for i in range(0, len(token), limit))
            continue
        if cur and len(cur) + 1 + len(token) > limit:
            out.append(cur)
            cur = token
        else:
            cur = (cur + " " + token).strip()
    if cur:
        out.append(cur)
    return out


def _split_long_text(text: str, limit: int) -> list[str]:
    units: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        if len(sentence) > limit:
            units.extend(_split_words(sentence, limit))
        elif sentence:
            units.append(sentence)
    out: list[str] = []
    cur = ""
    for unit in units:
        if cur and len(cur) + 1 + len(unit) > limit:
            out.append(cur)
            cur = unit
        else:
            cur = (cur + " " + unit).strip()
    if cur:
        out.append(cur)
    return out


def _table_cells(row: str) -> list[str]:
    """Parse Markdown cells while keeping escaped pipes inside their original cell."""
    content = row.strip()
    if content.startswith("|"):
        content = content[1:]
    if content.endswith("|"):
        content = content[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", content)]


def _labelled_table_row(row: str, header_line: str, limit: int) -> list[str]:
    """Split an oversized horizontal row into labelled, self-contained cell records.

    This is intentionally not a free-text split of the original row: financial
    amounts and requirements must retain BOTH their row label and program column.
    """
    columns = _table_cells(header_line)
    values = _table_cells(row)
    if len(columns) != len(values) or len(columns) < 2:
        raise ValueError("table row exceeds chunk cap; column count cannot be verified")
    row_field = columns[0] or "Row"
    row_label = values[0]
    if not row_label:
        raise ValueError("table row exceeds chunk cap; missing row label requires review")

    parts: list[str] = []
    for column, value in zip(columns[1:], values[1:]):
        if not column:
            raise ValueError("table row exceeds chunk cap; missing column label requires review")
        prefix = f"{row_field}: {row_label}\n{column}: "
        budget = limit - len(prefix)
        if budget <= 0:
            raise ValueError("table row exceeds chunk cap; row/column labels too long")
        # Reject unbroken, overlong values. Cutting a number or identifier midway
        # would produce unsafe, misleading financial/eligibility fragments.
        if any(len(token) > budget for token in value.split()):
            raise ValueError("table row exceeds chunk cap; cannot safely split an unbroken value")
        fragments = _split_long_text(value, budget) if len(value) > budget else [value]
        for fragment in fragments:
            text = prefix + fragment
            if len(text) > limit:
                raise ValueError("table row exceeds chunk cap; labelled cell over limit")
            parts.append(text)
    return parts


def _actual_program_header(rows: list[str]) -> str | None:
    """Recover program names from a two-tier PDF-extracted comparison table.

    Some PDFs export a *grouping/banner* header (with blank/fragmented cells),
    a Markdown separator, then the true program labels on the next row. Only
    promote that row when the following row explicitly says 'Program Title'.
    Otherwise preserve normal Markdown table handling, rather than guessing
    column names or silently attributing financial figures to the wrong group.
    """
    if len(rows) < 4:
        return None
    if not set(rows[1].replace("|", "").strip()) <= set("-: "):
        return None
    group_cells = _table_cells(rows[0])
    program_cells = _table_cells(rows[2])
    title_cells = _table_cells(rows[3])
    if len(program_cells) < 3 or len(group_cells) != len(program_cells):
        return None
    title = re.sub(r"<[^>]*>|[*_\s]", "", title_cells[0]).casefold() if title_cells else ""
    if title != "programtitle" or not all(program_cells[1:]):
        return None
    # Preserve the exact names from the actual PDF program-name row. The
    # first column is the row/field label, not another program name.
    program_cells[0] = "Field"
    return "|" + "|".join(program_cells) + "|"


def _split_table(table: str, limit: int) -> list[str]:
    rows = table.splitlines()
    if not rows:
        return []
    separator = len(rows) > 1 and set(rows[1].replace("|", "").strip()) <= set("-: ")
    header = rows[:2] if separator else rows[:1]
    body = rows[len(header):]
    program_header = _actual_program_header(rows) if separator else None
    if program_header is not None:
        # The line between separator and Program Title contains the real
        # program names; the first row is a garbled grouping/banner header.
        # Make the program row the table header and consume it from the body.
        header = [program_header, rows[1]]
        body = rows[3:]
    header_text = "\n".join(header)
    if len(header_text) > limit:
        raise ValueError("table header exceeds chunk cap; manual review required")
    out, cur = [], list(header)
    for row in body:
        if len(header_text) + 1 + len(row) > limit:
            # A very wide program-comparison row cannot be split arbitrarily:
            # emit separately labelled per-program records instead.
            if len(cur) > len(header):
                out.append("\n".join(cur))
                cur = list(header)
            out.extend(_labelled_table_row(row, header[0], limit))
            continue
        candidate = "\n".join(cur + [row])
        if len(candidate) > limit and len(cur) > len(header):
            out.append("\n".join(cur))
            cur = list(header)
        cur.append(row)
    if len(cur) > len(header) or not out:
        out.append("\n".join(cur))
    return out


def _unitize(blocks: list[Block], limit: int) -> list[Block]:
    units: list[Block] = []
    for block in blocks:
        if len(block.text) <= limit:
            units.append(block)
        elif block.kind == "table":
            units.extend(Block("table", t) for t in _split_table(block.text, limit))
        else:
            units.extend(Block("para", t) for t in _split_long_text(block.text, limit))
    return units


def _pack_blocks(blocks: list[Block], limit: int) -> list[tuple[str, bool]]:
    """Return (piece, contains_table). A table is never mixed into overlapping prose."""
    pieces: list[tuple[str, bool]] = []
    cur = ""
    has_table = False
    for unit in _unitize(blocks, limit):
        candidate = f"{cur}\n\n{unit.text}" if cur else unit.text
        if len(candidate) > limit and cur:
            pieces.append((cur, has_table))
            cur = unit.text
            has_table = unit.kind == "table"
        else:
            cur = candidate
            has_table = has_table or unit.kind == "table"
    if cur:
        pieces.append((cur, has_table))
    return pieces


def merge_small_sections(sections: list[Section], min_chars: int) -> list[Section]:
    """Retained for API compatibility. NEVER merge across headings/programs."""
    return sections


def chunk_markdown(md: str, target: int = 1600, max_chars: int = 2800,
                   min_chars: int = 200, overlap_chars: int = 120) -> list[Chunk]:
    if target <= 0 or max_chars <= 0 or min_chars < 0 or overlap_chars < 0:
        raise ValueError("chunk sizes and overlap must be non-negative and target/max positive")
    chunks: list[Chunk] = []
    for section in parse_sections(md):
        header = _header(section.heading_path)
        limit = min(target, max_chars) - len(header)
        if limit <= 0:
            raise ValueError("heading path is longer than chunk cap")
        parts = _pack_blocks(section.blocks, limit)
        previous_text, previous_had_table = "", False
        for piece, had_table in parts:
            content = piece
            # Only overlap prose within one heading path; tables must not be duplicated.
            if previous_text and not previous_had_table and not had_table and overlap_chars:
                budget = min(overlap_chars, max(0, limit - len(piece) - 2))
                if budget >= 15:
                    tail = previous_text[-budget:]
                    if " " in tail:
                        tail = tail.split(" ", 1)[-1]
                    if tail:
                        content = tail + "\n\n" + piece
            text = header + content
            if len(text) > max_chars:
                raise ValueError("generated chunk exceeds hard cap")
            chunks.append(Chunk(order=len(chunks), heading_path=section.heading_path.copy(), text=text,
                                chars=len(text), sha256=hashlib.sha256(text.encode("utf-8")).hexdigest()))
            previous_text, previous_had_table = piece, had_table
    return chunks
