import unittest
from pathlib import Path

from nova_sonic.knowledge import (
    REQUIRED_FRONTMATTER_FIELDS,
    CardParseError,
    get_card,
    load_card,
    load_cards,
    render_grounding_block,
)

CARDS_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "program_cards"


class ProgramCardSchemaTests(unittest.TestCase):
    """Every card in knowledge/program_cards/ must satisfy this contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cards = load_cards(CARDS_DIR)

    def test_at_least_the_expected_programs_are_present(self):
        # SOW 2a names five categories of source; we expect at least one
        # card per category plus the two consumer-protection cards.
        ids = {c.id for c in self.cards}
        expected_minimum = {
            "nj-mltss",
            "nj-jacc",
            "medicare-home-health",
            "va-home-care-benefits",
            "nj-hcsf-consumer-guide",
        }
        self.assertTrue(expected_minimum.issubset(ids), ids)

    def test_no_duplicate_ids(self):
        ids = [c.id for c in self.cards]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_card_has_all_required_fields_nonempty(self):
        for card in self.cards:
            for field in REQUIRED_FRONTMATTER_FIELDS:
                value = getattr(card, field)
                self.assertTrue(value, f"{card.path}: {field} is empty")

    def test_every_card_has_a_source_url_that_looks_like_a_url(self):
        for card in self.cards:
            self.assertTrue(
                card.source_url.startswith("http"),
                f"{card.path}: source_url {card.source_url!r} doesn't look like a URL",
            )

    def test_every_card_body_has_a_spoken_answer_section(self):
        # Stage 4 ("Answering (RAG)") requires a short spoken answer, not
        # just reference material -- enforce the section exists.
        for card in self.cards:
            self.assertIn(
                "What to say on the call",
                card.body,
                f"{card.path}: missing 'What to say on the call' section",
            )

    def test_every_card_has_a_sources_section(self):
        for card in self.cards:
            self.assertIn("## Sources", card.body, f"{card.path}: missing Sources section")

    def test_review_status_is_a_known_value(self):
        for card in self.cards:
            self.assertIn(card.review_status, ("draft-needs-verification", "verified"))


class LoaderBehaviorTests(unittest.TestCase):
    def test_get_card_returns_none_for_unknown_id(self):
        self.assertIsNone(get_card("not-a-real-program", cards=load_cards(CARDS_DIR)))

    def test_get_card_returns_matching_card(self):
        cards = load_cards(CARDS_DIR)
        card = get_card("nj-mltss", cards=cards)
        self.assertIsNotNone(card)
        self.assertEqual(card.program, "NJ FamilyCare Medicaid Managed Long Term Services and Supports (MLTSS)")

    def test_render_grounding_block_includes_every_card_source_url(self):
        cards = load_cards(CARDS_DIR)
        block = render_grounding_block(cards)
        for card in cards:
            self.assertIn(card.source_url, block)

    def test_render_grounding_block_can_exclude_unverified_cards(self):
        cards = load_cards(CARDS_DIR)
        # All current cards are draft-needs-verification, so excluding
        # unverified cards should currently yield an empty block.
        block = render_grounding_block(cards, include_unverified=False)
        self.assertEqual(block, "")

    def test_default_system_prompt_is_not_silently_ungrounded(self):
        # Guards against the failure mode where a broken card (bad
        # frontmatter, parse error) makes config._build_default_system_prompt
        # swallow the exception and ship a system prompt with *no* program
        # grounding at all -- the agent would still run, just answer every
        # eligibility/payment question with nothing but the base persona.
        # If this test fails, something in knowledge/program_cards/ broke;
        # go find out what before anyone talks to this prompt.
        from nova_sonic.config import DEFAULT_SYSTEM_PROMPT

        cards = load_cards(CARDS_DIR)
        self.assertGreater(len(cards), 0, "no cards loaded -- is CARDS_DIR right?")
        for card in cards:
            self.assertIn(
                card.source_url,
                DEFAULT_SYSTEM_PROMPT,
                f"{card.id} is missing from the live system prompt -- grounding may "
                "have silently failed (see config._build_default_system_prompt)",
            )

    def test_default_system_prompt_intentionally_includes_unverified_cards(self):
        # Policy decision (as of now): we test with the full corpus, including
        # draft-needs-verification cards, because no one has done the expert
        # fact-check pass yet and we need realistic answers to test against.
        # This must change to include_unverified=False before any card is
        # used on a live call with a real caller -- see knowledge/README.md,
        # "review_status". If this test starts failing because a card flips
        # to "verified", that's fine; update the assertion, don't just delete it.
        cards = load_cards(CARDS_DIR)
        self.assertTrue(
            all(not c.is_verified for c in cards),
            "a card is now verified -- re-check whether config.py's "
            "include_unverified=True is still the right call before launch",
        )

    def test_grounding_block_stays_within_the_no_rag_token_budget(self):
        # The whole "cards instead of a vector store" design (see
        # knowledge/README.md, "Why cards, not raw pages") rests on the
        # corpus being small enough to hand to the model directly -- roughly
        # 6-10k tokens as of writing. This is a regression test for that
        # assumption: it doesn't check correctness, just that nobody added
        # enough cards to quietly blow the budget without anyone noticing.
        # If this fails because the corpus grew, that's a real decision
        # point: raise the budget deliberately, or revisit vector RAG.
        import tiktoken

        cards = load_cards(CARDS_DIR)
        block = render_grounding_block(cards, include_unverified=True)
        enc = tiktoken.get_encoding("cl100k_base")
        token_count = len(enc.encode(block))
        self.assertLess(
            token_count,
            12000,
            f"grounding block is {token_count} tokens -- that's past the "
            "'small enough for a system prompt' budget this design assumes; "
            "see knowledge/README.md 'Why cards, not raw pages'",
        )

    def test_malformed_card_missing_frontmatter_raises(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "bad.md"
            bad_path.write_text("no frontmatter here\n", encoding="utf-8")
            with self.assertRaises(CardParseError):
                load_card(bad_path)

    def test_duplicate_ids_across_files_raises(self):
        import tempfile

        card_text = (
            "---\n"
            "id: dup\n"
            "program: X\n"
            "category: x\n"
            "publisher: X\n"
            "source_url: https://example.com\n"
            "effective_date: 2026-01-01\n"
            "fetch_date: 2026-01-01\n"
            "review_status: draft-needs-verification\n"
            "---\n"
            "## What to say on the call\n\nHi.\n\n## Sources\n\n- https://example.com\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.md").write_text(card_text, encoding="utf-8")
            (Path(tmp) / "b.md").write_text(card_text, encoding="utf-8")
            with self.assertRaises(CardParseError):
                load_cards(Path(tmp))


if __name__ == "__main__":
    unittest.main()
