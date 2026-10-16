"""Structure-first hierarchical chunking for OFFLINE STAGING EXPERIMENTS.

One section can contain several bounded topical parents. Each parent's children
are smaller, context-bearing search units. Parent texts are stored separately;
retrieval must explicitly fetch the parent before using it as expanded context.
No LLM is used at indexing time. No live KB is modified by this module.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .chunker import Block, Chunk, _header, _pack_blocks, parse_sections

# Generic topical labels, not fixed to any government program.
_TOPIC = re.compile(
    r"^\s*(?:\*\*)?(eligibility|requirements|qualifications|services|benefits|"
    r"application|how to apply|enrollment|coverage|costs?|fees?|payment|"
    r"contact|appeals?|documents?|assessment|income|assets?|resources|"
    r"exceptions?|limitations?|caregiver support)(?:\*\*)?\s*[:.\-–]\s*",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class Parent:
    parent_id: str
    heading_path: tuple[str, ...]
    topic: str
    text: str
    chars: int


@dataclass(frozen=True)
class Child:
    chunk: Chunk
    parent_id: str
    topic: str


@dataclass
class Hierarchy:
    parents: list[Parent]
    children: list[Child]


def _topic(text: str) -> str | None:
    match = _TOPIC.match(text.strip())
    return match.group(1).strip().lower() if match else None


def _flush_parent(path: list[str], topic: str, blocks: list[Block], parent_limit: int,
                  parents: list[Parent], children: list[Child], child_target: int,
                  overlap_chars: int) -> None:
    if not blocks:
        return
    head = _header(path)
    # Each parent contains one coherent, bounded group of blocks.
    content = "\n\n".join(b.text for b in blocks)
    parent_text = head + (f"Topic: {topic}\n\n" if topic else "") + content
    if len(parent_text) > parent_limit:
        raise ValueError("hierarchical parent exceeds configured character limit")
    digest = hashlib.sha256((str(len(parents)) + "\0" + parent_text).encode("utf-8")).hexdigest()
    parent_id = digest[:20]
    parents.append(Parent(parent_id, tuple(path), topic, parent_text, len(parent_text)))

    # Don't combine tables and prose within a search child; table context stays intact.
    context = head + (f"Topic: {topic}\n\n" if topic else "")
    budget = min(child_target, parent_limit) - len(context)
    if budget < 40:
        raise ValueError("heading and topic leave no room for child content")
    last_prose = ""
    for piece, had_table in _pack_blocks(blocks, budget):
        actual = piece
        if not had_table and last_prose and overlap_chars:
            space = min(overlap_chars, budget - len(piece) - 2)
            if space >= 15:
                tail = last_prose[-space:]
                if " " in tail:
                    tail = tail.split(" ", 1)[-1]
                if tail:
                    actual = tail + "\n\n" + piece
        text = context + actual
        order = len(children)
        chunk = Chunk(order=order, heading_path=list(path), text=text,
                      chars=len(text), sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
        children.append(Child(chunk, parent_id, topic))
        last_prose = piece if not had_table else ""


def hierarchical_chunk_markdown(md: str, *, parent_max_chars: int = 2400,
                                child_target_chars: int = 950,
                                overlap_chars: int = 80) -> Hierarchy:
    """Make independently searchable children and linked, non-indexed parents.

    Parent boundary: Markdown section; additionally a labelled topical paragraph,
    table, or configured parent length can begin a fresh parent. Every parent
    is constrained to max length. Children are narrower context-bearing units.
    """
    if parent_max_chars < 160 or child_target_chars < 80 or overlap_chars < 0:
        raise ValueError("invalid hierarchical chunk size/overlap")
    if child_target_chars > parent_max_chars:
        raise ValueError("child target cannot exceed parent maximum")
    parents: list[Parent] = []
    children: list[Child] = []
    for section in parse_sections(md):
        path = section.heading_path
        header = _header(path)
        parent_budget = parent_max_chars - len(header) - 80  # headroom for topical label
        if parent_budget < 80:
            raise ValueError("hierarchical section heading exceeds parent budget")
        blocks: list[Block] = []
        active_topic = ""
        size = 0

        def flush():
            nonlocal blocks, size
            _flush_parent(path, active_topic, blocks, parent_max_chars, parents, children,
                          child_target_chars, overlap_chars)
            blocks, size = [], 0

        for source_block in section.blocks:
            # Split paragraphs into bounded, coherent blocks using the same
            # loss-preserving sentence/word fallback as section-aware chunking.
            if source_block.kind == "table":
                from .chunker import _split_table
                # Tables must fit the *child* retrieval budget as well as the
                # parent. Split once here, before creating labelled wide-row
                # records, so child packing never tries to parse those records
                # as a second, unrelated Markdown table.
                table_budget = min(parent_budget, child_target_chars - len(header) - 80)
                if table_budget < 80:
                    raise ValueError("heading leaves insufficient room for table labels")
                pieces = (_split_table(source_block.text, table_budget)
                          if len(source_block.text) > table_budget else [source_block.text])
            else:
                from .chunker import _split_long_text
                pieces = (_split_long_text(source_block.text, parent_budget)
                          if len(source_block.text) > parent_budget else [source_block.text])
            for part in pieces:
                block = Block(source_block.kind, part)
                labelled = _topic(part) if block.kind != "table" else None
                if blocks and (block.kind == "table" or any(b.kind == "table" for b in blocks)
                               or (labelled and labelled != active_topic)
                               or size + len(part) + 2 > parent_budget):
                    flush()
                if labelled:
                    active_topic = labelled
                if len(part) > parent_budget:
                    raise ValueError("single hierarchical block exceeds parent budget")
                blocks.append(block)
                size += len(part) + 2
                if block.kind == "table":
                    flush()
        flush()
    return Hierarchy(parents, children)
