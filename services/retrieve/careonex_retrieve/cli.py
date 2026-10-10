"""`careonex-retrieve serve` (HTTP API) and `careonex-retrieve query` (one-shot, writes the example file)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from botocore.exceptions import ClientError, NoCredentialsError, TokenRetrievalError

from careonex_retrieve import config
from careonex_retrieve.retriever import build_filter, retrieve, _passage_key
from careonex_retrieve.batch_compare import (load_questions, write_json, ensure_matching_report, reuse_labels, load_seeds)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("careonex_retrieve.app:app", host="0.0.0.0", port=args.port, log_level="info")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    runtime = config.session().client("bedrock-agent-runtime")
    flt = build_filter(args.program, args.year, args.jurisdiction, args.source_id)
    result = retrieve(runtime, config.knowledge_base_id(), args.query, args.top_k, flt, latest_only=not args.all_years, search_mode=args.search_mode, program_hint=args.program,
                      include_parent_context=getattr(args, "parent_context", False))
    out = result.as_dict()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if args.save:
        p = Path(args.data_dir) / "retrieval-example.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print(f"saved {p}", file=sys.stderr)
    return 0 if result.passages else 1


def _comparison_report(runtime, kb_id: str, query: str, program: str | None,
                       year: int | None, jurisdiction: str | None, source_id: str | None,
                       top_k: int, candidate_k: int, candidate_mode: str = "expanded") -> dict:
    """Use exactly the same retrieval configuration for individual and batch A/B runs."""
    flt = build_filter(program, year, jurisdiction, source_id)
    runs: dict = {}
    pool: dict = {}
    for mode in ("baseline", candidate_mode):
        result = retrieve(runtime, kb_id, query, top_k=candidate_k,
                          flt=flt, search_mode=mode, program_hint=program)
        ids: list[str] = []
        for passage in result.passages:
            key = _passage_key(passage)
            ids.append(key)
            if key not in pool:
                pool[key] = {"id": key, "s3_key": passage.s3_key,
                             "title": passage.title, "source_url": passage.source_url,
                             "text": passage.text, "relevance": None}
        runs[mode] = {"ids": ids, "latency_ms": result.latency_ms,
                      "queries_used": result.queries_used, "top_ids": ids[:top_k]}
    return {"query": query, "knowledge_base_id": kb_id,
            "top_k": top_k, "candidate_k": candidate_k, "candidate_mode": candidate_mode,
            "runs": runs, "candidate_pool": list(pool.values()),
            "note": "Manually grade every candidate_pool.relevance as 0/1/2. "
                    "Existing grades are reused only for identical passages when requested."}


def cmd_compare(args: argparse.Namespace) -> int:
    """Read-only A/B on one KB, exporting a pool for human grading."""
    runtime = config.session().client("bedrock-agent-runtime")
    report = _comparison_report(runtime, config.knowledge_base_id(), args.query,
                                args.program, args.year, args.jurisdiction, args.source_id,
                                args.top_k, args.candidate_k, getattr(args, "candidate_mode", "expanded"))
    write_json(args.output, report)
    print(json.dumps({"saved": str(args.output), "candidate_pool_size": len(report["candidate_pool"]),
                      "runs": {name: {"latency_ms": run["latency_ms"],
                                      "queries_used": run["queries_used"],
                                      "top_ids": run["top_ids"]}
                               for name, run in report["runs"].items()},
                      "next": "Review candidate_pool and grade relevance as 0/1/2 before evaluating."},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_batch_compare(args: argparse.Namespace) -> int:
    """Run a reproducible question set; save per case and resume without erasing grades."""
    questions = load_questions(args.questions)
    if args.limit is not None:
        questions = questions[:args.limit]
    seeds = load_seeds(args.seed_dir)
    kb_id = config.knowledge_base_id()
    candidate_mode = getattr(args, "candidate_mode", "expanded")
    cases_dir = args.output_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    completed = skipped = seed_grades = 0
    status: list[dict] = []
    for index, case in enumerate(questions, 1):
        report_path = cases_dir / (case["id"] + ".json")
        if report_path.exists() and not args.force:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            ensure_matching_report(report, case, kb_id, args.top_k, args.candidate_k, candidate_mode)
            skipped += 1
            print(f"[{index}/{len(questions)}] skipped existing {case['id']}", file=sys.stderr)
        else:
            # The same client can be reused; lazy initialization lets a fully cached
            # batch be evaluated even if the AWS SSO token has since expired.
            if completed == 0:
                runtime = config.session().client("bedrock-agent-runtime")
            report = _comparison_report(runtime, kb_id, case["query"],
                                        case.get("program"), case.get("year"),
                                        case.get("jurisdiction"), case.get("source_id"),
                                        args.top_k, args.candidate_k, candidate_mode)
            report["case_id"] = case["id"]
            seed_grades += reuse_labels(report, seeds)
            write_json(report_path, report)
            completed += 1
            print(f"[{index}/{len(questions)}] saved {case['id']} "
                  f"({len(report['candidate_pool'])} passages, "
                  f"{report['runs']['baseline']['latency_ms']}ms/"
                  f"{report['runs'][candidate_mode]['latency_ms']}ms)", file=sys.stderr)
        status.append({"id": case["id"], "query": case["query"], "program": case.get("program"),
                       "file": f"cases/{case['id']}.json"})
        # Save an intermediate manifest as well, so Ctrl+C or expired SSO won't
        # destroy completed work. Rerunning without --force skips saved cases.
        manifest = {"version": 1, "knowledge_base_id": kb_id,
                    "top_k": args.top_k, "candidate_k": args.candidate_k,
                    "candidate_mode": candidate_mode, "source_questions": str(args.questions), "cases": status,
                    "note": "Reports are ungraded except for exact-match reviewed seed labels. "
                            "The recall denominator is the candidate union per question."}
        write_json(args.output_dir / "manifest.json", manifest)
    print(json.dumps({"questions_selected": len(questions), "new_reports": completed,
                      "skipped_existing": skipped, "reused_verified_labels": seed_grades,
                      "manifest": str(args.output_dir / 'manifest.json'),
                      "next": "Review missing relevance labels in cases/*.json; run "
                              "python -m careonex_retrieve.batch_eval --dir <output-dir>"},
                     indent=2))
    return 0

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="careonex-retrieve", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve", help="run the HTTP API")
    s.add_argument("--port", type=int, default=config.PORT)
    s.set_defaults(fn=cmd_serve)
    q = sub.add_parser("query", help="one retrieval, printed as JSON (the Milestone 2 example)")
    q.add_argument("query")
    q.add_argument("--program")
    q.add_argument("--year", type=int)
    q.add_argument("--jurisdiction")
    q.add_argument("--source-id")
    q.add_argument("--top-k", type=int, default=config.DEFAULT_TOP_K)
    q.add_argument("--search-mode", choices=("baseline", "expanded", "intent", "feedback"), default=None,
                   help="baseline (default); expanded (legacy Medicaid rules); intent (LLM); feedback (model-free search feedback)")
    q.add_argument("--parent-context", action="store_true", help="staging only: fetch linked parent text after ranking")
    q.add_argument("--all-years", action="store_true", help="also return passages whose figures are superseded by a newer year")
    q.add_argument("--save", action="store_true", help="also write data/retrieval-example.json")
    q.add_argument("--data-dir", default=config.DATA_DIR)
    q.set_defaults(fn=cmd_query)
    c = sub.add_parser("compare", help="A/B compare baseline vs selected query expansion mode")
    c.add_argument("--candidate-mode", choices=("expanded", "intent", "feedback"), default="expanded",
                   help="expanded=legacy Medicaid rules; intent=Bedrock LLM; feedback=model-free")
    c.add_argument("query")
    c.add_argument("--program")
    c.add_argument("--year", type=int)
    c.add_argument("--jurisdiction")
    c.add_argument("--source-id")
    c.add_argument("--top-k", type=int, default=3)
    c.add_argument("--candidate-k", type=int, default=10)
    c.add_argument("--output", type=Path, default=Path("evaluation/medicaid-ab.json"))
    c.set_defaults(fn=cmd_compare)
    b = sub.add_parser("batch-compare", help="read-only A/B on a JSON list of realistic caller questions")
    b.add_argument("--candidate-mode", choices=("expanded", "intent", "feedback"), default="expanded",
                   help="expanded=legacy benchmark (default); intent=LLM; feedback=no model permissions required")
    b.add_argument("--questions", type=Path, required=True)
    b.add_argument("--output-dir", type=Path, required=True)
    b.add_argument("--top-k", type=int, default=3)
    b.add_argument("--candidate-k", type=int, default=10)
    b.add_argument("--limit", type=int, help="run the first N questions (use 3 for a low-cost smoke test)")
    b.add_argument("--seed-dir", type=Path, help="reuse pre-reviewed grades ONLY if query, KB, passage ID and text match exactly")
    b.add_argument("--force", action="store_true", help="overwrite previous reports and their relevance grades")
    b.set_defaults(fn=cmd_batch_compare)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command in ("compare", "batch-compare") and (args.top_k < 1 or args.candidate_k < args.top_k):
        print("--candidate-k must be >= --top-k >= 1", file=sys.stderr)
        sys.exit(2)
    if args.command == "batch-compare" and args.limit is not None and args.limit < 1:
        print("--limit must be >= 1", file=sys.stderr)
        sys.exit(2)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("botocore").setLevel(logging.ERROR)
    try:
        sys.exit(args.fn(args))
    except (NoCredentialsError, TokenRetrievalError) as exc:
        print(f"AWS credentials not available: {exc}", file=sys.stderr)
        sys.exit(3)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("AccessDenied", "AccessDeniedException"):
            print(f"AccessDenied: {exc}\nThe AC215 permission set needs bedrock:Retrieve on the knowledge base.", file=sys.stderr)
            sys.exit(5)
        raise
