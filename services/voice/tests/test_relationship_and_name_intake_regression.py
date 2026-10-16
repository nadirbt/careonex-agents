"""Regressions from the live call: avoid repeating relationship, collect caller name.

No AWS calls; each save is written to pytest's temporary directory.
"""
import asyncio
import json

import nova_sonic.tools as tools_mod
from nova_sonic.session import (
    NovaSonicSession,
    _caller_name_stated_by_caller,
    _relationship_stated_by_caller,
)


class FakeInput:
    async def send(self, chunk):
        return None


class FakeStream:
    input_stream = FakeInput()


async def call(session, tool, payload, key):
    await session._start_tool({
        "toolName": tool,
        "toolUseId": key,
        "content": json.dumps(payload),
    })
    return json.loads(session.tool_calls[-1]["output"])


def test_short_live_relationship_reply_is_valid_only_after_direct_question():
    direct = [
        ("USER", "My mother needs dressing and bathing care."),
        ("ASSISTANT", "Could you tell me your relationship to your mother?"),
        ("USER", "son and mother"),
    ]
    assert _relationship_stated_by_caller("son", direct)
    assert _relationship_stated_by_caller("son", [
        ("ASSISTANT", "How are you related to her?"),
        ("USER", "mother and son"),
    ])
    assert not _relationship_stated_by_caller("daughter", direct)
    assert not _relationship_stated_by_caller("son", [("USER", "Son and mother")])
    assert not _relationship_stated_by_caller("daughter", [("USER", "My mom needs help")])


def test_short_relationship_answer_does_not_reask(monkeypatch):
    async def case():
        s = NovaSonicSession()
        s.stream = FakeStream()
        s.transcripts = [
            ("USER", "My mother needs dressing help in Hudson County."),
            ("ASSISTANT", "What's your relationship to your mother?"),
            ("USER", "son and mother"),
        ]
        return await call(s, "intake_next_question", {
            "kind_of_help": "dressing", "county": "Hudson", "relationship": "son",
        }, "relationship")

    r = asyncio.run(case())
    assert r["field"] == "timeline", r


def test_name_must_be_caller_provided():
    assert _caller_name_stated_by_caller("Marcus", [("USER", "My name is Marcus")])
    assert _caller_name_stated_by_caller("Marcus", [
        ("ASSISTANT", "What name should the coordinator ask for?"),
        ("USER", "Marcus"),
    ])
    assert not _caller_name_stated_by_caller("Marcus", [("USER", "My mother is Marcus")])
    assert not _caller_name_stated_by_caller("Marcus", [("USER", "My mom needs help")])
    assert not _caller_name_stated_by_caller("Marcus", [("USER", "Her caregiver is Marcus")])


def test_missing_name_blocks_save_even_if_model_supplies_one(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_mod, "INTAKE_DIR", str(tmp_path / "intakes"))

    async def case():
        s = NovaSonicSession()
        s.stream = FakeStream()
        s.transcripts = [
            ("USER", "My mom needs help bathing in Hudson County."),
            ("ASSISTANT", "What's your relationship to your mother?"),
            ("USER", "son and mother"),
            ("ASSISTANT", "When would you like care to start?"),
            ("USER", "Tomorrow"),
            ("ASSISTANT", "Is 1234567890 the correct callback number?"),
            ("USER", "Yes"),
        ]
        payload = {
            "kind_of_help": "bathing", "relationship": "son", "county": "Hudson",
            "timeline": "tomorrow", "caller_name": "Marcus", "callback_phone": "1234567890",
        }
        return await call(s, "save_intake", payload, "too-early")

    result = asyncio.run(case())
    assert result["saved"] is False
    assert result["missing_field"] == "caller_name"
    assert not list(tmp_path.rglob("*.json"))


def test_name_question_then_successful_file_write(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_mod, "INTAKE_DIR", str(tmp_path / "intakes"))

    async def case():
        s = NovaSonicSession()
        s.stream = FakeStream()
        s.transcripts = [
            ("USER", "My mom needs help bathing in Hudson County."),
            ("ASSISTANT", "What's your relationship to your mother?"),
            ("USER", "son and mother"),
            ("ASSISTANT", "When would you like care to start?"),
            ("USER", "Tomorrow"),
            ("ASSISTANT", "What name should the coordinator ask for?"),
            ("USER", "Marcus"),
            ("ASSISTANT", "Is your callback number one two three four five six seven eight nine zero correct?"),
            ("USER", "yes"),
        ]
        payload = {
            "kind_of_help": "bathing", "relationship": "son", "county": "Hudson",
            "timeline": "tomorrow", "caller_name": "Marcus", "callback_phone": "1234567890",
        }
        return await call(s, "save_intake", payload, "valid")

    result = asyncio.run(case())
    assert result["saved"] is True
    files = list((tmp_path / "intakes").glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text())
    assert saved["relationship"] == "son"
    assert saved["caller_name"] == "Marcus"
    assert saved["timeline"] == "tomorrow"
    assert saved["county"] == "Hudson"


def test_caller_may_decline_to_provide_name(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_mod, "INTAKE_DIR", str(tmp_path / "intakes"))

    async def case():
        s = NovaSonicSession()
        s.stream = FakeStream()
        s.transcripts = [
            ("ASSISTANT", "How are you related to her?"),
            ("USER", "I am her son"),
            ("ASSISTANT", "When would you like care to start?"),
            ("USER", "Next week"),
            ("ASSISTANT", "What name should the coordinator ask for?"),
            ("USER", "Prefer not to say"),
            ("ASSISTANT", "Is 1234567890 the correct callback number?"),
            ("USER", "Yes"),
        ]
        return await call(s, "save_intake", {
            "kind_of_help": "dressing", "county": "Hudson", "relationship": "son",
            "timeline": "next week", "callback_phone": "1234567890",
        }, "declined")

    result = asyncio.run(case())
    assert result["saved"] is True
    f, = list((tmp_path / "intakes").glob("*.json"))
    assert "caller_name" not in json.loads(f.read_text())
