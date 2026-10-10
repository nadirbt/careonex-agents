"""Language switching and missed-answer recovery regressions (no Bedrock calls)."""
import asyncio
import json

from nova_sonic.config import DEFAULT_SYSTEM_PROMPT, VOICE_ID
from nova_sonic.session import NovaSonicSession
from nova_sonic.tools import (
    INTAKE_NEXT_QUESTION,
    SET_CONVERSATION_LANGUAGE,
    TOOLS,
    handle_tool,
    intake_next_question_sync,
    save_intake_sync,
    set_conversation_language_sync,
    explicit_language_request,
)


class FakeInput:
    def __init__(self):
        self.sent = []

    async def send(self, part):
        self.sent.append(json.loads(part.value.bytes_.decode("utf-8"))["event"])


class FakeStream:
    def __init__(self):
        self.input_stream = FakeInput()


def test_uses_polyglot_voice_and_prompts_for_switching():
    assert VOICE_ID in ("matthew", "tiffany")
    assert "set_conversation_language" in DEFAULT_SYSTEM_PROMPT
    assert "unless the caller changes it" in DEFAULT_SYSTEM_PROMPT
    assert "repeat the SAME pending question" in DEFAULT_SYSTEM_PROMPT
    assert "DO NOT assume a value" in DEFAULT_SYSTEM_PROMPT
    assert "don't endlessly repeat" in DEFAULT_SYSTEM_PROMPT
    names = [item["toolSpec"]["name"] for item in TOOLS]
    assert "set_conversation_language" in names
    schema = json.loads(SET_CONVERSATION_LANGUAGE["toolSpec"]["inputSchema"]["json"])
    assert schema["required"] == ["language"]


def test_switches_to_each_official_supported_language():
    for entry, canonical in {
        "English": "English", "Español": "Spanish", "French": "French", "Deutsch": "German",
        "Italiano": "Italian", "Português": "Portuguese", "हिंदी": "Hindi"
    }.items():
        result = set_conversation_language_sync({"language": entry})
        assert result["changed"] and result["language"] == canonical
        assert f"TRY speaking {canonical}" in result["guidance"]


def test_any_language_can_be_attempted_without_claiming_guaranteed_support():
    for lang, canonical in (("Mandarin", "Mandarin Chinese"), ("中文", "Mandarin Chinese"),
                            ("Japanese", "Japanese"), ("Korean", "Korean"),
                            ("Arabic", "Arabic"), ("Swahili", "Swahili")):
        result = set_conversation_language_sync({"language": lang})
        assert result["changed"] and result["language"] == canonical
        assert "best effort" in result["guidance"].lower()
    assert not set_conversation_language_sync({"language": ""})["changed"]


def test_language_tool_works_via_handler_without_aws():
    result = json.loads(asyncio.run(handle_tool("set_conversation_language", '{"language":"Spanish"}')))
    assert result["changed"] is True and result["language"] == "Spanish"


def test_language_preference_stays_per_session_and_propagates_to_retrieval(monkeypatch):
    import nova_sonic.session as mod

    async def fake_handle_tool(name, args_json):
        return json.dumps({"passages": [{"text": "Source text in English"}]})

    monkeypatch.setattr(mod, "handle_tool", fake_handle_tool)

    async def scenario():
        first = NovaSonicSession()
        second = NovaSonicSession()
        first.stream = FakeStream()
        first.transcripts.append(("USER", "Could you speak Spanish, please?"))
        await first._start_tool({"toolName": "set_conversation_language", "toolUseId": "l1", "content": '{"language":"Spanish"}'})
        await first._start_tool({"toolName": "lookup_program_info", "toolUseId": "r1", "content": '{"query":"JACC"}'})
        first.transcripts.extend([("ASSISTANT", "Here is what I found."), ("USER", "Can you speak Mandarin?")])
        await first._start_tool({"toolName": "set_conversation_language", "toolUseId": "l2", "content": '{"language":"Mandarin"}'})
        assert first.preferred_language == "Mandarin Chinese"
        first.transcripts.extend([("ASSISTANT", "我可以试着说中文。"), ("USER", "Can you speak English?")])
        await first._start_tool({"toolName": "set_conversation_language", "toolUseId": "l3", "content": '{"language":"English"}'})
        return first, second

    first, second = asyncio.run(scenario())
    results = {c["toolUseId"]: json.loads(c["output"]) for c in first.tool_calls}
    assert results["l1"]["language"] == "Spanish"
    assert results["r1"]["conversation_language"] == "Spanish"
    assert "ONLY in Spanish" in results["r1"]["language_guidance"]
    assert results["l2"]["changed"] and results["l2"]["language"] == "Mandarin Chinese"
    assert first.preferred_language == "English" and second.preferred_language == "English"
    assert all(c["result_sent"] for c in first.tool_calls)


