"""Offline A/B evaluation of reranking using manually graded candidate passages.

Input JSON/JSONL: {"query": "...", "passages": [{"text":"...", "score":0.7,
"s3_key":"...", "relevance": 0|1|2}, ...]}. Every passage is graded by a
reviewer (0 irrelevant, 1 partially relevant, 2 answer-bearing). The pool
is fixed: this evaluates ordering, NOT KB search recall or answer accuracy.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from types import SimpleNamespace

from careonex_retrieve.rerank import rerank_passages


def metrics(passages: list, top_k: int = 5) -> dict:
    grades = [int(p.relevance) for p in passages]
    top = grades[:top_k]
    relevant = sum(g > 0 for g in grades)
    retrieved = sum(g > 0 for g in top)
    reciprocal = next((1 / i for i, g in enumerate(top, 1) if g > 0), 0.0)
    def dcg(values):
        return sum((2**g - 1) / math.log2(i + 1) for i, g in enumerate(values, 1))
    best = dcg(sorted(grades, reverse=True)[:top_k])
    return {"precision_at_k": round(retrieved / top_k, 4),
            "recall_at_k": round(retrieved / relevant, 4) if relevant else None,
            "mrr_at_k": round(reciprocal, 4),
            "ndcg_at_k": round(dcg(top) / best, 4) if best else None}


def evaluate_case(case: dict, top_k: int = 5) -> dict:
    passages = case.get("passages", [])
    if not passages or any("text" not in p or "relevance" not in p for p in passages):
        raise ValueError("each passage must contain text and reviewer-assigned relevance (0, 1, or 2)")
    if any(p["relevance"] not in (0, 1, 2) for p in passages):
        raise ValueError("relevance grades must be 0, 1, or 2")
    if not case.get("query"):
        raise ValueError("query is required")
    rows = [SimpleNamespace(**p, rerank_score=None) for p in passages]
    # Inputs come directly from Bedrock in their original retrieval order.
    reranked = rerank_passages(case["query"], rows, mode="lexical")
    return {"query": case["query"], "candidates": len(rows), "top_k": top_k,
            "bedrock_order": metrics(rows, top_k), "lexical_cosine_rerank": metrics(reranked, top_k),
            "bedrock_top_ids": [getattr(p, "s3_key", None) for p in rows[:top_k]],
            "reranked_top_ids": [getattr(p, "s3_key", None) for p in reranked[:top_k]]}


def read_cases(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("evaluation file is empty")
    if text.startswith("["):
        cases = json.loads(text)
    elif text.startswith("{") and "\n" not in text:
        cases = [json.loads(text)]
    else:
        cases = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(cases, list):
        raise ValueError("expected a JSON list or JSONL records")
    return cases


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.top_k < 1:
        parser.error("--top-k must be positive")
    results = [evaluate_case(c, args.top_k) for c in read_cases(args.input)]
    summary = {"cases": len(results), "top_k": args.top_k, "results": results,
               "note": "Manual relevance grades required. Measures candidate ordering, not factual correctness or KB-wide recall."}
    output = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
