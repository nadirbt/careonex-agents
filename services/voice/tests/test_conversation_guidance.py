"""Offline regressions for the conversational contract; behavior also needs a live Sonic trial.

These checks guard against accidentally re-introducing a long sales-pitch prompt,
broad age-based lookups, or a multi-question intake script.
"""

import json

from nova_sonic.config import DEFAULT_SYSTEM_PROMPT
from nova_sonic.events import session_start
from nova_sonic.tools import INTAKE_NEXT_QUESTION, LOOKUP_PROGRAM_INFO, intake_next_question_sync


def test_greeting_responds_to_request_instead_of_volunteering_programs():
    prompt = DEFAULT_SYSTEM_PROMPT
    assert "Do not introduce Medicaid, Medicare, JACC" in prompt
    assert "looking for some help for my mother" in prompt
    assert "What kind of help does she need?" in prompt
    assert "ONE question per turn" in prompt
    assert "under 35 spoken words" in prompt


def test_yes_to_intake_offer_has_priority_over_program_explanation():
    prompt = DEFAULT_SYSTEM_PROMPT
    assert "'yeah sure'" in prompt
    assert "immediately begin the" in prompt
    assert "brief intake using intake_next_question" in prompt
    assert "don't repeat the last program explanation" in prompt
    assert "save_intake succeeds" in prompt


def test_retrieval_is_on_demand_not_a_sales_pitch():
    prompt = DEFAULT_SYSTEM_PROMPT
    lookup = LOOKUP_PROGRAM_INFO["toolSpec"]
    assert "specific factual question relevant to home care" in prompt
    assert "Do NOT call retrieval for greetings" in prompt
    assert "the retrieve service itself handles optional query expansion" in prompt
    assert "not just government programs" in lookup["description"]
    assert "never for greetings" in lookup["description"]
    assert "search unfinished speech" in json.loads(lookup["inputSchema"]["json"])["properties"]["query"]["description"]


def test_intake_questions_are_single_and_skip_earlier_information():
    known = {"relationship": "daughter", "care_recipient_age": "78", "county": "Monmouth", "kind_of_help": "bathing and dressing"}
    item = intake_next_question_sync(known)
    assert item["field"] == "timeline"
    assert item["ask"].count("?") == 1
    assert intake_next_question_sync({})["ask"] == "What kind of help does your family need most?"
    assert "agrees to have details recorded" in INTAKE_NEXT_QUESTION["toolSpec"]["description"]


def test_spoken_generation_budget_is_bounded():
    config = json.loads(session_start())["event"]["sessionStart"]["inferenceConfiguration"]
    assert config["maxTokens"] == 256  # concise spoken-response default; overridable by env
