"""`careonex-extract run`: raw/ -> text/ in the knowledge bucket."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from botocore.exceptions import ClientError, NoCredentialsError, TokenRetrievalError

from careonex_extract import config
from careonex_extract.extract import extract_all


def cmd_run(args: argparse.Namespace) -> int:
    s3 = config.session().client("s3")
    summary = extract_all(
        s3,
        config.bucket_name(),
        snapshot_id=args.snapshot_id,
        only=set(args.only) if args.only else None,
        force=args.force,
        dry_run=args.dry_run,
        data_dir=args.data_dir,
    )
    changed = [i.text_key for i in summary.items if i.text_changed]
    print(json.dumps({"snapshot_id": summary.snapshot_id, "bucket": summary.bucket, "counts": summary.counts, "text_changed": changed}, indent=2))
    return 1 if summary.counts.get("failed") else 0


def cmd_convert(args: argparse.Namespace) -> int:
    """Convert one local file and print the Markdown (for eyeballing extractor quality)."""
    from pathlib import Path

    from careonex_extract.convert import to_markdown

    p = Path(args.path)
    sys.stdout.write(to_markdown(p.read_bytes(), p.suffix.lstrip("."), p.name))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="careonex-extract", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="extract every raw/ document that has no up-to-date text/ counterpart")
    r.add_argument("--snapshot-id", help="defaults to today's date")
    r.add_argument("--only", nargs="*", help="substrings of raw keys to restrict to")
    r.add_argument("--force", action="store_true", help="re-extract even if raw bytes and extractor are unchanged")
    r.add_argument("--dry-run", action="store_true", help="convert and hash, write nothing")
    r.add_argument("--data-dir", default=config.DATA_DIR)
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("convert", help="convert a local PDF/HTML file to Markdown on stdout")
    c.add_argument("path")
    c.set_defaults(fn=cmd_convert)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("botocore", "urllib3", "trafilatura", "pymupdf4llm"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    try:
        sys.exit(args.fn(args))
    except (NoCredentialsError, TokenRetrievalError) as exc:
        print(f"AWS credentials not available: {exc}\nRun `aws sso login --profile careonex-team` on the host.", file=sys.stderr)
        sys.exit(3)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("AccessDenied", "AccessDeniedException"):
            print(f"AccessDenied: {exc}\nThe AC215 permission set must be reprovisioned with the S3 statement (TEAM_SETUP.md).", file=sys.stderr)
            sys.exit(5)
        raise
