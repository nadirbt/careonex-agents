"""Voice-only regressions for real spoken-caller feedback."""
import asyncio
import json

from nova_sonic.config import DEFAULT_SYSTEM_PROMPT
from nova_sonic.tools import INTAKE_ORDER, explicit_language_request, intake_next_question_sync
from nova_sonic.events import session_start
from nova_sonic.session import NovaSonicSession


def test_short_spoken_turn_contract():
    text = DEFAULT_SYSTEM_PROMPT
    for key in ("TOP PRIORITY", "ONE question per turn", "under 35 spoken words", "one at a time", "too many questions", "ANSWER INTERRUPTIONS", "DO NOT begin an unfinished phrase"):
        assert key in text
    assert "ELIGIBILITY QUESTIONS ARE NOT INTAKE CONSENT" in text
    assert "do not automatically restart" in text
    assert "Never invent a supported-language list" in text


def test_minimal_callback_flow_skips_unneeded_assessment():
    assert [key for key, _ in INTAKE_ORDER] == ["kind_of_help", "county", "relationship", "timeline", "caller_name", "callback_phone"]
    assert intake_next_question_sync({})["field"] == "kind_of_help"
    assert intake_next_question_sync({"kind_of_help":"bathing", "county":"Middlesex"})["field"] == "relationship"
    assert intake_next_question_sync({"kind_of_help":"bathing", "county":"not sure", "caller_name":"Michael"})["field"] == "relationship"
    result = intake_next_question_sync({"kind_of_help":"bathing", "county":"Middlesex", "relationship":"son", "timeline":"within a month", "caller_name":"Michael", "callback_phone":"1234567890"})
    assert result["complete"] and "WAIT" in result["guidance"]


def test_language_reply_requires_context_not_arbitrary_mentions():
    assert not explicit_language_request("西伯来语啊", "Hebrew")
    assert explicit_language_request("西伯来语啊", "Hebrew", "请告诉我你希望用哪种语言继续对话")
    assert explicit_language_request("希伯来语", "Hebrew", "Which language would you prefer?")
    assert not explicit_language_request("My document mentions Hebrew.", "Hebrew")
    assert explicit_language_request("请用希伯来语和我说话", "Hebrew")
    assert explicit_language_request("Could you speak Arabic?", "Arabic")
    assert not explicit_language_request("你好", "Spanish")


def test_latest_assistant_turn_available_for_language_context():
    session=NovaSonicSession()
    session.transcripts=[("ASSISTANT","Tell me your county."),("USER","Not sure."),("ASSISTANT","Which language would you prefer?"),("USER","Hebrew")]
    assert session._latest_assistant_utterance()=="Which language would you prefer?"
    assert explicit_language_request(session._latest_user_utterance(), "Hebrew",session._latest_assistant_utterance())


def test_voice_token_cap_configurable(monkeypatch):
    monkeypatch.setenv("NOVA_SONIC_MAX_TOKENS", "384")
    assert json.loads(session_start())["event"]["sessionStart"]["inferenceConfiguration"]["maxTokens"] == 384
