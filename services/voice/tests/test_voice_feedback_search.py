"""Voice's opt-in/rollback wiring for model-free feedback retrieval."""
from nova_sonic import tools as t


def _fake_response(*_args):
    return {"passages": [{"text": "JACC is a NJ home care program.", "title": "JACC",
                          "source_url": "https://nj.gov/example"}],
            "search_mode": "feedback", "expansion_status": "used",
            "queries_used": ["q", "q expanded"], "latency_ms": 45}


def test_voice_uses_feedback_by_default_without_model_call(monkeypatch):
    monkeypatch.delenv("CAREONEX_VOICE_SEARCH_MODE", raising=False)
    monkeypatch.setattr(t, "RETRIEVE_URL", "http://retrieve:8080")
    seen = []
    monkeypatch.setattr(t, "_post_json", lambda url, payload, timeout: (seen.append(payload), _fake_response())[1])
    result = t.lookup_program_info_sync({"query": "How do I apply for JACC?", "program": "JACC"})
    assert seen[0]["search_mode"] == "feedback"
    assert seen[0]["program"] == "JACC"
    assert len(seen) == 1
    assert result["passages"][0]["text"].startswith("JACC")
    assert "queries_used" not in result  # search debugging info must not be spoken


def test_voice_baseline_rollback(monkeypatch):
    monkeypatch.setenv("CAREONEX_VOICE_SEARCH_MODE", "baseline")
    monkeypatch.setattr(t, "RETRIEVE_URL", "http://retrieve:8080")
    seen = []
    monkeypatch.setattr(t, "_post_json", lambda url, payload, timeout: (seen.append(payload), _fake_response())[1])
    result = t.lookup_program_info_sync({"query": "Medicare bathing assistance"})
    assert seen[0]["search_mode"] == "baseline"
    assert len(result["passages"]) == 1


def test_voice_invalid_mode_fails_closed_to_baseline(monkeypatch):
    monkeypatch.setenv("CAREONEX_VOICE_SEARCH_MODE", "intent")  # lacks InvokeModel permission
    monkeypatch.setattr(t, "RETRIEVE_URL", "http://retrieve:8080")
    seen = []
    monkeypatch.setattr(t, "_post_json", lambda url, payload, timeout: (seen.append(payload), _fake_response())[1])
    t.lookup_program_info_sync({"query": "JACC age rules"})
    assert seen[0]["search_mode"] == "baseline"


def test_voice_feedback_preserves_caller_facts_and_parent_disabled(monkeypatch):
    monkeypatch.delenv("CAREONEX_VOICE_SEARCH_MODE", raising=False)
    monkeypatch.delenv("CAREONEX_VOICE_PARENT_CONTEXT", raising=False)
    monkeypatch.setattr(t, "RETRIEVE_URL", "http://retrieve:8080")
    seen = []
    monkeypatch.setattr(t, "_post_json", lambda url, payload, timeout: (seen.append(payload), _fake_response())[1])
    t.lookup_program_info_sync({"query": "help with bathing", "situation": "no Medicaid",
                                "county": "Hudson", "age": "82"})
    assert seen[0]["search_mode"] == "feedback"
    assert "no Medicaid" in seen[0]["query"]
    assert "82" in seen[0]["query"]
    assert "Hudson County" in seen[0]["query"]
    assert "include_parent_context" not in seen[0]


def test_feedback_keeps_rrf_order_but_baseline_keeps_historical_date_order(monkeypatch):
    monkeypatch.setattr(t, "RETRIEVE_URL", "http://retrieve:8080")
    monkeypatch.setattr(t, "_post_json", lambda *args: {"passages": [
        {"text": "Most relevant JACC guidance", "title": "JACC", "effective_date": "2025-01-01"},
        {"text": "Less relevant PACE guidance", "title": "PACE", "effective_date": "2026-01-01"},
    ]})
    monkeypatch.setenv("CAREONEX_VOICE_SEARCH_MODE", "feedback")
    feedback = t.lookup_program_info_sync({"query": "JACC help"})
    assert [p["text"] for p in feedback["passages"]] == [
        "Most relevant JACC guidance", "Less relevant PACE guidance"]
    monkeypatch.setenv("CAREONEX_VOICE_SEARCH_MODE", "baseline")
    baseline = t.lookup_program_info_sync({"query": "JACC help"})
    assert [p["text"] for p in baseline["passages"]] == [
        "Less relevant PACE guidance", "Most relevant JACC guidance"]
