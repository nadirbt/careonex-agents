import json
from types import SimpleNamespace

import pytest
from careonex_retrieve.expansion_eval import evaluate_comparison
from careonex_retrieve.query_expansion import plan_queries
from careonex_retrieve.retriever import Passage, build_filter, reciprocal_rank_fusion, retrieve


def make_passage(key, text):
    return Passage(text=text, score=0.9, source_url="https://nj.gov/", title="Title",
                   program="All DoAS programs", effective_date="2026-03-01",
                   heading_path="care", s3_key=key)


def test_query_expansion_is_conservative():
    q = "Does Medicaid cover help with bathing and dressing in New Jersey?"
    plan = plan_queries(q, "Medicaid")
    assert len(plan) == 3 and plan[0] == q
    assert "PCA" in plan[1] and "MLTSS" in plan[2]
    assert plan_queries(q, "JACC") == plan  # question explicitly says Medicaid
    assert plan_queries("Does NJ FamilyCare Medicaid PCA cover bathing?", "Medicaid") == ["Does NJ FamilyCare Medicaid PCA cover bathing?"]
    assert plan_queries("Does MLTSS cover bathing?", "Medicaid") == ["Does MLTSS cover bathing?"]
    assert plan_queries("Does Medicare cover home health?", "Medicare") == ["Does Medicare cover home health?"]
    assert plan_queries("What is the JACC income limit?", "JACC") == ["What is the JACC income limit?"]
    assert plan_queries("Does Medicaid pay for prescription drugs?", "Medicaid") == ["Does Medicaid pay for prescription drugs?"]
    assert len(plan_queries("Can Medicaid provide a home aide?", "Medicaid")) == 3
    with pytest.raises(ValueError):
        plan_queries("  ", "Medicaid")


def test_rrf_deduplicates_and_moves_consistently_found_passage_to_top():
    runs = [[make_passage("wrong", "PAAD"), make_passage("right", "PCA benefit")],
            [make_passage("right", "PCA benefit"), make_passage("other", "MLTSS")],
            [make_passage("right", "PCA benefit"), make_passage("wrong", "PAAD")]]
    output = reciprocal_rank_fusion(runs)
    assert [p.s3_key for p in output] == ["right", "wrong", "other"]
    assert output[0].fusion_score > output[1].fusion_score
    assert len(output) == 3
    assert [p.s3_key for p in reciprocal_rank_fusion([[runs[0][0], runs[0][0]]])] == ["wrong"]


def test_rrf_avoids_merging_distinct_passages_without_s3_key():
    a = make_passage(None, "first")
    b = make_passage(None, "second")
    assert len(reciprocal_rank_fusion([[a,b]])) == 2


def test_baseline_uses_one_call_expanded_uses_parallel_calls(monkeypatch):
    q = "Does Medicaid cover bathing and dressing at home?"
    class FakeRuntime:
        def __init__(self):
            self.calls = []
        def retrieve(self, **kwargs):
            term = kwargs["retrievalQuery"]["text"]
            self.calls.append(term)
            def raw(key, text):
                return {"content": {"text": text}, "metadata": {"title": key, "program": "All DoAS programs", "effective_date": "2026-04-01"},
                        "location": {"s3Location": {"uri": "s3://bucket/" + key}}}
            if "PCA" in term:
                return {"retrievalResults": [raw("right", "PCA personal care") ,raw("other", "Medicaid") ]}
            if "MLTSS" in term:
                return {"retrievalResults": [raw("right", "PCA personal care"), raw("other2", "MLTSS")]}
            return {"retrievalResults": [raw("wrong", "PAAD benefits"), raw("right", "PCA personal care")]}

    monkeypatch.setenv("CAREONEX_RETRIEVE_RERANK", "none")
    f = FakeRuntime()
    baseline = retrieve(f, "TEST", q, top_k=3, flt=build_filter("Medicaid"), search_mode="baseline", program_hint="Medicaid")
    assert len(f.calls) == 1
    assert baseline.passages[0].s3_key == "wrong" and baseline.search_mode == "baseline"
    f = FakeRuntime()
    expanded = retrieve(f, "TEST", q, top_k=3, flt=build_filter("Medicaid"), search_mode="expanded", program_hint="Medicaid")
    assert len(f.calls) == 3
    assert expanded.passages[0].s3_key == "right"
    assert len(expanded.queries_used) == 3
    assert expanded.latency_ms >= 0
    assert expanded.passages[0].fusion_score is not None
    assert expanded.passages[0].score is None  # fake AWS omitted the Bedrock score


