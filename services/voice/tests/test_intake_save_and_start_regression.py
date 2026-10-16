"""Live-call regression: start-time prompt and actual local intake persistence.

These tests do not invoke AWS or change any retrieval/RAG code.
"""
import asyncio
import json

import nova_sonic.tools as tools_mod
from nova_sonic.session import NovaSonicSession


class _FakeInput:
    def __init__(self):
        self.sent = []

    async def send(self, chunk):
        self.sent.append(json.loads(chunk.value.bytes_.decode("utf-8"))["event"])


class _FakeStream:
    def __init__(self):
        self.input_stream = _FakeInput()


async def _call(session, tool, args, identifier):
    await session._start_tool({
        "toolName": tool, "toolUseId": identifier, "content": json.dumps(args),
    })
    return json.loads(session.tool_calls[-1]["output"])


def test_model_cannot_skip_start_time_with_invented_now():
    async def scenario():
        s = NovaSonicSession()
        s.stream = _FakeStream()
        s.transcripts = [
            ("USER", "My mom needs help bathing in Hudson County."),
            ("ASSISTANT", "What is your relationship to the person?"),
            ("USER", "I'm her son."),
        ]
        result = await _call(s, "intake_next_question", {
            "kind_of_help": "bathing", "county": "Hudson", "relationship": "son",
            "timeline": "now",  # hallucinated by the model, NOT said by caller
        }, "q")
        return result, s

    result, session = asyncio.run(scenario())
    assert result["field"] == "timeline"
    assert "start" in result["ask"].lower()
    assert "timeline" not in session._intake_details


def test_missing_start_blocks_save_even_when_model_supplies_one(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_mod, "INTAKE_DIR", str(tmp_path / "intakes"))

    async def scenario():
        s = NovaSonicSession()
        s.stream = _FakeStream()
        s.transcripts = [
            ("USER", "I need bathing help for my mother in Hudson County."),
            ("ASSISTANT", "What is your relationship to her?"),
            ("USER", "I'm her daughter."),
            ("ASSISTANT", "Is your phone number 1234567890 correct?"),
            ("USER", "Yes."),
        ]
        r = await _call(s, "save_intake", {
            "kind_of_help": "bathing", "county": "Hudson", "relationship": "daughter",
            "caller_name": "Test Caller", "callback_phone": "1234567890",
            "timeline": "now",  # the caller never said this
        }, "early")
        return r

    r = asyncio.run(scenario())
    assert r["saved"] is False
    assert r["missing_field"] == "timeline"
    assert not list(tmp_path.rglob("*.json"))


def test_collects_missing_start_carries_answers_and_writes_real_file(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_mod, "INTAKE_DIR", str(tmp_path / "intakes"))

    async def scenario():
        s = NovaSonicSession()
        s.stream = _FakeStream()
        s.transcripts = [
            ("USER", "My mother needs help bathing and dressing in Hudson County."),
            ("ASSISTANT", "How are you related to her?"),
            ("USER", "I'm her son."),
        ]
        first = await _call(s, "intake_next_question", {
            "kind_of_help": "bathing and dressing", "county": "Hudson",
            "relationship": "son", "timeline": "now",  # wrong model guess
            "caller_name": "Test Caller", "callback_phone": "1234567890",
        }, "next")
        assert first["field"] == "timeline"
        s.transcripts.extend([
            ("ASSISTANT", "When would you like care to start?"),
            ("USER", "Within a month."),
            ("ASSISTANT", "What name should the coordinator ask for?"),
            ("USER", "Test Caller"),
        ])
        # The model's earlier, unsupported name is discarded. The new answer
        # makes it safe to remember, while earlier county/relationship persist.
        later = await _call(s, "intake_next_question", {
            "timeline": "within a month", "caller_name": "Test Caller",
        }, "followup")
        assert later["complete"] is True
        s.transcripts.extend([
            ("ASSISTANT", "Your callback number is one two three four five six seven eight nine zero. Is that correct?"),
            ("USER", "Yes."),
        ])
        result = await _call(s, "save_intake", {"callback_phone": "1234567890"}, "save")
        return result

    result = asyncio.run(scenario())
    assert result["saved"] is True
    files = list((tmp_path / "intakes").glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text())
    assert saved["county"] == "Hudson"
    assert saved["relationship"] == "son"
    assert saved["timeline"] == "within a month"
    assert saved["caller_name"] == "Test Caller"
    assert saved["callback_phone"] == "1234567890"
    assert saved["kind_of_help"] == "bathing and dressing"