def test_repeat_missing_answer_without_advancing_to_a_new_field():
    args = {"relationship": "daughter", "care_recipient_age": "78", "county": "Monmouth", "repeat_field": "county"}
    result = intake_next_question_sync(args)
    assert result["repeated"] is True and result["field"] == "county"
    assert result["ask"] == "Which New Jersey county is care needed in?"
    assert result["ask"].count("?") == 1
    spec = json.loads(INTAKE_NEXT_QUESTION["toolSpec"]["inputSchema"]["json"])
    assert "county" in spec["properties"]["repeat_field"]["enum"]


def test_clear_answer_moves_on_without_repeating():
    args = {"relationship": "son", "care_recipient_age": "75", "county": "Monmouth"}
    result = intake_next_question_sync(args)
    assert result["field"] == "kind_of_help"
    assert not result.get("repeated", False)


def test_invalid_partial_phone_is_rejected_not_saved(tmp_path, monkeypatch):
    import nova_sonic.tools as tools
    monkeypatch.setattr(tools, "INTAKE_DIR", str(tmp_path / "intakes"))
    result = save_intake_sync({"callback_phone": "201-55", "county": "Monmouth"})
    assert not result["saved"] and result["error"] == "invalid callback_phone"
    assert "repeat" in result["guidance"].lower()
    assert not (tmp_path / "intakes").exists()


def test_language_guard_rejects_wrong_or_unrequested_switch():
    assert not explicit_language_request("你好", "Spanish")
    assert not explicit_language_request("Hola, can you help me?", "Spanish")
    assert not explicit_language_request("Can you speak English?", "Spanish")
    assert not explicit_language_request("The Medicaid guide is available in English.", "English")
    assert explicit_language_request("Can you speak English?", "English")
    assert explicit_language_request("English, please.", "English")
    assert explicit_language_request("¿puedes hablar inglés?", "English")
    assert explicit_language_request("Could you speak Spanish from now on?", "Spanish")
    assert explicit_language_request("请用英语回答", "English")
    assert explicit_language_request("¿puedes hablar chino?", "Chinese")
    assert explicit_language_request("¿puedes hablar chino?", "Mandarin")


def test_wrong_language_tool_call_is_rejected_even_if_model_requests_it():
    async def scenario():
        session = NovaSonicSession()
        session.stream = FakeStream()
        session.transcripts = [("USER", "你好")]
        await session._start_tool({"toolName": "set_conversation_language", "toolUseId": "wrong", "content": '{"language":"Spanish"}'})
        return session
    session = asyncio.run(scenario())
    assert session.preferred_language == "English"
    result = json.loads(session.tool_calls[0]["output"])
    assert result["changed"] is False and "no explicit request" in result["error"]
    assert session.tool_calls[0]["result_sent"] is True


def test_retrieve_timeout_allows_slow_aws_requests():
    from nova_sonic.config import RETRIEVE_TIMEOUT_S
    assert RETRIEVE_TIMEOUT_S >= 10  # configurable with CAREONEX_RETRIEVE_TIMEOUT_S