def test_expansion_degrades_to_single_search_for_specific_question(monkeypatch):
    class Stub:
        calls = 0
        def retrieve(self, **kwargs):
            self.calls += 1
            return {"retrievalResults": []}
    monkeypatch.setenv("CAREONEX_RETRIEVE_RERANK", "none")
    s=Stub()
    result=retrieve(s, "TEST", "Does Medicaid PCA cover bathing?", top_k=3, search_mode="expanded", program_hint="Medicaid")
    assert s.calls == 1 and len(result.queries_used) == 1


def test_invalid_mode_fails_clearly():
    with pytest.raises(ValueError):
        retrieve(None, "K", "Medicaid bathing", search_mode="magic")


def test_evaluation_requires_complete_labels_and_uses_shared_union():
    report={"top_k": 2, "candidate_pool": [{"id":"irrelevant","relevance":0},
        {"id":"relevant","relevance":2}, {"id":"partial","relevance":None}],
        "runs": {"baseline":{"ids":["irrelevant","relevant"],"latency_ms":1500},
                 "expanded":{"ids":["relevant","partial"],"latency_ms":2300}}}
    assert evaluate_comparison(report)["ungraded"] == 1
    report["candidate_pool"][2]["relevance"]=1
    result=evaluate_comparison(report)
    assert result["ready"]
    assert result["results"]["expanded"]["mrr_at_k"] == 1
    assert result["results"]["baseline"]["mrr_at_k"] == .5
    assert result["results"]["expanded"]["recall_at_k"] == 1
    assert result["results"]["baseline"]["recall_at_k"] == .5


def test_expansion_guard_protects_wrong_program_questions():
    q="Does JACC cover bathing and dressing?"
    assert plan_queries(q, "JACC") == [q]
    q="Does Medicaid cover home care under MLTSS?"
    assert plan_queries(q, "MLTSS") == [q]


def test_compare_writes_ungraded_union_with_both_modes(tmp_path, monkeypatch):
    from careonex_retrieve import cli
    q = "Does Medicaid cover bathing and dressing?"
    monkeypatch.setattr(cli.config, "knowledge_base_id", lambda: "KBTEST")
    monkeypatch.setattr(cli.config, "session", lambda: SimpleNamespace(client=lambda name: object()))
    a = make_passage("a", "unrelated")
    b = make_passage("b", "PCA")
    c = make_passage("c", "MLTSS")
    def fake_retrieve(*args, search_mode=None, **kwargs):
        return SimpleNamespace(passages=([a, b] if search_mode == "baseline" else [b, c]),
                               latency_ms=1200 if search_mode == "baseline" else 1600,
                               queries_used=[q] if search_mode == "baseline" else [q, "PCA", "MLTSS"])
    monkeypatch.setattr(cli, "retrieve", fake_retrieve)
    filename = tmp_path / "ab.json"
    args = SimpleNamespace(query=q, program="Medicaid", year=None, jurisdiction=None,
                           source_id=None, candidate_k=10, top_k=2, output=filename)
    assert cli.cmd_compare(args) == 0
    saved = json.loads(filename.read_text())
    assert saved["top_k"] == 2
    assert len(saved["candidate_pool"]) == 3
    assert saved["runs"]["expanded"]["ids"][0] == "s3:b"
    assert all(p["relevance"] is None for p in saved["candidate_pool"])


def test_http_explicit_search_mode(monkeypatch):
    from fastapi.testclient import TestClient
    from careonex_retrieve import app as api
    from careonex_retrieve.retriever import RetrievalResult
    called = {}
    monkeypatch.setattr(api.config, "knowledge_base_id", lambda: "TEST")
    monkeypatch.setattr(api, "runtime", lambda: object())
    def fake_retrieve(*args, **kwargs):
        called.update(kwargs)
        return RetrievalResult(query="q", knowledge_base_id="TEST", filter=None,
                               top_k=3, latency_ms=0)
    monkeypatch.setattr(api, "retrieve", fake_retrieve)
    client = TestClient(api.app)
    assert client.post("/retrieve", json={"query": "Does Medicaid cover bathing?", "program": "Medicaid",
                                         "search_mode": "expanded", "top_k": 3}).status_code == 200
    assert called["search_mode"] == "expanded" and called["program_hint"] == "Medicaid"
    assert client.post("/retrieve", json={"query": "Medicaid bathing", "search_mode": "unsupported"}).status_code == 422
