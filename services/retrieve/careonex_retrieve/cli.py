"""`careonex-retrieve serve` (HTTP API) and `careonex-retrieve query` (one-shot, writes the example file)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from botocore.exceptions import ClientError, NoCredentialsError, TokenRetrievalError

from careonex_retrieve import config
from careonex_retrieve.retriever import build_filter, retrieve


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("careonex_retrieve.app:app", host="0.0.0.0", port=args.port, log_level="info")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    runtime = config.session().client("bedrock-agent-runtime")
    flt = build_filter(args.program, args.year, args.jurisdiction, args.source_id)
    result = retrieve(runtime, config.knowledge_base_id(), args.query, args.top_k, flt, latest_only=not args.all_years)
    out = result.as_dict()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if args.save:
        p = Path(args.data_dir) / "retrieval-example.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print(f"saved {p}", file=sys.stderr)
    return 0 if result.passages else 1


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
    q.add_argument("--all-years", action="store_true", help="also return passages whose figures are superseded by a newer year")
    q.add_argument("--save", action="store_true", help="also write data/retrieval-example.json")
    q.add_argument("--data-dir", default=config.DATA_DIR)
    q.set_defaults(fn=cmd_query)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
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
