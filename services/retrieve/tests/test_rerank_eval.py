import io
import json
from types import SimpleNamespace

import pytest
from careonex_retrieve.rerank import cosine_similarity, lexical_cosine_scores, rerank_passages, titan_embed
from careonex_retrieve.ranking_eval import evaluate_case, metrics


def test_dense_cosine_math():
    assert cosine_similarity([1, 0], [1, 0]) == 1
    assert cosine_similarity([1, 0], [0, 1]) == 0
    assert cosine_similarity([2, 2], [-2, -2]) == pytest.approx(-1)
    assert cosine_similarity([0, 0], [1, 2]) == 0
    with pytest.raises(ValueError):
        cosine_similarity([1], [1, 2])


def test_local_tfidf_cosine_prioritizes_a_relevant_passage():
    scores = lexical_cosine_scores("Medicaid bathing and dressing", [
        "JACC funding for transportation", "Medicaid provides bathing and dressing help at home",
        "Medicare hospital stays"])
    assert scores[1] > scores[0] and scores[1] > scores[2]


def test_lexical_rerank_opt_in_and_score_exposed():
    rows = [SimpleNamespace(text="unrelated unrelated", score=0.9, rerank_score=None),
            SimpleNamespace(text="Medicaid bathing dressing", score=0.7, rerank_score=None)]
    assert rerank_passages("Medicaid bathing dressing", rows)[0] is rows[0]
    ordered = rerank_passages("Medicaid bathing dressing", rows, mode="lexical")
    assert ordered[0] is rows[1] and ordered[0].rerank_score > ordered[1].rerank_score
    with pytest.raises(ValueError):
        rerank_passages("hello", rows, mode="titan")


def test_titan_embeddings_are_only_invoked_when_requested():
    class Client:
        calls = 0
        def invoke_model(self, **kwargs):
            self.calls += 1
            assert kwargs["modelId"].startswith("amazon.titan-embed")
            return {"body": io.BytesIO(json.dumps({"embedding": [1.0, 0.0]}).encode())}
    client = Client()
    rows = [SimpleNamespace(text="a", rerank_score=None)]
    rerank_passages("b", rows, mode="titan", embed_client=client)
    assert client.calls == 2


def test_ranking_evaluation_relevance_metrics():
    case = {"query": "Medicaid bathing", "passages": [
        {"s3_key": "no", "text": "insurance car insurance", "relevance": 0},
        {"s3_key": "yes", "text": "Medicaid bathing benefits", "relevance": 2},
        {"s3_key": "maybe", "text": "home care Medicaid", "relevance": 1},
    ]}
    report = evaluate_case(case, top_k=1)
    assert report["lexical_cosine_rerank"]["ndcg_at_k"] > report["bedrock_order"]["ndcg_at_k"]
    assert report["lexical_cosine_rerank"]["mrr_at_k"] == 1
    with pytest.raises(ValueError):
        evaluate_case({"query": "foo", "passages": [{"text": "bar"}]})
