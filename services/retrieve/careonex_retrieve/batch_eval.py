"""Summarize baseline versus either the legacy or the intent-aware RAG benchmark.

All candidate passages must have human-reviewed grades (0/1/2) for a
case to count toward ranking metrics. Incomplete cases are reported but
NEVER silently counted as zero. Recall is limited to the candidate union.

  python -m careonex_retrieve.batch_eval --dir evaluation/batch_runs
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean, median

from careonex_retrieve.expansion_eval import evaluate_comparison

METRICS = ("precision_at_k", "recall_at_k", "mrr_at_k", "ndcg_at_k")


def _aggregate(ready: list[dict], candidate_mode: str = "expanded") -> dict:
    def mean_or_none(values):
        return round(mean(values), 4) if values else None

    summary = {}
    for mode in ("baseline", candidate_mode):
        rows = [case["metrics"][mode] for case in ready]
        per_metric = {metric: mean_or_none([r[metric] for r in rows if r[metric] is not None])
                      for metric in METRICS}
        latencies = sorted(r["latency_ms"] for r in rows)
        summary[mode] = {**per_metric,
                         "mean_latency_ms": round(mean(latencies), 1) if latencies else None,
                         "median_latency_ms": round(median(latencies), 1) if latencies else None,
                         "p95_latency_ms": latencies[math.ceil(len(latencies) * .95) - 1] if latencies else None}
    return summary


def evaluate_batch(directory: Path) -> dict:
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest.get("cases"), list):
        raise ValueError("Expected manifest with a cases list")
    candidate_mode = manifest.get("candidate_mode", "expanded")
    if candidate_mode not in ("expanded", "intent"):
        raise ValueError("Unsupported candidate mode in manifest")
    seen: set[str] = set()
    cases: list[dict] = []
    for item in manifest["cases"]:
        case_id = item["id"]
        if case_id in seen:
            raise ValueError(f"Duplicate case ID in manifest: {case_id}")
        seen.add(case_id)
        # Constrain file names to the id we validated at collection time.
        expected = f"cases/{case_id}.json"
        if item.get("file") != expected or '/' in case_id or '\\' in case_id or case_id.startswith('.'):
            raise ValueError(f"Unsafe or unexpected case file for {case_id!r}")
        path = directory / expected
        if not path.is_file():
            cases.append({"id": case_id, "status": "missing_file"})
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        if (report.get("query") != item["query"] or
                report.get("knowledge_base_id") != manifest["knowledge_base_id"] or
                report.get("top_k") != manifest["top_k"] or
                report.get("candidate_k") != manifest["candidate_k"]):
            raise ValueError(f"Stored report for {case_id} doesn't match manifest")
        evaluated = evaluate_comparison(report)
        expanded_queries = report["runs"][candidate_mode].get("queries_used", [])
        if not evaluated["ready"]:
            cases.append({"id": case_id, "status": "needs_grading",
                          "ungraded": evaluated["ungraded"],
                          "candidates": evaluated["total_candidates"],
                          "expansion_triggered": len(expanded_queries) > 1})
        else:
            cases.append({"id": case_id, "status": "graded",
                          "candidates": evaluated["judged_candidates"],
                          "relevant_in_union": evaluated["relevant_in_union"],
                          "expansion_triggered": len(expanded_queries) > 1,
                          "metrics": evaluated["results"]})
    ready = [c for c in cases if c["status"] == "graded"]
    return {
        "candidate_mode": candidate_mode,
        "questions_in_manifest": len(cases),
        "graded_questions": len(ready),
        "ungraded_questions": sum(c["status"] == "needs_grading" for c in cases),
        "missing_files": sum(c["status"] == "missing_file" for c in cases),
        "expanded_query_cases_in_manifest": sum(c.get("expansion_triggered", False) for c in cases),
        "summary_all_graded": _aggregate(ready, candidate_mode),
        "summary_expansion_triggered_only": _aggregate([c for c in ready if c["expansion_triggered"]], candidate_mode),
        "cases": cases,
        "note": "Macro averages only include completely graded cases. Recall@k denominator is the "
                "judged candidate union per query (not all relevant passages in the KB). "
                "Scores evaluate retrieval rankings, not factual correctness of generated answers. "
                "Latency comes from single observations per query/mode and is not a robust speed benchmark."
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-complete", action="store_true", help="exit nonzero when grading is incomplete")
    args = parser.parse_args(argv)
    result = evaluate_batch(args.dir)
    output = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    if args.require_complete and result["graded_questions"] != result["questions_in_manifest"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
