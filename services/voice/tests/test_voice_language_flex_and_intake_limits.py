"""Regression tests for live-caller failures; no AWS calls."""
import asyncio
import json

from nova_sonic.config import DEFAULT_SYSTEM_PROMPT
from nova_sonic.tools import (INTAKE_NEXT_QUESTION, explicit_language_request,
    intake_next_question_sync, save_intake_sync, set_conversation_language_sync)


def test_no_language_whitelist_or_mandarin_ban():
    prompt = DEFAULT_SYSTEM_PROMPT.lower()
    assert "do not restrict the caller" in prompt
    assert "including mandarin chinese" in prompt
    for name in ("Mandarin", "Cantonese", "Japanese", "Arabic", "Vietnamese", "Haitian Creole"):
        result = set_conversation_language_sync({"language": name})
        assert result["changed"] is True
        assert "not supported" not in result["guidance"].lower()
        assert "best effort" in result["guidance"].lower()
    assert set_conversation_language_sync({"language": "普通话"})["language"] == "Mandarin Chinese"
    assert explicit_language_request("请用中文回答", "Mandarin")
    assert explicit_language_request("Could you speak Arabic?", "Arabic")
    assert not explicit_language_request("你好", "Spanish")
    assert not explicit_language_request("こんにちは", "Japanese")


def test_clarify_county_without_advancing_intake():
    spec = json.loads(INTAKE_NEXT_QUESTION["toolSpec"]["inputSchema"]["json"])
    assert "help_topic" in spec["properties"]
    out = intake_next_question_sync({"relationship": "son", "county": "not sure", "help_topic": "county"})
    assert out["clarification"] is True and out["field"] == "county"
    assert "Middlesex" in out["ask"] and "town" in out["ask"]
    assert "repeat" not in out["guidance"].lower() or "do not repeat" in out["guidance"].lower()


def test_clarify_hours_without_demanding_exact_number():
    out = intake_next_question_sync({"help_topic": "hours_per_week"})
    assert out["clarification"] is True
    assert "optional" in out["ask"]


def test_impossible_hours_must_be_clarified_not_saved(tmp_path, monkeypatch):
    import nova_sonic.tools as tools
    monkeypatch.setattr(tools, "INTAKE_DIR", str(tmp_path / "intakes"))
    for value in ("200", "200 hours", "200 per week", "0", "169"):
        response = intake_next_question_sync({"hours_per_week": value, "county": "Middlesex"})
        assert response["validation_required"] is True and response["field"] == "hours_per_week"
        failed = save_intake_sync({"callback_phone": "2125550123", "county": "Middlesex", "hours_per_week": value})
        assert failed["saved"] is False and failed["error"] == "invalid hours_per_week"
    assert not (tmp_path / "intakes").exists()
    assert intake_next_question_sync({"hours_per_week": "24", "county": "Middlesex"})["field"] != "hours_per_week"
    assert intake_next_question_sync({"hours_per_week": "caregiver lives in the home", "county": "Middlesex"})["field"] != "hours_per_week"


def test_eligibility_is_not_consent():
    assert "ELIGIBILITY QUESTIONS ARE NOT INTAKE CONSENT" in DEFAULT_SYSTEM_PROMPT
    assert "answer THAT question first" in DEFAULT_SYSTEM_PROMPT
    assert "Do not say 'I didn't catch that'" in DEFAULT_SYSTEM_PROMPT
    assert "not that a coordinator was contacted" in DEFAULT_SYSTEM_PROMPT
