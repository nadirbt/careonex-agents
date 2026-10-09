"""Load and render program cards for grounding the dialog agent.

Reads Markdown files with a YAML-ish frontmatter block from
knowledge/program_cards/ (see knowledge/README.md for the schema each card
must follow) and exposes them either as structured records (for a lookup
tool) or as a single rendered block of text (for a system prompt).

No embeddings, no vector store, no external service: this intentionally
keeps grounding as simple as the corpus size (8-10 programs) allows. See
knowledge/README.md, "Why cards, not raw pages," for the reasoning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REQUIRED_FRONTMATTER_FIELDS = (
    "id",
    "program",
    "category",
    "publisher",
    "source_url",
    "effective_date",
    "fetch_date",
    "review_status",
)

VALID_REVIEW_STATUSES = ("draft-needs-verification", "verified")

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


class CardParseError(ValueError):
    """Raised when a program card file does not match the required schema."""


@dataclass(frozen=True)
class ProgramCard:
    id: str
    program: str
    category: str
    publisher: str
    source_url: str
    effective_date: str
    fetch_date: str
    review_status: str
    body: str
    path: Path

    @property
    def is_verified(self) -> bool:
        return self.review_status == "verified"


def _parse_frontmatter(text: str, path: Path) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise CardParseError(f"{path}: missing '---' frontmatter block")

    raw_frontmatter, body = match.group(1), match.group(2)
    fields: dict[str, str] = {}
    for line_no, line in enumerate(raw_frontmatter.splitlines(), start=1):
        if not line.strip():
            continue
        if ":" not in line:
            raise CardParseError(
                f"{path}: frontmatter line {line_no} is not 'key: value': {line!r}"
            )
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()

    missing = [f for f in REQUIRED_FRONTMATTER_FIELDS if f not in fields]
    if missing:
        raise CardParseError(f"{path}: missing frontmatter field(s): {missing}")

    if fields["review_status"] not in VALID_REVIEW_STATUSES:
        raise CardParseError(
            f"{path}: review_status must be one of {VALID_REVIEW_STATUSES}, "
            f"got {fields['review_status']!r}"
        )

    return fields, body.strip()


def load_card(path: Path) -> ProgramCard:
    text = path.read_text(encoding="utf-8")
    fields, body = _parse_frontmatter(text, path)
    return ProgramCard(
        id=fields["id"],
        program=fields["program"],
        category=fields["category"],
        publisher=fields["publisher"],
        source_url=fields["source_url"],
        effective_date=fields["effective_date"],
        fetch_date=fields["fetch_date"],
        review_status=fields["review_status"],
        body=body,
        path=path,
    )


def load_cards(cards_dir: Path | None = None) -> list[ProgramCard]:
    """Load every *.md card in cards_dir, sorted by id for stable ordering."""
    if cards_dir is None:
        cards_dir = Path(__file__).resolve().parent.parent / "knowledge" / "program_cards"
    cards = [load_card(p) for p in sorted(cards_dir.glob("*.md"))]
    ids = [c.id for c in cards]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise CardParseError(f"Duplicate card id(s) found: {duplicates}")
    return sorted(cards, key=lambda c: c.id)


def get_card(program_id: str, cards: list[ProgramCard] | None = None) -> ProgramCard | None:
    """Look up a single card by id -- the shape a Sonic tool call would use."""
    cards = cards if cards is not None else load_cards()
    for card in cards:
        if card.id == program_id:
            return card
    return None


def render_grounding_block(cards: list[ProgramCard] | None = None, *, include_unverified: bool = True) -> str:
    """Render all cards into one Markdown block suitable for a system prompt.

    Set include_unverified=False to exclude cards still marked
    'draft-needs-verification' -- useful once verification is underway and
    you want the live agent to only ground on confirmed facts.
    """
    cards = cards if cards is not None else load_cards()
    if not include_unverified:
        cards = [c for c in cards if c.is_verified]

    sections = []
    for card in cards:
        sections.append(
            f"### {card.program}\n"
            f"(source: {card.source_url} | publisher: {card.publisher} | "
            f"effective: {card.effective_date} | fetched: {card.fetch_date})\n\n"
            f"{card.body}"
        )
    return "\n\n---\n\n".join(sections)
