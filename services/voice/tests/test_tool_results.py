import asyncio
import json

import nova_sonic.session as session_mod
from nova_sonic.session import NovaSonicSession
from nova_sonic.smoke import check_expected_tool


class _FakeInput:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, chunk):
        self.sent.append(json.loads(chunk.value.bytes_.decode("utf-8"))["event"])


class _FakeStream:
    def __init__(self):
        self.input_stream = _FakeInput()


def test_overlapping_tool_calls_keep_their_own_outputs(monkeypatch):
    # The first call finishes last. Its output must still land on its own record, and each toolResult
    # must carry the toolUseId of the call it answers.
    delays = {"slow": 0.05, "fast": 0.0}

    async def fake_handle_tool(name, args_json):
        await asyncio.sleep(delays[json.loads(args_json)["query"]])
        return json.dumps({"answer_for": json.loads(args_json)["query"]})

    monkeypatch.setattr(session_mod, "handle_tool", fake_handle_tool)

    async def scenario():
        s = NovaSonicSession()
        s.stream = _FakeStream()
        t1 = s._start_tool({"toolName": "lookup_program_info", "toolUseId": "tu-slow", "content": '{"query": "slow"}'})
        t2 = s._start_tool({"toolName": "lookup_program_info", "toolUseId": "tu-fast", "content": '{"query": "fast"}'})
        await asyncio.gather(t1, t2)
        return s

    s = asyncio.run(scenario())
    by_id = {c["toolUseId"]: c for c in s.tool_calls}
    assert json.loads(by_id["tu-slow"]["output"]) == {"answer_for": "slow"}
    assert json.loads(by_id["tu-fast"]["output"]) == {"answer_for": "fast"}
    assert by_id["tu-slow"]["result_sent"] and by_id["tu-fast"]["result_sent"]

    starts = [e["contentStart"] for e in s.stream.input_stream.sent if "contentStart" in e]
    results = [e["toolResult"] for e in s.stream.input_stream.sent if "toolResult" in e]
    content_to_use = {c["contentName"]: c["toolResultInputConfiguration"]["toolUseId"] for c in starts}
    assert {content_to_use[r["contentName"]]: json.loads(r["content"])["answer_for"] for r in results} == {"tu-slow": "slow", "tu-fast": "fast"}


def test_result_not_marked_sent_when_stream_closed(monkeypatch):
    async def fake_handle_tool(name, args_json):
        return '{"passages": [{"text": "x"}]}'

    monkeypatch.setattr(session_mod, "handle_tool", fake_handle_tool)

    async def scenario():
        s = NovaSonicSession()  # stream never opened
        await s._start_tool({"toolName": "lookup_program_info", "toolUseId": "tu-1", "content": '{"query": "q"}'})
        return s

    rec = asyncio.run(scenario()).tool_calls[0]
    assert "output" in rec and rec["result_sent"] is False


def _call(tool_use_id="tu-1", output=None, sent=True, sent_at=10.0, name="lookup_program_info"):
    c = {"name": name, "toolUseId": tool_use_id, "input": "{}", "result_sent": sent}
    if output is not None:
        c["output"] = json.dumps(output)
    if sent:
        c["result_sent_at"] = sent_at
    return c


def test_expect_tool_passes_on_complete_round_trip():
    r = check_expected_tool([_call(output={"passages": [{"text": "x"}]})], "lookup_program_info", last_audio_at=12.0)
    assert r["complete_round_trip"] and r["called"] and r["succeeded"] and r["result_sent"] and r["reply_after_result"]
    assert r["failures"] == []


def test_expect_tool_fails_when_not_called():
    r = check_expected_tool([], "lookup_program_info")
    assert not r["complete_round_trip"] and "never called" in r["failures"][0]


def test_expect_tool_fails_on_retrieval_error_or_empty_passages():
    err = check_expected_tool([_call(output={"error": "retrieve failed: timed out", "passages": []})], "lookup_program_info")
    assert not err["complete_round_trip"] and "retrieve failed" in err["failures"][0]
    empty = check_expected_tool([_call(output={"passages": []})], "lookup_program_info")
    assert not empty["complete_round_trip"] and "no passages" in empty["failures"][0]


def test_expect_tool_fails_when_result_not_sent():
    r = check_expected_tool([_call(output={"passages": [{"text": "x"}]}, sent=False)], "lookup_program_info")
    assert r["succeeded"] and not r["result_sent"] and not r["complete_round_trip"]
    assert "not sent back" in r["failures"][0]


def test_expect_tool_needs_success_and_delivery_on_the_same_call():
    calls = [
        _call("tu-1", output={"passages": [{"text": "x"}]}, sent=False),
        _call("tu-2", output={"error": "retrieve failed"}, sent=True),
    ]
    assert not check_expected_tool(calls, "lookup_program_info")["complete_round_trip"]
