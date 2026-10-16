"""A/B/C retrieval evaluation across three ISOLATED Bedrock Knowledge Bases.

Collect candidates with identical queries/settings; require independent passage
judgments before reporting ranking quality. This script cannot create a KB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from pathlib import Path

VARIANTS = ("legacy", "section", "hierarchical")


def _parent_context(s3, child_uri: str, parent_id: str) -> str:
    """Use child S3 location and parent_id to load NON-INDEXED parent context."""
    if not child_uri or not child_uri.startswith("s3://") or not parent_id:
        return ""
    bucket, sep, key = child_uri[5:].partition("/")
    if not sep or "/hierarchical/chunks/" not in key:
        return ""
    folder = key.rsplit("/", 1)[0]
    parent_folder = folder.replace("/hierarchical/chunks/", "/hierarchical/parents/", 1)
    parent_key = f"{parent_folder}/{parent_id}.md"
    if not parent_key.startswith("experiments/chunking/"):
        raise ValueError("parent lookup escaped staging prefix")
    return s3.get_object(Bucket=bucket, Key=parent_key)["Body"].read().decode("utf-8")


def collect(questions_path: Path, kb_ids: dict[str, str], top_k: int,
            output: Path, include_parent_context: bool = False) -> dict:
    import boto3
    runtime = boto3.client("bedrock-agent-runtime")
    s3 = boto3.client("s3") if include_parent_context else None
    obj = json.loads(questions_path.read_text(encoding="utf8"))
    questions = obj["questions"] if isinstance(obj, dict) else obj
    cases = []
    for n, q in enumerate(questions, 1):
        runs = {}
        for variant in VARIANTS:
            # This is the pure query baseline: no query expansion, filters, or reranking.
            start = time.perf_counter()
            data = runtime.retrieve(
                knowledgeBaseId=kb_ids[variant],
                retrievalQuery={"text": q["query"]},
                retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}},
            )
            passages = []
            for rank, hit in enumerate(data.get("retrievalResults", []), 1):
                passage_text = (hit.get("content") or {}).get("text", "")
                uri = ((hit.get("location") or {}).get("s3Location") or {}).get("uri")
                md = hit.get("metadata") or {}
                passage = {"rank": rank, "text": passage_text,
                           "sha256": hashlib.sha256(passage_text.encode()).hexdigest(),
                           "s3_uri": uri, "metadata": md,
                           "bedrock_score": hit.get("score"), "relevance": None}
                if include_parent_context and variant == "hierarchical":
                    passage["parent_text"] = _parent_context(s3, uri, md.get("parent_id", ""))
                passages.append(passage)
            runs[variant] = {"latency_ms": round((time.perf_counter() - start) * 1000),
                             "kb_id": kb_ids[variant], "passages": passages}
        cases.append({"id": q["id"], "query": q["query"], "runs": runs})
        print(f"[{n}/{len(questions)}] {q['id']} completed", flush=True)
    payload = {"top_k": top_k, "questions": len(cases), "cases": cases,
               "grading": "Set relevance=0,1,2 for EVERY passage, independently of strategy. "
                          "Run score command only after human review.",
               "limitation": "Compares retrieval, not voice accuracy; no filters or query expansion. "
                             "Optional parent_text is extra context, not a separately ranked hit."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    return payload


def score(input_path: Path, output_path: Path | None = None) -> dict:
    obj = json.loads(input_path.read_text(encoding="utf8"))
    k = int(obj["top_k"])
    all_metrics = {v: [] for v in VARIANTS}
    results = []
    for case in obj["cases"]:
        pool = {}
        for strategy in VARIANTS:
            for p in case["runs"][strategy]["passages"]:
                g = p.get("relevance")
                if type(g) is not int or g not in (0, 1, 2):
                    raise ValueError(f"Unreviewed passage: {case['id']} {strategy} rank {p['rank']}")
                sha = p["sha256"]
                if sha in pool and pool[sha] != g:
                    raise ValueError(f"Conflicting grades for identical passage: {case['id']} {sha[:8]}")
                pool[sha] = g
        ideal = sorted(pool.values(), reverse=True)[:k]
        def dcg(grades):
            return sum((2 ** g - 1) / math.log2(rank + 1) for rank, g in enumerate(grades, 1))
        ideal_dcg = dcg(ideal)
        by_strategy = {}
        for strategy in VARIANTS:
            run = case["runs"][strategy]
            grades = [p["relevance"] for p in run["passages"][:k]]
            # Pad absent results: a system that returns fewer than k is penalized.
            grades += [0] * (k - len(grades))
            first = next((1 / i for i, g in enumerate(grades, 1) if g > 0), 0.0)
            m = {"precision_at_k": sum(g > 0 for g in grades) / k,
                 "mrr_at_k": first, "ndcg_at_k": dcg(grades) / ideal_dcg if ideal_dcg else None,
                 "latency_ms": run["latency_ms"]}
            by_strategy[strategy] = m
            all_metrics[strategy].append(m)
        results.append({"id": case["id"], "metrics": by_strategy})
    averages = {}
    for strategy, metrics in all_metrics.items():
        averages[strategy] = {key: round(statistics.mean(v[key] for v in metrics if v[key] is not None), 4)
                              for key in ("precision_at_k", "mrr_at_k", "ndcg_at_k", "latency_ms")
                              if any(v[key] is not None for v in metrics)}
    out = {"questions": len(results), "top_k": k, "averages": averages, "cases": results,
           "warning": "Relevance grades are manual. Pooled NDCG uses unique retrieved passage texts "
                      "as the ideal candidate set; this is not exhaustive KB recall."}
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf8")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--questions", type=Path, default=Path("evaluation/batch_questions.json"))
    c.add_argument("--legacy-kb", required=True)
    c.add_argument("--section-kb", required=True)
    c.add_argument("--hierarchical-kb", required=True)
    c.add_argument("--top-k", type=int, default=5)
    c.add_argument("--include-parent-context", action="store_true", help="fetch hierarchical parent text from staged S3 after retrieving children")
    c.add_argument("--output", type=Path, required=True)
    s = sub.add_parser("score")
    s.add_argument("--input", type=Path, required=True)
    s.add_argument("--output", type=Path)
    args = p.parse_args(argv)
    if args.command == "collect":
        if args.top_k < 1:
            p.error("--top-k must be positive")
        result = collect(args.questions, {"legacy": args.legacy_kb, "section": args.section_kb,
                                          "hierarchical": args.hierarchical_kb}, args.top_k, args.output,
                         include_parent_context=args.include_parent_context)
        print(f"Saved {len(result['cases'])} ungraded cases; grade relevance before scoring.")
    else:
        print(json.dumps(score(args.input, args.output), indent=2))


if __name__ == "__main__":
    main()
