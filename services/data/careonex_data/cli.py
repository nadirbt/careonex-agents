"""Command-line entry point: `careonex-data <command>` (also `python -m careonex_data`)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from botocore.exceptions import ClientError, NoCredentialsError, TokenRetrievalError

from careonex_data import config
from careonex_data.buckets import BucketNotOurs, describe, ensure_bucket, probe
from careonex_data.ingest import ingest


def _s3():
    return config.session().client("s3")


def cmd_whoami(_: argparse.Namespace) -> int:
    print(json.dumps({"account": config.account_id(), "arn": config.caller_arn(), "region": config.REGION}, indent=2))
    return 0


def cmd_ensure_buckets(args: argparse.Namespace) -> int:
    s3 = _s3()
    results = []
    for spec in config.BUCKETS.values():
        name = config.bucket_name(spec)
        st = ensure_bucket(s3, spec, name, config.REGION, enforce=args.enforce)
        results.append(st.as_dict())
    print(json.dumps(results, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    s3 = _s3()
    out = []
    for spec in config.BUCKETS.values():
        name = config.bucket_name(spec)
        state = probe(s3, name)
        if state == "owned":
            out.append(describe(s3, spec, name).as_dict())
        else:
            out.append({"key": spec.key, "name": name, "exists": state != "missing", "accessible": False, "state": state})
    print(json.dumps(out, indent=2))
    return 0


def cmd_ls(args: argparse.Namespace) -> int:
    s3 = _s3()
    bucket = config.bucket_name(config.BUCKETS[args.bucket])
    paginator = s3.get_paginator("list_objects_v2")
    n = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=args.prefix or ""):
        for obj in page.get("Contents", []):
            print(f"{obj['Size']:>10}  {obj['LastModified']:%Y-%m-%d %H:%M}  {obj['Key']}")
            n += 1
    print(f"{n} objects in s3://{bucket}/{args.prefix or ''}", file=sys.stderr)
    return 0


def cmd_put(args: argparse.Namespace) -> int:
    s3 = _s3()
    bucket = config.bucket_name(config.BUCKETS[args.bucket])
    s3.upload_file(args.local, bucket, args.key)
    print(f"s3://{bucket}/{args.key}")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    s3 = _s3()
    bucket = config.bucket_name(config.BUCKETS[args.bucket])
    Path(args.local).parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, args.key, args.local)
    print(args.local)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    s3 = _s3()
    bucket = config.bucket_name(config.KNOWLEDGE)
    if not args.dry_run and probe(s3, bucket) != "owned":
        print(f"Knowledge bucket {bucket} is not available. Run `ensure-buckets` first.", file=sys.stderr)
        return 2
    manifest = ingest(
        s3,
        bucket,
        catalog_path=args.catalog,
        snapshot_id=args.snapshot_id,
        local_dir=args.local_dir,
        data_dir=args.data_dir,
        dry_run=args.dry_run,
        only=set(args.only) if args.only else None,
    )
    print(json.dumps({"snapshot_id": manifest.snapshot_id, "bucket": bucket, "counts": manifest.counts}, indent=2))
    return 1 if manifest.counts.get("failed") else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="careonex-data", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("whoami", help="print the AWS identity the container is using").set_defaults(fn=cmd_whoami)

    e = sub.add_parser("ensure-buckets", help="create missing buckets with baseline settings; leave existing ones alone")
    e.add_argument("--enforce", action="store_true", help="re-apply versioning/encryption/public-access-block on existing buckets")
    e.set_defaults(fn=cmd_ensure_buckets)

    sub.add_parser("status", help="show whether each bucket exists and its settings").set_defaults(fn=cmd_status)

    for name, fn, help_ in (("ls", cmd_ls, "list objects"),):
        l = sub.add_parser(name, help=help_)
        l.add_argument("prefix", nargs="?", default="")
        l.add_argument("--bucket", choices=list(config.BUCKETS), default="knowledge")
        l.set_defaults(fn=fn)

    pu = sub.add_parser("put", help="upload one local file")
    pu.add_argument("local")
    pu.add_argument("key")
    pu.add_argument("--bucket", choices=list(config.BUCKETS), default="knowledge")
    pu.set_defaults(fn=cmd_put)

    g = sub.add_parser("get", help="download one object")
    g.add_argument("key")
    g.add_argument("local")
    g.add_argument("--bucket", choices=list(config.BUCKETS), default="knowledge")
    g.set_defaults(fn=cmd_get)

    i = sub.add_parser("ingest", help="fetch every catalog source into the knowledge bucket with metadata sidecars")
    i.add_argument("--catalog", default=config.CATALOG_PATH)
    i.add_argument("--snapshot-id", help="defaults to <today>-<catalog sha prefix>")
    i.add_argument("--local-dir", help="use already-downloaded files from here (e.g. the Drive mirror) instead of fetching")
    i.add_argument("--data-dir", default=config.DATA_DIR, help="where to write the local manifest copy")
    i.add_argument("--only", nargs="*", help="restrict to these source_ids or file names")
    i.add_argument("--dry-run", action="store_true", help="fetch and hash, upload nothing")
    i.set_defaults(fn=cmd_ingest)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("botocore").setLevel(logging.ERROR)  # its WARNING-level token traceback duplicates our message
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    try:
        sys.exit(args.fn(args))
    except (NoCredentialsError, TokenRetrievalError) as exc:
        print(
            f"AWS credentials not available: {exc}\n"
            "Run `aws sso login --profile careonex-team` on the host and make sure ~/.aws is mounted into the container.",
            file=sys.stderr,
        )
        sys.exit(3)
    except BucketNotOurs as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(4)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
            print(
                f"AccessDenied: {exc}\n"
                "The AC215 permission set only allows Nova Sonic. Bucket work needs the AC215-Data permission set "
                "(see services/data/README.md).",
                file=sys.stderr,
            )
            sys.exit(5)
        raise
