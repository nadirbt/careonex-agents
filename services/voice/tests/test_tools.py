import asyncio
import json

from nova_sonic import events
from nova_sonic.tools import LOOKUP_PROGRAM_INFO, handle_tool, lookup_program_info_sync


def test_prompt_start_carries_tool_configuration():
    payload = json.loads(events.prompt_start("p", [LOOKUP_PROGRAM_INFO]))["event"]["promptStart"]
    assert payload["toolUseOutputConfiguration"] == {"mediaType": "application/json"}
    spec = payload["toolConfiguration"]["tools"][0]["toolSpec"]
    assert spec["name"] == "lookup_program_info"
    schema = json.loads(spec["inputSchema"]["json"])
    assert schema["required"] == ["query"]
    assert "toolConfiguration" not in json.loads(events.prompt_start("p"))["event"]["promptStart"]


def test_tool_result_events_sequence():
    seq = [json.loads(e)["event"] for e in events.tool_result_events("p", "c", "tu-1", '{"passages": []}')]
    assert list(seq[0]) == ["contentStart"] and seq[0]["contentStart"]["type"] == "TOOL" and seq[0]["contentStart"]["role"] == "TOOL"
    assert seq[0]["contentStart"]["toolResultInputConfiguration"]["toolUseId"] == "tu-1"
    assert seq[1]["toolResult"]["content"] == '{"passages": []}'
    assert list(seq[2]) == ["contentEnd"]


def test_lookup_without_retrieve_url_degrades_gracefully(monkeypatch):
    import nova_sonic.tools as t

    monkeypatch.setattr(t, "RETRIEVE_URL", "")
    out = lookup_program_info_sync({"query": "JACC income limit"})
    assert out["passages"] == [] and "follow up" in out["guidance"]
    assert json.loads(asyncio.run(handle_tool("lookup_program_info", '{"query": "x"}')))["error"]


def test_lookup_calls_retrieve_and_trims(monkeypatch):
    import nova_sonic.tools as t

    monkeypatch.setattr(t, "RETRIEVE_URL", "http://retrieve:8080")
    seen = {}

    def fake_post(url, payload, timeout):
        seen["url"], seen["payload"] = url, payload
        return {"latency_ms": 123, "passages": [{"text": "x" * 5000, "title": "JACC", "source_url": "https://nj.gov/jacc", "program": "JACC", "effective_date": "2026-03-12"}]}

    monkeypatch.setattr(t, "_post_json", fake_post)
    out = lookup_program_info_sync({"query": "JACC income limit", "program": "JACC"})
    assert seen["url"] == "http://retrieve:8080/retrieve" and seen["payload"]["program"] == "JACC"
    assert len(out["passages"][0]["text"]) == 1200 and out["passages"][0]["document"] == "JACC" and out["latency_ms"] == 123
    assert out["passages"][0]["year"] == "2026" and "source" not in out["guidance"].lower().replace("source'", "")


def test_passages_sorted_newest_first_and_titles_speakable(monkeypatch):
    import nova_sonic.tools as t

    monkeypatch.setattr(t, "RETRIEVE_URL", "http://retrieve:8080")
    monkeypatch.setattr(t, "_post_json", lambda url, payload, timeout: {"passages": [
        {"text": "old", "title": "NJ Division of Aging Services | 2026 Program Guide", "effective_date": "2025-03-23"},
        {"text": "new", "title": "DoAS Programs Side-by-Side (2026)", "effective_date": "2026-03-01"},
    ]})
    out = lookup_program_info_sync({"query": "JACC income limit"})
    assert [p["text"] for p in out["passages"]] == ["new", "old"]
    assert out["passages"][1]["document"] == "2026 Program Guide"


def test_save_intake_writes_a_record(tmp_path, monkeypatch):
    import nova_sonic.tools as t

    monkeypatch.setattr(t, "INTAKE_DIR", str(tmp_path / "intakes"))
    out = json.loads(asyncio.run(handle_tool("save_intake", json.dumps({
        "caller_name": "Katherine", "relationship": "daughter", "care_recipient_age": "77", "county": "Hudson",
        "kind_of_help": "bathing, meals, companionship", "hours_per_week": "20", "timeline": "now",
        "payer": "not sure", "callback_phone": "201-555-0100", "language": "Spanish"}))))
    assert out["saved"] is True and "coordinator" in out["guidance"]
    files = list((tmp_path / "intakes").glob("*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    assert rec["county"] == "Hudson" and rec["language"] == "Spanish" and rec["status"] == "new" and rec["channel"] == "voice"


def test_save_intake_requires_callback_phone(tmp_path, monkeypatch):
    import nova_sonic.tools as t

    monkeypatch.setattr(t, "INTAKE_DIR", str(tmp_path / "intakes"))
    out = json.loads(asyncio.run(handle_tool("save_intake", json.dumps({"caller_name": "K"}))))
    assert out["saved"] is False and not (tmp_path / "intakes").exists()


def test_prompt_start_lists_both_tools():
    from nova_sonic.tools import TOOLS

    names = [t["toolSpec"]["name"] for t in json.loads(events.prompt_start("p", TOOLS))["event"]["promptStart"]["toolConfiguration"]["tools"]]
    assert names == ["lookup_program_info", "save_intake"]
