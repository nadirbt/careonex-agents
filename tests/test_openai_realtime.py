import unittest


class PromptParityTests(unittest.TestCase):
    """openai_realtime/ is meant to reuse the exact same system prompt as
    nova_sonic/, so prompt-design work (persona, hard rules, program-card
    grounding) is testable once and compared fairly across models. If this
    test fails, someone edited one branch's prompt without the other --
    which may be intentional (see openai_realtime/config.py's note on
    model-specific instruction quirks), but should be a deliberate choice,
    not a silent drift."""

    def test_openai_branch_reuses_nova_system_prompt_unmodified(self):
        from nova_sonic.config import DEFAULT_SYSTEM_PROMPT
        from openai_realtime.config import SYSTEM_PROMPT

        self.assertEqual(SYSTEM_PROMPT, DEFAULT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
