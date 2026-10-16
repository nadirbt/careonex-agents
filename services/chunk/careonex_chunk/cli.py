"""`careonex-chunk run`: text/ -> chunks/ in the knowledge bucket."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from botocore.exceptions import ClientError, NoCredentialsError, TokenRetrievalError

from careonex_chunk import config
from careonex_chunk.writer import chunk_all


def cmd_run(args: argparse.Namespace) -> int:
    s3 = config.session().client("s3")
    summary = chunk_all(
        s3, config.bucket_name(), snapshot_id=args.snapshot_id,
        only=set(args.only) if args.only else None, force=args.force, dry_run=args.dry_run, data_dir=args.data_dir,
    )
    print(json.dumps({"snapshot_id": summary.snapshot_id, "bucket": summary.bucket, "counts": summary.counts, "total_chunks": summary.total_chunks}, indent=2))
    return 1 if summary.counts.get("failed") else 0


def cmd_preview(args: argparse.Namespace) -> int:
    from careonex_chunk.chunker import chunk_markdown

    md = Path(args.path).read_text(encoding="utf-8")
    for c in chunk_markdown(md, config.TARGET_CHARS, config.MAX_CHARS, config.MIN_CHARS, config.OVERLAP_CHARS):
        print(f"--- {c.chunk_id}  {c.chars} chars  [{' > '.join(c.heading_path)}]")
        print(c.text if args.full else c.text[:300].replace("\n", " ") + (" ..." if c.chars > 300 else ""))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="careonex-chunk", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="chunk every text/ document whose chunks are missing or stale")
    r.add_argument("--snapshot-id")
    r.add_argument("--only", nargs="*", help="substrings of text keys to restrict to")
    r.add_argument("--force", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--data-dir", default=config.DATA_DIR)
    r.set_defaults(fn=cmd_run)
    v = sub.add_parser("preview", help="chunk a local Markdown file and print the pieces")
    v.add_argument("path")
    v.add_argument("--full", action="store_true")
    v.set_defaults(fn=cmd_preview)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("botocore").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    try:
        sys.exit(args.fn(args))
    except (NoCredentialsError, TokenRetrievalError) as exc:
        print(f"AWS credentials not available: {exc}\nRun `aws sso login --profile careonex-team` on the host.", file=sys.stderr)
        sys.exit(3)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("AccessDenied", "AccessDeniedException"):
            print(f"AccessDenied: {exc}\nThe AC215 permission set must include the S3 statement (TEAM_SETUP.md).", file=sys.stderr)
            sys.exit(5)
        raise
