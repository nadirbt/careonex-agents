"""A/B benchmark for program-aware query expansion on a fixed AWS KB.

Generate a comparison with `careonex-retrieve compare ...`, manually grade
EVERY unique candidate in the candidate_pool as 0, 1, or 2, then run:
  python -m careonex_retrieve.expansion_eval --input evaluation/medicaid-ab.json

Metrics compare the two runs against the SAME judged union of candidates,
not against the entire KB or the factual correctness of a spoken answer.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def evaluate_comparison(report: dict) -> dict:
    pool = report["candidate_pool"]
    labels = {p["id"]: p.get("relevance") for p in pool}
    if len(labels) != len(pool):
        raise ValueError("candidate_pool contains duplicate IDs")
    missing = [k for k, grade in labels.items() if grade not in (0, 1, 2) or type(grade) is not int]
    if missing:
        return {"ready": False, "ungraded": len(missing), "total_candidates": len(labels),
                "note": "Grade every candidate_pool.relevance as 0, 1, or 2 before calculating metrics."}
    top_k = int(report["top_k"])
    if top_k < 1:
        raise ValueError("top_k must be positive")
    all_grades = list(labels.values())
    relevant = sum(g > 0 for g in all_grades)

    def dcg(grades):
        return sum((2**g - 1) / math.log2(i + 1) for i, g in enumerate(grades, 1))

    ideal = dcg(sorted(all_grades, reverse=True)[:top_k])
    results = {}
    for name, run in report["runs"].items():
        ids = run["ids"]
        if len(set(ids)) != len(ids) or any(k not in labels for k in ids):
            raise ValueError(f"Invalid or duplicate candidate ID in {name}")
        grades = [labels[k] for k in ids[:top_k]]
        hit_count = sum(g > 0 for g in grades)
        reciprocal = next((1 / idx for idx, g in enumerate(grades, 1) if g > 0), 0.0)
        results[name] = {
            "precision_at_k": round(hit_count / top_k, 4),
            "recall_at_k": round(hit_count / relevant, 4) if relevant else None,
            "mrr_at_k": round(reciprocal, 4),
            "ndcg_at_k": round(dcg(grades) / ideal, 4) if ideal else None,
            "latency_ms": run["latency_ms"],
            "top_ids": ids[:top_k],
        }
    return {"ready": True, "top_k": top_k, "judged_candidates": len(labels),
            "relevant_in_union": relevant, "results": results,
            "note": "Recall denominator is relevant passages in the union of returned candidates, not the full KB. These are passage rankings, not answer-grounding scores."}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = evaluate_comparison(json.loads(args.input.read_text(encoding="utf-8")))
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(payload)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if not report["ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
