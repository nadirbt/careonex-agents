"""Fail-closed retrieval and intake validation regressions; no AWS calls."""

import asyncio
import json

from nova_sonic import tools as t


def test_unknown_county_is_still_a_missing_field():
    required = {k: "value" for k, _ in t.INTAKE_ORDER}
    required["county"] = "New Jersey"
    out = t.intake_next_question_sync(required)
    assert out["field"] == "county"
    assert "Which New Jersey county" in out["ask"]


def test_save_rejects_noncounty_and_normalizes_real_county(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "INTAKE_DIR", str(tmp_path))
    wrong = t.save_intake_sync({"county": "Newark", "callback_phone": "201-555-0100"})
    assert not wrong["saved"] and list(tmp_path.iterdir()) == []
    fine = t.save_intake_sync({"county": "Monmouth County, NJ", "callback_phone": "201-555-0100"})
    assert fine["saved"]
    assert json.loads(next(tmp_path.glob("*.json")).read_text())["county"] == "Monmouth"
    assert "do not promise" in fine["guidance"]


def test_empty_and_malformed_hits_are_not_evidence(monkeypatch):
    monkeypatch.setattr(t, "RETRIEVE_URL", "http://retrieve:8080")
    monkeypatch.setattr(t, "_post_json", lambda *args: {"passages": [{"text": ""}, {"foo": "bar"}], "latency_ms": 4})
    result = t.lookup_program_info_sync({"query": "income limit"})
    assert result["passages"] == [] and result["error"]
    assert "Do not state figures" in result["guidance"]


def test_retrieval_carries_source_provenance(monkeypatch):
    monkeypatch.setattr(t, "RETRIEVE_URL", "http://retrieve:8080")
    monkeypatch.setattr(t, "_post_json", lambda *args: {"passages": [
        {"text": "JACC is for people 60 and older", "source_url": "https://www.nj.gov/example", "title": "NJ DoAS", "effective_date": "2026-03-01"}
    ]})
    result = t.lookup_program_info_sync({"query": "JACC age"})
    assert result["passages"][0]["source_url"] == "https://www.nj.gov/example"


def test_tool_invalid_json_object_shape():
    result = json.loads(asyncio.run(t.handle_tool("intake_next_question", "[1, 2]")))
    assert "error" in result
