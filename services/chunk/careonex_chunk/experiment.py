"""Safe offline A/B/C chunk export and explicitly opted-in staging S3 upload.

Never modifies production `chunks/` or production Knowledge Base data. Staging
variants are stored under experiments/chunking/<run>/<strategy>/chunks/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from . import chunker, legacy_chunker
from .hierarchical import hierarchical_chunk_markdown
from .writer import SIDECAR_LIMIT_BYTES, sidecar_for

STRATEGIES = ("legacy", "section", "hierarchical")
_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{2,50}$")


def _staging_prefix(run: str, strategy: str) -> str:
    if not _ID.fullmatch(run) or strategy not in STRATEGIES:
        raise ValueError("invalid staging experiment ID or strategy")
    return f"experiments/chunking/{run}/{strategy}"


def _json(path: Path, content: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_variant(base: Path, relative: Path, md: str, strategy: str, attrs: dict,
                   run: str, parent_max: int, child_target: int) -> dict:
    if strategy == "legacy":
        chunks = legacy_chunker.chunk_markdown(md)
        parents = []
        linked = [(c, "", "") for c in chunks]
    elif strategy == "section":
        chunks = chunker.chunk_markdown(md)
        parents = []
        linked = [(c, "", "") for c in chunks]
    else:
        hierarchy = hierarchical_chunk_markdown(md, parent_max_chars=parent_max,
                                                  child_target_chars=child_target)
        chunks = [item.chunk for item in hierarchy.children]
        parents = hierarchy.parents
        linked = [(item.chunk, item.parent_id, item.topic) for item in hierarchy.children]

    # Preserve subfolders, with a suffix-safe directory distinct for same filename stems.
    doc_dir = relative.with_suffix("")
    indexed_dir = base / "chunks" / doc_dir
    indexed_dir.mkdir(parents=True, exist_ok=True)
    for c, parent_id, topic in linked:
        dest = indexed_dir / f"{c.chunk_id}.md"
        dest.write_text(c.text, encoding="utf-8")
        sidecar = sidecar_for(c, attrs, f"text/{relative.as_posix()}", len(chunks))
        sidecar_attrs = sidecar["metadataAttributes"]
        # Metadata values must remain small for Bedrock KB sidecars.
        sidecar_attrs["strategy"] = strategy
        if parent_id:
            sidecar_attrs["parent_id"] = parent_id
        if topic:
            sidecar_attrs["topic"] = topic[:40]
        if len(json.dumps(sidecar, ensure_ascii=False).encode("utf-8")) >= SIDECAR_LIMIT_BYTES:
            raise ValueError(f"Bedrock sidecar exceeds {SIDECAR_LIMIT_BYTES} bytes: {dest}")
        _json(dest.with_name(dest.name + ".metadata.json"), sidecar)

    parent_dir = base / "parents" / doc_dir
    for parent in parents:
        parent_dir.mkdir(parents=True, exist_ok=True)
        (parent_dir / f"{parent.parent_id}.md").write_text(parent.text, encoding="utf-8")

    return {"document": relative.as_posix(), "strategy": strategy,
            "children": len(chunks), "parents": len(parents),
            "mean_chars": round(sum(c.chars for c in chunks) / len(chunks), 1) if chunks else 0,
            "max_chars": max((c.chars for c in chunks), default=0),
            "over_2800": sum(c.chars > 2800 for c in chunks),
            "total_indexed_chars": sum(c.chars for c in chunks),
            "parent_context": "separate, NOT indexed" if strategy == "hierarchical" else "not applicable"}


def export(input_dir: Path, output_dir: Path, run: str,
           parent_max: int = 2400, child_target: int = 950) -> dict:
    if not _ID.fullmatch(run):
        raise ValueError("experiment ID must be 3-51 letters/numbers/underscores/hyphens")
    input_dir, output_dir = input_dir.resolve(), output_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"Markdown input directory not found: {input_dir}")
    # Refuse overwrite and refuse export into source tree, preventing recursive input.
    if output_dir == input_dir or input_dir in output_dir.parents:
        raise ValueError("output must be outside input directory")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("output directory must be empty; choose a new experiment output")
    sources = sorted(p for p in input_dir.rglob("*.md") if not p.name.endswith(".metadata.json"))
    if not sources:
        raise ValueError("no Markdown documents found")
    rows, errors = [], []
    for src in sources:
        rel = src.relative_to(input_dir)
        doc_attrs = {}
        meta_path = src.with_name(src.name + ".metadata.json")
        if meta_path.is_file():
            doc_attrs = json.loads(meta_path.read_text(encoding="utf-8")).get("metadataAttributes", {})
        doc_attrs.setdefault("source_id", rel.parts[0] if len(rel.parts) > 1 else rel.stem[:60])
        md = src.read_text(encoding="utf-8")
        for strategy in STRATEGIES:
            try:
                rows.append(_write_variant(output_dir / "strategies" / strategy, rel, md,
                                           strategy, doc_attrs, run, parent_max, child_target))
            except (ValueError, UnicodeError) as exc:
                errors.append({"document": rel.as_posix(), "strategy": strategy, "error": str(exc)})
    summary = {"experiment_id": run, "input_document_count": len(sources),
               "strategies": list(STRATEGIES), "results": rows, "errors": errors,
               "staging_prefixes": {s: _staging_prefix(run, s) for s in STRATEGIES},
               "note": "Local corpus statistics only; not a retrieval relevance experiment. "
                       "Parents are NOT indexed or automatically fetched by Nova Sonic."}
    _json(output_dir / "manifest.json", summary)
    return summary


def fetch_text(bucket: str, dest: Path, profile: str | None = None) -> dict:
    """Read existing extracted Markdown from S3, without uploading or changing data."""
    import boto3
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    s3 = session.client("s3")
    n = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="text/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not (key.endswith(".md") or key.endswith(".md.metadata.json")):
                continue
            rel = Path(key).relative_to("text")
            if ".." in rel.parts:
                raise ValueError("unsafe S3 key")
            dst = dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
            n += 1
    return {"downloaded_files": n, "local_path": str(dest), "bucket": bucket, "read_only": True}


def stage_upload(export_dir: Path, bucket: str, *, confirm: bool = False) -> dict:
    """Write ONLY the sandboxed experiment prefixes; no deletes, no prod chunks."""
    summary = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    run = summary["experiment_id"]
    if summary.get("errors"):
        raise ValueError("experiment contains failed chunks; resolve before upload")
    if not bucket.startswith("ac215-"):
        raise ValueError("staging bucket must be an ac215-* knowledge bucket")
    if not confirm:
        raise ValueError("no upload performed; pass --confirm-upload to write isolated staging prefixes")
    import boto3
    s3 = boto3.Session().client("s3")
    # Reject reusing a staging run: a stale child could otherwise remain in the
    # index and invalidate the controlled comparison. Never delete anything.
    for strategy in STRATEGIES:
        prefix = _staging_prefix(run, strategy) + "/"
        existing = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        if existing.get("KeyCount", 0):
            raise ValueError(f"staging prefix is not empty: {prefix}; choose a new experiment ID")
    result = {}
    for strategy in STRATEGIES:
        base = export_dir / "strategies" / strategy
        prefix = _staging_prefix(run, strategy)
        count = 0
        for kind in ("chunks", "parents"):
            folder = base / kind
            if not folder.is_dir():
                continue
            for file in sorted(folder.rglob("*")):
                if not file.is_file():
                    continue
                rel = file.relative_to(base).as_posix()
                target = f"{prefix}/{rel}"
                s3.put_object(Bucket=bucket, Key=target, Body=file.read_bytes(),
                              ContentType="application/json" if target.endswith(".json") else "text/markdown; charset=utf-8")
                count += 1
        result[strategy] = {"objects_uploaded": count, "staging_prefix": prefix}
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export", help="offline A/B/C chunk export from a local Markdown folder")
    exp.add_argument("--input-dir", type=Path, required=True)
    exp.add_argument("--output-dir", type=Path, required=True)
    exp.add_argument("--experiment-id", required=True)
    exp.add_argument("--parent-max", type=int, default=2400)
    exp.add_argument("--child-target", type=int, default=950)
    dl = sub.add_parser("fetch-text", help="read-only download of text/ S3 Markdown to local folder")
    dl.add_argument("--bucket", required=True)
    dl.add_argument("--output-dir", type=Path, required=True)
    up = sub.add_parser("stage-upload", help="upload isolated A/B/C objects; requires opt-in")
    up.add_argument("--export-dir", type=Path, required=True)
    up.add_argument("--bucket", required=True)
    up.add_argument("--confirm-upload", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "export":
        result = export(args.input_dir, args.output_dir, args.experiment_id, args.parent_max, args.child_target)
    elif args.command == "fetch-text":
        result = fetch_text(args.bucket, args.output_dir)
    else:
        result = stage_upload(args.export_dir, args.bucket, confirm=args.confirm_upload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("errors"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
