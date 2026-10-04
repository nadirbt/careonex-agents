"""`careonex-kb sync`: provision S3 Vectors + Bedrock Knowledge Base and run ingestion."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from botocore.exceptions import ClientError, NoCredentialsError, TokenRetrievalError

from careonex_kb import config
from careonex_kb.provision import MissingServiceRole, sync


def cmd_sync(args: argparse.Namespace) -> int:
    state = sync(config.session(), wait=not args.no_wait, skip_ingest=args.skip_ingest)
    out = Path(args.data_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "knowledge-base.json").write_text(json.dumps(state.as_dict(), indent=2))
    print(json.dumps(state.as_dict(), indent=2))
    if state.ingestion_status and state.ingestion_status != "COMPLETE" and not args.no_wait:
        return 1
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    sess = config.session()
    s3 = sess.client("s3")
    try:
        body = s3.get_object(Bucket=config.bucket_name(), Key=config.CONFIG_KEY)["Body"].read()
        print(body.decode())
        return 0
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            print("No knowledge base has been provisioned yet (config/knowledge-base.json missing). Run `sync`.", file=sys.stderr)
            return 2
        raise


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="careonex-kb", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sync", help="get-or-create vector bucket, index, knowledge base, data source; start ingestion")
    s.add_argument("--no-wait", action="store_true", help="start ingestion and return without polling")
    s.add_argument("--skip-ingest", action="store_true", help="provision only")
    s.add_argument("--data-dir", default=config.DATA_DIR)
    s.set_defaults(fn=cmd_sync)
    sub.add_parser("status", help="print config/knowledge-base.json from the bucket").set_defaults(fn=cmd_status)
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
    except MissingServiceRole as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(6)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("AccessDenied", "AccessDeniedException"):
            print(f"AccessDenied: {exc}\nThe AC215 permission set needs the s3vectors, bedrock knowledge-base and iam:PassRole statements (TEAM_SETUP.md).", file=sys.stderr)
            sys.exit(5)
        raise
