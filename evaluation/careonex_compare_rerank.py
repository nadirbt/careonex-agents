r"""Compare rerankers on one FIXED, manually graded CareOneX passage pool.

Run from the CareOneX project root:
  uv run --python 3.12 --directory services/retrieve python "$PWD\evaluation\careonex_compare_rerank.py" --input "$PWD\evaluation\medicaid-real-graded.json" --methods baseline,lexical,titan,bedrock

The script never writes to your Knowledge Base. Titan and Bedrock rerank
methods invoke AWS models and can incur cost; request only methods desired.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from careonex_retrieve.ranking_eval import metrics, read_cases
from careonex_retrieve.rerank import cosine_similarity, lexical_cosine_scores, titan_embed


def rerank_indices(similarities: list[float], blend_weight: float) -> list[int]:
    n = len(similarities)
    if not n:
        return []
    lo, hi = min(similarities), max(similarities)
    scores = []
    for i, sim in enumerate(similarities):
        cosine_normalized = (sim - lo) / (hi - lo) if hi > lo else 0.0
        original_rank = 1 - i / max(1, n - 1)
        scores.append(blend_weight * cosine_normalized + (1 - blend_weight) * original_rank)
    return sorted(range(n), key=lambda i: (-scores[i], i))


def bedrock_rerank(client, query: str, docs: list[str], model_arn: str) -> tuple[list[int], list[float]]:
    response = client.rerank(
        queries=[{"type": "TEXT", "textQuery": {"text": query}}],
        sources=[{"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": doc}}} for doc in docs],
        rerankingConfiguration={
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {"modelArn": model_arn},
                "numberOfResults": len(docs),
            },
        },
    )
    scores = [None] * len(docs)
    order = []
    for result in response.get("results", []):
        index = int(result["index"])
        if 0 <= index < len(docs) and index not in order:
            order.append(index)
            scores[index] = float(result["relevanceScore"])
    # Preserve any omitted candidates at the bottom in original order.
    order += [i for i in range(len(docs)) if i not in order]
    return order, scores


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True, help="JSON with a fixed query, passages, and reviewed relevance grades")
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--methods", default="baseline,lexical,titan,bedrock", help="Comma-separated: baseline,lexical,titan,bedrock")
    p.add_argument("--output", type=Path, help="Optional local results JSON path")
    args = p.parse_args()
    if args.top_k < 1:
        p.error("--top-k must be positive")
    requested = [s.strip().lower() for s in args.methods.split(",") if s.strip()]
    valid = {"baseline", "lexical", "titan", "bedrock"}
    if not requested or any(m not in valid for m in requested):
        p.error(f"--methods must contain only {', '.join(sorted(valid))}")
    cases = read_cases(args.input)
    if len(cases) != 1:
        p.error("This focused experiment requires exactly one graded query/case")
    case = cases[0]
    query = case["query"]
    passages = case["passages"]
    if not passages or any("text" not in q or q.get("relevance") not in (0, 1, 2) for q in passages):
        p.error("Each passage must have text and a manually reviewed relevance grade of 0, 1, or 2")
    rows = [SimpleNamespace(**item) for item in passages]
    texts = [r.text for r in rows]
    n = len(rows)
    results = {}

    def add(name: str, order: list[int], time_seconds: float = 0.0, raw_scores=None):
        ranked = [rows[i] for i in order]
        results[name] = {
            "metrics": metrics(ranked, args.top_k),
            "latency_seconds": round(time_seconds, 3),
            "top": [
                {"rank": k + 1, "candidate_index": i, "relevance_grade": rows[i].relevance,
                 "title": getattr(rows[i], "heading_path", None) or getattr(rows[i], "title", None),
                 "source": getattr(rows[i], "s3_key", None),
                 "score": round(raw_scores[i], 6) if raw_scores is not None and raw_scores[i] is not None else None}
                for k, i in enumerate(order[:args.top_k])
            ],
        }

    if "baseline" in requested:
        add("bedrock_baseline", list(range(n)))
    if "lexical" in requested:
        t = time.perf_counter()
        lexical_scores = lexical_cosine_scores(query, texts)
        add("tfidf_blend_0.6", rerank_indices(lexical_scores, 0.6), time.perf_counter() - t, lexical_scores)
    if "titan" in requested or "bedrock" in requested:
        import boto3
        region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        sess = boto3.Session(region_name=region)
    if "titan" in requested:
        try:
            t = time.perf_counter()
            client = sess.client("bedrock-runtime")
            model_id = os.getenv("CAREONEX_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
            qv = titan_embed(client, query, model_id)
            sims = [cosine_similarity(qv, titan_embed(client, doc, model_id)) for doc in texts]
            elapsed = time.perf_counter() - t
            add("titan_pure_cosine", rerank_indices(sims, 1.0), elapsed, sims)
            add("titan_blend_0.6", rerank_indices(sims, 0.6), elapsed, sims)
        except Exception as exc:
            results["titan_error"] = f"{type(exc).__name__}: {exc}"
    if "bedrock" in requested:
        try:
            t = time.perf_counter()
            client = sess.client("bedrock-agent-runtime")
            model_arn = os.getenv("CAREONEX_RERANK_MODEL_ARN", f"arn:aws:bedrock:{region}::foundation-model/cohere.rerank-v3-5:0")
            order, scores = bedrock_rerank(client, query, texts, model_arn)
            add("bedrock_reranker", order, time.perf_counter() - t, scores)
        except Exception as exc:
            results["bedrock_error"] = f"{type(exc).__name__}: {exc}"

    report = {"query": query, "candidates": n, "top_k": args.top_k,
              "label_note": "Provisional human relevance grades; this compares ranking in a fixed candidate pool, not KB recall or answer accuracy.",
              "results": results}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
