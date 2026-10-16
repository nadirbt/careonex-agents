"""Intent planner integration tests: all requests are mocked, never call AWS."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from careonex_retrieve.query_expansion import IntentPlan, plan_intent_queries
from careonex_retrieve.retriever import build_filter, retrieve, without_program_filter


class FakeModel:
    def __init__(self, queries=None, broaden=False, *, bad=None, error=None):
        self.queries = queries if queries is not None else []
        self.broaden = broaden
        self.bad = bad
        self.error = error
        self.requests = []

    def converse(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        text = self.bad if self.bad is not None else json.dumps({
            "queries": self.queries, "broaden_program_filter": self.broaden,
        })
        return {"output": {"message": {"content": [{"text": text}]}}}


class FakeKB:
    def __init__(self, fail_supplementary=False):
        self.calls = []
        self.fail_supplementary = fail_supplementary

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        query = kwargs["retrievalQuery"]["text"]
        if self.fail_supplementary and "Original caller question:" in query:
            raise RuntimeError("supplementary failed")
        if "JACC" in query:
            items = [("jacc", "JACC non-Medicaid home-care guidance"), ("general", "general NJ programs")]
        elif "Medicare" in query:
            items = [("medicare", "Medicare skilled home health benefits"), ("general", "general NJ programs")]
        else:
            items = [("general", "general NJ programs"), ("jacc", "JACC non-Medicaid home-care guidance")]
        return {"retrievalResults": [{"content": {"text": text},
                "metadata": {"title": name, "program": "All DoAS programs", "effective_date": "2026-01-01"},
                "location": {"s3Location": {"uri": f"s3://some-bucket/{name}"}}}
                for name, text in items]}


def test_intent_planner_handles_jacc_and_non_medicaid_question():
    question = "I'm not on Medicaid. What home-care alternatives can I use?"
    model = FakeModel(["NJ JACC non-Medicaid senior home-care assistance",
                       "NJ alternative payment options for older adults"], broaden=True)
    plan = plan_intent_queries(question, model, program="Medicaid")
    assert len(plan.queries) == 3
    assert plan.queries[0] == question
    assert all(question in q for q in plan.queries[1:])
    assert "JACC" in plan.queries[1]
    assert plan.broaden_program_filter
    assert model.requests[0]["modelId"] == "amazon.nova-lite-v1:0"
    assert model.requests[0]["inferenceConfig"]["temperature"] == 0
    assert json.loads(model.requests[0]["messages"][0]["content"][0]["text"])["caller_question"] == question


def test_intent_planner_handles_medicare_and_specific_queries():
    question = "Does Medicare pay for bathing when I don't need skilled nursing?"
    model = FakeModel(["Medicare home health aide limitations without skilled services"])
    p = plan_intent_queries(question, model)
    assert p.queries[0] == question and "Medicare" in p.queries[1]
    precise = "What number should I call for Bergen County ADRC?"
    no_extra = FakeModel([])
    assert plan_intent_queries(precise, no_extra).queries == [precise]
    assert len(no_extra.requests) == 1


def test_query_validation_and_deduplication():
    q = "How do I apply for PACE?"
    plan = plan_intent_queries(q, FakeModel([q, "  PACE    New Jersey eligibility details  "]))
    assert plan.queries == [q, f"PACE New Jersey eligibility details | Original caller question: {q}"]
    for wrong in ('not json', '{"queries":["a","b","c"],"broaden_program_filter":false}',
                  '{"queries":"not a list","broaden_program_filter":false}',
                  '{"queries":["valid"],"broaden_program_filter":"true"}',
                  '{"queries":["okay"],"broaden_program_filter":false,"surprise":1}'):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            plan_intent_queries(q, FakeModel(bad=wrong))
    huge = "Very long question " * 50
    model = FakeModel(["not used"])
    assert plan_intent_queries(huge, model).queries == [huge.strip()]
    assert not model.requests


def test_program_filter_broadening_preserves_nonprogram_limits():
    f = build_filter("Medicaid", year=2026, jurisdiction="NJ", source_id="test")
    broadened = without_program_filter(f)
    text = str(broadened)
    assert "program" not in text
    for token in ("year", "jurisdiction", "source_id"):
        assert token in text
    assert without_program_filter(build_filter("Medicaid")) is None
    assert without_program_filter(None) is None


def test_retrieve_intent_fuses_programs_beyond_medicaid(monkeypatch):
    monkeypatch.setenv("CAREONEX_RETRIEVE_RERANK", "none")
    kb = FakeKB()
    question = "I'm not on Medicaid. What options do I have?"
    model = FakeModel(["New Jersey JACC alternatives for non-Medicaid older adults"], broaden=True)
    original = build_filter("Medicaid")
    result = retrieve(kb, "TEST", question, top_k=3, flt=original, search_mode="intent",
                      program_hint="Medicaid", query_model=model)
    assert result.search_mode == "intent"
    assert result.expansion_status == "used"
    assert result.queries_used[0] == question
    assert len(result.queries_used) == 2
    assert result.filter is None
    assert len(kb.calls) == 2
    assert all("filter" not in call["retrievalConfiguration"]["vectorSearchConfiguration"] for call in kb.calls)
    assert result.planning_latency_ms >= 0
    assert all(p.fusion_score for p in result.passages)
    assert result.latency_ms >= result.planning_latency_ms


def test_retrieve_intent_keeps_specific_program_filter(monkeypatch):
    monkeypatch.setenv("CAREONEX_RETRIEVE_RERANK", "none")
    kb = FakeKB()
    filt = build_filter("Medicare")
    model = FakeModel(["Medicare skilled home health benefit coverage"], broaden=False)
    result = retrieve(kb, "TEST", "Does Medicare cover home aide care?", flt=filt,
                      search_mode="intent", query_model=model, program_hint="Medicare")
    assert len(result.queries_used) == 2
    assert result.filter == filt
    assert all(call["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"] == filt for call in kb.calls)


def test_planner_access_failure_or_bad_json_falls_back_to_original(monkeypatch):
    monkeypatch.setenv("CAREONEX_RETRIEVE_RERANK", "none")
    q = "My sister can she get paid as my caregiver?"
    for model in (FakeModel(error=TimeoutError("timeout")), FakeModel(bad="not-json")):
        kb = FakeKB()
        r = retrieve(kb, "TEST", q, search_mode="intent", query_model=model)
        assert r.expansion_status == "fallback"
        assert r.queries_used == [q]
        assert len(kb.calls) == 1
        assert r.passages


def test_one_query_is_not_fused_or_marked_failure(monkeypatch):
    monkeypatch.setenv("CAREONEX_RETRIEVE_RERANK", "none")
    r = retrieve(FakeKB(), "TEST", "What is JACC?", search_mode="intent", query_model=FakeModel([]))
    assert r.expansion_status == "no_expansion" and r.passages[0].fusion_score is None


def test_supplementary_search_failure_does_not_lose_baseline(monkeypatch):
    monkeypatch.setenv("CAREONEX_RETRIEVE_RERANK", "none")
    r = retrieve(FakeKB(fail_supplementary=True), "TEST", "What does Medicare cover?",
                 search_mode="intent", query_model=FakeModel(["Medicare home health requirements"]))
    assert r.passages and r.queries_used[0] == "What does Medicare cover?"
    assert r.expansion_status == "partial_fallback"


def test_http_accepts_intent_mode(monkeypatch):
    from careonex_retrieve import app as api
    from careonex_retrieve.retriever import RetrievalResult
    monkeypatch.setattr(api.config, "knowledge_base_id", lambda: "TEST")
    monkeypatch.setattr(api, "runtime", lambda: object())
    received = {}
    def fake(*args, **kwargs):
        received.update(kwargs)
        return RetrievalResult(query="q", knowledge_base_id="TEST", filter=None, top_k=3, latency_ms=0)
    monkeypatch.setattr(api, "retrieve", fake)
    result = TestClient(api.app).post("/retrieve", json={"query":"What is the PACE age requirement?", "search_mode":"intent"})
    assert result.status_code == 200 and received["search_mode"] == "intent"


def test_cli_candidate_intent_benchmark(tmp_path, monkeypatch):
    from careonex_retrieve import cli
    from careonex_retrieve.batch_eval import evaluate_batch
    from careonex_retrieve.batch_compare import write_json
    monkeypatch.setattr(cli.config, "knowledge_base_id", lambda: "TEST")
    monkeypatch.setattr(cli.config, "session", lambda: SimpleNamespace(client=lambda name: object()))
    seen = []
    def fake(*args, search_mode=None, **kwargs):
        seen.append(search_mode)
        passage = SimpleNamespace(s3_key="x", title="x", text="x", source_url="https://nj.gov")
        return SimpleNamespace(passages=[passage], latency_ms=40, queries_used=[args[2]])
    monkeypatch.setattr(cli, "retrieve", fake)
    questions = tmp_path / "q.json"
    write_json(questions, {"questions": [{"id":"pace_eligibility","query":"Who qualifies for PACE?"}]})
    out = tmp_path / "results"
    args = SimpleNamespace(questions=questions, output_dir=out, top_k=1, candidate_k=5,
                           candidate_mode="intent", limit=None, seed_dir=None, force=False)
    cli.cmd_batch_compare(args)
    assert seen == ["baseline", "intent"]
    report_path = out / "cases/pace_eligibility.json"
    report = json.loads(report_path.read_text())
    assert set(report["runs"]) == {"baseline", "intent"}
    report["candidate_pool"][0]["relevance"] = 2
    write_json(report_path, report)
    summary = evaluate_batch(out)
    assert summary["candidate_mode"] == "intent"
    assert summary["summary_all_graded"]["intent"]["ndcg_at_k"] == 1
    args.candidate_mode = "expanded"
    with pytest.raises(ValueError):
        cli.cmd_batch_compare(args)  # never reuse reports from another mode
