"""Only the caller can establish their relationship; timing is asked explicitly."""
import asyncio
import json

import nova_sonic.session as session_mod
from nova_sonic.session import NovaSonicSession, _relationship_stated_by_caller
from nova_sonic.tools import INTAKE_ORDER, intake_next_question_sync


class FakeInput:
    async def send(self, chunk):
        return None


class FakeStream:
    input_stream = FakeInput()


def test_relationship_is_asked_and_start_time_follows_one_question_at_a_time():
    assert [name for name, _ in INTAKE_ORDER] == [
        "kind_of_help", "county", "relationship", "timeline", "caller_name", "callback_phone"
    ]
    facts = {"kind_of_help": "bathing and dressing", "county": "Hudson"}
    question = intake_next_question_sync(facts)
    assert question["field"] == "relationship" and question["ask"].count("?") == 1
    facts["relationship"] = "son"
    question = intake_next_question_sync(facts)
    assert question["field"] == "timeline" and "start" in question["ask"].lower()
    facts["timeline"] = "within a month"
    assert intake_next_question_sync(facts)["field"] == "caller_name"


def test_my_mom_does_not_prove_daughter():
    assert not _relationship_stated_by_caller("daughter", [("USER", "My mom needs help dressing.")])
    assert not _relationship_stated_by_caller("daughter", [("USER", "I'm not her daughter.")])
    assert not _relationship_stated_by_caller("daughter", [("USER", "My daughter's mother needs help.")])
    assert _relationship_stated_by_caller("daughter", [("USER", "I'm her daughter.")])
    assert _relationship_stated_by_caller("son", [("USER", "I am her son.")])
    assert _relationship_stated_by_caller("daughter", [
        ("ASSISTANT", "What is your relationship to the person needing care?"),
        ("USER", "Daughter")
    ])


def test_voice_intake_tool_does_not_skip_relationship_due_to_model_guess(monkeypatch):
    async def mock_tool(name, data):
        assert name == "intake_next_question"
        return json.dumps(intake_next_question_sync(json.loads(data)))
    monkeypatch.setattr(session_mod, "handle_tool", mock_tool)

    async def scenario():
        session = NovaSonicSession()
        session.stream = FakeStream()
        session.transcripts = [("USER", "My mom lives in Hudson, and needs help bathing.")]
        await session._start_tool({"toolName": "intake_next_question", "toolUseId": "rel-question", "content": json.dumps({
            "kind_of_help": "bathing", "county": "Hudson", "relationship": "daughter"
        })})
        return json.loads(session.tool_calls[-1]["output"])

    result = asyncio.run(scenario())
    assert result["field"] == "relationship"


def test_voice_save_blocks_guessed_relationship_and_preserves_confirmed_fields(monkeypatch):
    stored = []

    async def mock_tool(name, payload):
        assert name == "save_intake"
        stored.append(json.loads(payload))
        return json.dumps({"saved": True, "intake_id": "test-id"})

    monkeypatch.setattr(session_mod, "handle_tool", mock_tool)

    async def scenario(transcripts, surname):
        s = NovaSonicSession()
        s.stream = FakeStream()
        s.transcripts = transcripts + [
            ("ASSISTANT", "I have 1234567890. Is that the correct callback number?"),
            ("USER", "Yes"),
        ]
        await s._start_tool({"toolName": "save_intake", "toolUseId": surname, "content": json.dumps({
            "caller_name": "Test Caller", "relationship": "daughter", "county": "Hudson",
            "kind_of_help": "bathing", "timeline": "within a month", "callback_phone": "1234567890"
        })})
        return json.loads(s.tool_calls[-1]["output"])

    guess = asyncio.run(scenario([("USER", "My mom needs bathing assistance")], "guess"))
    assert guess["saved"] is False and guess["missing_field"] == "relationship"
    assert stored == []

    confirmed = asyncio.run(scenario([
        ("USER", "I am her daughter. She needs care in Hudson next month"),
        ("ASSISTANT", "What name should the coordinator ask for?"),
        ("USER", "Test Caller"),
    ], "confirmed"))
    assert confirmed["saved"] is True
    assert stored[0]["relationship"] == "daughter"
    assert stored[0]["timeline"] == "within a month"
    assert stored[0]["county"] == "Hudson"


def test_save_checks_county_and_start_time_even_if_model_skipped_them(monkeypatch):
    called = []

    async def mock_tool(name, payload):
        called.append(json.loads(payload))
        return json.dumps({"saved": True})

    monkeypatch.setattr(session_mod, "handle_tool", mock_tool)

    async def save(payload, history=()):
        s = NovaSonicSession()
        s.stream = FakeStream()
        s.transcripts = [("USER", "I am her son."),
                         ("ASSISTANT", "What name should the coordinator ask for?"),
                         ("USER", "Test Caller")] + list(history) + [
            ("ASSISTANT", "Your number is 1234567890. Is that correct?"),
            ("USER", "Yes"),
        ]
        await s._start_tool({"toolName": "save_intake", "toolUseId": "check", "content": json.dumps(payload)})
        return json.loads(s.tool_calls[-1]["output"])

    payload = {"relationship": "son", "timeline": "within a month", "caller_name": "Test Caller", "callback_phone": "1234567890"}
    result = asyncio.run(save(payload))
    assert result["missing_field"] == "county"
    assert not called
    payload["county"] = "Hudson"
    payload.pop("timeline")
    result = asyncio.run(save(payload))
    assert result["missing_field"] == "timeline"
    assert not called
    result = asyncio.run(save(payload, [("ASSISTANT", "When would you like care to start?"), ("USER", "I'm not sure")]))
    assert result["saved"] is True
    assert "timeline" not in called[0]


def test_caller_may_skip_unknown_county_after_it_was_asked(monkeypatch):
    captured = []

    async def mock_tool(name, payload):
        captured.append(json.loads(payload))
        return json.dumps({"saved": True})

    monkeypatch.setattr(session_mod, "handle_tool", mock_tool)

    async def run():
        s = NovaSonicSession()
        s.stream = FakeStream()
        s.transcripts = [
            ("USER", "I am his son"),
            ("ASSISTANT", "Which New Jersey county is care needed in?"),
            ("USER", "I don't know"),
            ("ASSISTANT", "When would you like care to start?"),
            ("USER", "Within a month."),
            ("ASSISTANT", "What name should the coordinator ask for?"),
            ("USER", "Test Caller"),
            ("ASSISTANT", "Is the callback number 1234567890 correct?"),
            ("USER", "yes"),
        ]
        await s._start_tool({"toolName": "save_intake", "toolUseId": "skip", "content": json.dumps({
            "relationship": "son", "county": "not sure", "timeline": "within a month", "caller_name": "Test Caller", "callback_phone": "1234567890"
        })})
        return json.loads(s.tool_calls[-1]["output"])

    result = asyncio.run(run())
    assert result["saved"] is True
    assert "county" not in captured[0]
