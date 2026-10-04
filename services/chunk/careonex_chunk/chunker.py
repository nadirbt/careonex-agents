"""Section-aware Markdown chunking. Pure functions, no AWS.

Rules, in order of priority:
1. A heading starts a new section; the full heading path (H1 > H2 > H3) is prepended
   to every chunk so "Income limit: $4,855" never travels without "JACC".
2. A Markdown table is never split: its rows stay together, and the header row is
   repeated if a table must be broken because it alone exceeds the cap.
3. Sections longer than MAX_CHARS are split at paragraph boundaries, aiming for TARGET_CHARS.
4. Sections shorter than MIN_CHARS are merged into the following section of the same parent.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
CHUNKER_VERSION = "1"


@dataclass
class Block:
    kind: str  # "para" | "table"
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
    """Split Markdown into sections at headings, grouping paragraphs and tables into blocks."""
    stack: list[tuple[int, str]] = []
    sections: list[Section] = [Section(heading_path=[])]
    para: list[str] = []
    table: list[str] = []

    def flush_para():
        nonlocal para
        if para:
            text = "\n".join(para).strip()
            if text:
                sections[-1].blocks.append(Block("para", text))
            para = []

    def flush_table():
        nonlocal table
        if table:
            sections[-1].blocks.append(Block("table", "\n".join(table).strip()))
            table = []

    for line in md.splitlines():
        m = HEADING_RE.match(line)
        if m:
            flush_para()
            flush_table()
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            sections.append(Section(heading_path=[t for _, t in stack]))
            continue
        if TABLE_ROW_RE.match(line):
            flush_para()
            table.append(line.rstrip())
            continue
        if table:
            flush_table()
        if not line.strip():
            flush_para()
            continue
        para.append(line.rstrip())
    flush_para()
    flush_table()
    return [s for s in sections if s.blocks]


def _header(heading_path: list[str]) -> str:
    return (" > ".join(heading_path) + "\n\n") if heading_path else ""


def _split_long_text(text: str, target: int) -> list[str]:
    """Split a single oversized paragraph at sentence boundaries."""
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if cur and len(cur) + 1 + len(sent) > target:
            parts.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        parts.append(cur)
    return parts


def _split_table(table: str, target: int) -> list[str]:
    rows = table.split("\n")
    header = rows[:2] if len(rows) >= 2 and set(rows[1].replace("|", "").strip()) <= set("-: ") else rows[:1]
    body = rows[len(header):]
    out, cur = [], list(header)
    for r in body:
        if sum(len(x) + 1 for x in cur) + len(r) > target and len(cur) > len(header):
            out.append("\n".join(cur))
            cur = list(header)
        cur.append(r)
    if len(cur) > len(header) or not out:
        out.append("\n".join(cur))
    return out


def _pack_blocks(blocks: list[Block], target: int, max_chars: int) -> list[str]:
    """Greedy packing of blocks into pieces no longer than max_chars, aiming at target."""
    pieces, cur = [], ""
    units: list[str] = []
    for b in blocks:
        if len(b.text) <= max_chars:
            units.append(b.text)
        elif b.kind == "table":
            units.extend(_split_table(b.text, target))
        else:
            units.extend(_split_long_text(b.text, target))
    for u in units:
        if cur and len(cur) + 2 + len(u) > target:
            pieces.append(cur)
            cur = u
        else:
            cur = f"{cur}\n\n{u}".strip()
    if cur:
        pieces.append(cur)
    return pieces


def merge_small_sections(sections: list[Section], min_chars: int) -> list[Section]:
    """Fold sections shorter than min_chars into the next section (keeping their heading as text),
    or into the previous one when they come last."""
    out: list[Section] = []
    carry: list[Block] = []
    for s in sections:
        blocks = carry + s.blocks
        carry = []
        merged = Section(heading_path=s.heading_path, blocks=blocks)
        if merged.chars < min_chars:
            # Keep the small section's own heading visible inside the text it is folded into.
            title = " > ".join(s.heading_path)
            carry = ([Block("para", f"**{title}**")] if title else []) + blocks
            continue
        out.append(merged)
    if carry:
        if out:
            out[-1].blocks.extend(carry)
        else:
            out.append(Section(heading_path=[], blocks=carry))
    return out


def chunk_markdown(md: str, target: int = 1600, max_chars: int = 2800, min_chars: int = 200) -> list[Chunk]:
    sections = merge_small_sections(parse_sections(md), min_chars)
    chunks: list[Chunk] = []
    order = 0
    for s in sections:
        header = _header(s.heading_path)
        for piece in _pack_blocks(s.blocks, target - len(header), max_chars):
            text = header + piece
            chunks.append(Chunk(order=order, heading_path=list(s.heading_path), text=text, chars=len(text), sha256=hashlib.sha256(text.encode("utf-8")).hexdigest()))
            order += 1
    return chunks
