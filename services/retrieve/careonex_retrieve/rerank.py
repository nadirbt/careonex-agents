"""Optional, read-only second-stage ranking experiments.

Default ranking remains Bedrock's returned order. `lexical` uses local TF-IDF
cosine similarity blended with the Bedrock rank (fast, no external embedding
calls). `titan` explicitly requests Titan v2 text embeddings and computes true
dense-vector cosine similarity; it is more costly and should be benchmarked
before enabling in the low-latency voice path.

Bedrock's opaque retrieval `score` is NOT asserted to be cosine similarity.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:['’-][a-z0-9]+)?", re.I)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("vectors must be nonempty and have the same dimensions")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def lexical_cosine_scores(query: str, documents: list[str]) -> list[float]:
    """Corpus-local TF-IDF cosine; lexical baseline, NOT embedding similarity."""
    tokenized = [Counter(_TOKEN_RE.findall(t.lower())) for t in [query, *documents]]
    if not documents:
        return []
    docfreq = Counter(t for c in tokenized[1:] for t in c)
    n = len(documents)
    weights = {t: 1.0 + math.log((n + 1) / (df + 1)) for t, df in docfreq.items()}
    # Query-only words don't increase similarity; the weights naturally ignore them.
    def vec(c: Counter) -> dict[str, float]:
        return {t: (1.0 + math.log(count)) * weights[t] for t, count in c.items() if t in weights}
    q = vec(tokenized[0])
    qnorm = math.sqrt(sum(x * x for x in q.values()))
    result = []
    for doc in tokenized[1:]:
        v = vec(doc)
        denom = qnorm * math.sqrt(sum(x * x for x in v.values()))
        result.append(sum(q.get(t, 0.0) * value for t, value in v.items()) / denom if denom else 0.0)
    return result


def titan_embed(client, text: str, model_id: str = "amazon.titan-embed-text-v2:0") -> list[float]:
    """Invoke an embedding-only Bedrock model (not an LLM judge or KB write)."""
    # Titan v2 has an input-length limit; use a bounded passage while retaining the heading.
    response = client.invoke_model(modelId=model_id, contentType="application/json", accept="application/json",
                                   body=json.dumps({"inputText": text[:7500], "normalize": True}).encode("utf-8"))
    body = response["body"]
    payload = json.loads(body.read() if hasattr(body, "read") else body)
    embedding = payload.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise ValueError("embedding response missing embedding vector")
    return [float(v) for v in embedding]


def rerank_passages(query: str, passages: list, *, mode: str = "none", embed_client=None,
                    model_id: str = "amazon.titan-embed-text-v2:0", cosine_weight: float = 0.6) -> list:
    """Return same Passage objects re-ordered. Stores optional `rerank_score` per item.

    Normalized original rank (not raw Bedrock score) is blended with secondary
    cosine ranking to avoid assuming Bedrock scores are cross-query calibrated.
    """
    if mode == "none" or not passages:
        return list(passages)
    if not 0 <= cosine_weight <= 1:
        raise ValueError("cosine_weight must be between 0 and 1")
    if mode == "lexical":
        similarities = lexical_cosine_scores(query, [p.text for p in passages])
    elif mode == "titan":
        if embed_client is None:
            raise ValueError("titan mode requires an embedding client")
        qvector = titan_embed(embed_client, query, model_id)
        similarities = [cosine_similarity(qvector, titan_embed(embed_client, p.text, model_id)) for p in passages]
    else:
        raise ValueError(f"Unknown reranking mode: {mode}")
    # Normalize cosine to the observed candidate range; a zero-information tie
    # must not override the original Bedrock order.
    low, high = min(similarities), max(similarities)
    for i, (passage, sim) in enumerate(zip(passages, similarities)):
        sim_norm = (sim - low) / (high - low) if high > low else 0.0
        base_rank = 1.0 - i / max(1, len(passages) - 1)
        passage.rerank_score = round(cosine_weight * sim_norm + (1 - cosine_weight) * base_rank, 6)
    return sorted(passages, key=lambda p: p.rerank_score or 0.0, reverse=True)
