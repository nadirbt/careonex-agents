"""Opt-in live retrieval evaluation, no AWS writes and no simulated successes.

Run *after* verifying your AWS SSO role and starting the retrieval API:
  uv run --directory services/retrieve python -m careonex_retrieve.evaluate --repeats 3

Prints one JSON report suitable for review. A hit tests *retrieval of evidence*, NOT
voice-answer faithfulness or model correctness; those need a separate human review.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    id: str
    query: str
    program: str
    evidence: str


# A deliberately small, versioned smoke suite. These strings are taken from
# the team's curated 2026 source summary and should be re-reviewed each year.
CASES = (
    Case("jacc_income", "2026 JACC monthly income limit for a single person", "JACC", "$4,855"),
    Case("jacc_assets", "2026 JACC asset resource limit individual", "JACC", "$40,000"),
    Case("respite_income", "2026 Statewide Respite individual income limit", "respite", "$2,982"),
    Case("aadsp_income", "2025 AADSP annual individual income limit", "AADSP", "$50,256"),
    Case("pace_age", "minimum age for PACE in New Jersey", "PACE", "55"),
    Case("oaa_means", "Older Americans Act OAA means test financial eligibility", "OAA", "means test"),
)


def _fetch(base_url: str, case: Case, timeout: float) -> dict:
    req = urllib.request.Request(
        base_url.rstrip("/") + "/retrieve",
        data=json.dumps({"query": case.query, "program": case.program, "top_k": 6}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - operator-provided URL
        return json.load(response)


def evaluate(base_url: str, repeats: int = 3, timeout: float = 15.0) -> dict:
    results: list[dict] = []
    for case in CASES:
        for repeat in range(1, repeats + 1):
            t0 = time.perf_counter()
            entry: dict = {"case": case.id, "repeat": repeat, "evidence_expected": case.evidence}
            try:
                data = _fetch(base_url, case, timeout)
                passages = data.get("passages", [])
                entry.update(
                    found=any(case.evidence.casefold() in p.get("text", "").casefold() for p in passages),
                    traceable=any(p.get("source_url", "").startswith("https://") and p.get("title") for p in passages),
                    hits=len(passages),
                    latency_ms=int(data.get("latency_ms") or 0),
                )
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
                entry.update(found=False, traceable=False, error=f"{type(exc).__name__}: {exc}")
            entry["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
            results.append(entry)
    times = [r["elapsed_ms"] for r in results]
    return {
        "suite": "careonex-rag-retrieval-smoke-v1",
        "checks": len(results),
        "evidence_hit_rate": round(sum(bool(r["found"]) for r in results) / len(results), 3),
        "traceable_rate": round(sum(bool(r["traceable"]) for r in results) / len(results), 3),
        "p95_elapsed_ms": sorted(times)[max(0, int(len(times) * 0.95 + 0.99999) - 1)],
        "median_elapsed_ms": round(statistics.median(times)),
        "all_passed": all(r["found"] and r["traceable"] for r in results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="CareOneX: read-only RAG retrieval evaluation")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    result = evaluate(args.url, repeats=args.repeats, timeout=args.timeout)
    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    print(output)
    raise SystemExit(0 if result["all_passed"] else 1)


if __name__ == "__main__":
    main()
