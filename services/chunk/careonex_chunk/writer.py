"""Write chunks to S3 as one object per chunk plus a sidecar, under chunks/<source_id>/<stem>/.

One-object-per-chunk is the layout Bedrock Knowledge Bases expects when its chunking strategy
is NONE: each S3 object becomes exactly one vector, and the sidecar supplies the filterable
metadata. Stale chunk objects from a previous run are deleted so the index never holds text
that no longer exists in the source."""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from botocore.exceptions import ClientError

from careonex_chunk.chunker import CHUNKER_VERSION, Chunk, chunk_markdown
from careonex_chunk.config import CHUNKS_PREFIX, MAX_CHARS, MIN_CHARS, SIDECAR_SUFFIX, SNAPSHOT_PREFIX, TARGET_CHARS, TEXT_PREFIX

log = logging.getLogger(__name__)


@dataclass
class DocResult:
    text_key: str
    chunk_prefix: str
    status: str  # chunked | unchanged | failed | dry-run
    chunks: int = 0
    written: int = 0
    deleted: int = 0
    error: str | None = None


@dataclass
class Summary:
    snapshot_id: str
    bucket: str
    chunker_version: str
    started_at: str
    docs: list[DocResult] = field(default_factory=list)
    total_chunks: int = 0

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.docs:
            out[d.status] = out.get(d.status, 0) + 1
        return out

    def as_dict(self) -> dict:
        return {**asdict(self), "docs": [asdict(d) for d in self.docs], "counts": self.counts}


def list_text_documents(s3, bucket: str) -> list[str]:
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"{TEXT_PREFIX}/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".md"):
                keys.append(obj["Key"])
    return sorted(keys)


def chunk_prefix_for(text_key: str) -> str:
    # text/nj_doas/nj_doas_jacc.html.md -> chunks/nj_doas/nj_doas_jacc.html/
    rel = text_key[len(TEXT_PREFIX) + 1:]
    if rel.endswith(".md"):
        rel = rel[:-3]
    return f"{CHUNKS_PREFIX}/{rel}/"


def list_existing(s3, bucket: str, prefix: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith(SIDECAR_SUFFIX):
                try:
                    out[obj["Key"]] = s3.head_object(Bucket=bucket, Key=obj["Key"]).get("Metadata", {})
                except ClientError:
                    out[obj["Key"]] = {}
    return out


# Bedrock Knowledge Bases silently ignores a document whose .metadata.json is larger than 1024 bytes
# (the ingestion job still reports COMPLETE, with the reason only in failureReasons). Chunk sidecars
# therefore carry only what retrieval filters on and what a citation needs; hashes and provenance stay
# in the S3 object metadata and in the text/ sidecar.
SIDECAR_LIMIT_BYTES = 1024
_SIDECAR_BUDGET_BYTES = 900


def sidecar_for(chunk: Chunk, doc_attrs: dict, text_key: str, total: int) -> dict:
    def pick(key: str, limit: int | None = None):
        v = doc_attrs.get(key)
        if isinstance(v, str) and limit:
            v = v[:limit]
        return v

    attrs = {
        "source_id": pick("source_id"),
        "program": pick("program"),
        "title": pick("title", 80),
        "source_url": pick("source_url", 160),
        "effective_date": pick("effective_date"),
        "year": pick("year"),
        "jurisdiction": pick("jurisdiction"),
        "kind": pick("kind"),
        "heading_path": " > ".join(chunk.heading_path)[:140],
        "chunk_order": chunk.order,
        "chunk_count": total,
        "derived_from": text_key,
    }
    attrs = {k: v for k, v in attrs.items() if v not in (None, "")}
    # Enforce the budget by trimming the two free-text fields first.
    for field_name in ("heading_path", "title", "source_url"):
        while len(json.dumps({"metadataAttributes": attrs}, ensure_ascii=False).encode("utf-8")) > _SIDECAR_BUDGET_BYTES and len(attrs.get(field_name, "")) > 20:
            attrs[field_name] = attrs[field_name][: max(20, len(attrs[field_name]) // 2)]
    return {"metadataAttributes": attrs}


def chunk_all(
    s3,
    bucket: str,
    snapshot_id: str | None = None,
    only: set[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
    data_dir: str | Path | None = None,
) -> Summary:
    snapshot_id = snapshot_id or dt.date.today().isoformat()
    summary = Summary(
        snapshot_id=snapshot_id,
        bucket=bucket,
        chunker_version=CHUNKER_VERSION,
        started_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )
    jsonl_lines: list[str] = []

    for text_key in list_text_documents(s3, bucket):
        if only and not any(tok in text_key for tok in only):
            continue
        prefix = chunk_prefix_for(text_key)
        res = DocResult(text_key=text_key, chunk_prefix=prefix, status="failed")
        try:
            text_meta = s3.head_object(Bucket=bucket, Key=text_key).get("Metadata", {})
            text_sha = text_meta.get("text_sha256", "")
            existing = list_existing(s3, bucket, prefix)
            if (
                not force
                and existing
                and all(m.get("text_sha256") == text_sha and m.get("chunker_version") == CHUNKER_VERSION for m in existing.values())
            ):
                res.status, res.chunks = "unchanged", len(existing)
                summary.total_chunks += len(existing)
                summary.docs.append(res)
                log.info("unchanged %s (%d chunks)", prefix, len(existing))
                continue

            md = s3.get_object(Bucket=bucket, Key=text_key)["Body"].read().decode("utf-8")
            try:
                doc_attrs = json.loads(s3.get_object(Bucket=bucket, Key=text_key + SIDECAR_SUFFIX)["Body"].read())["metadataAttributes"]
            except ClientError:
                doc_attrs = {}
            chunks = chunk_markdown(md, TARGET_CHARS, MAX_CHARS, MIN_CHARS)
            res.chunks = len(chunks)
            wanted = {f"{prefix}{c.chunk_id}.md": c for c in chunks}
            for key, c in wanted.items():
                jsonl_lines.append(json.dumps({"key": key, "text_key": text_key, "chunk_id": c.chunk_id, "order": c.order, "heading_path": c.heading_path, "chars": c.chars, "sha256": c.sha256, "text": c.text}, ensure_ascii=False))
            if dry_run:
                res.status = "dry-run"
            else:
                for key, c in wanted.items():
                    prev = existing.get(key, {})
                    if prev.get("chunk_sha256") == c.sha256 and prev.get("text_sha256") == text_sha and prev.get("chunker_version") == CHUNKER_VERSION:
                        continue
                    s3.put_object(
                        Bucket=bucket, Key=key, Body=c.text.encode("utf-8"), ContentType="text/markdown; charset=utf-8",
                        Metadata={"chunk_sha256": c.sha256, "text_sha256": text_sha, "chunker_version": CHUNKER_VERSION, "derived_from": text_key},
                    )
                    s3.put_object(
                        Bucket=bucket, Key=key + SIDECAR_SUFFIX, ContentType="application/json",
                        Body=json.dumps(sidecar_for(c, doc_attrs, text_key, len(chunks)), ensure_ascii=False).encode("utf-8"),
                    )
                    res.written += 1
                stale = [k for k in existing if k not in wanted]
                for k in stale:
                    s3.delete_object(Bucket=bucket, Key=k)
                    s3.delete_object(Bucket=bucket, Key=k + SIDECAR_SUFFIX)
                res.deleted = len(stale)
                res.status = "chunked"
            summary.total_chunks += len(chunks)
            log.info("%-9s %s (%d chunks, %d written, %d stale removed)", res.status, prefix, res.chunks, res.written, res.deleted)
        except Exception as exc:  # noqa: BLE001
            res.error = f"{type(exc).__name__}: {exc}"
            log.error("failed    %s: %s", text_key, res.error)
        summary.docs.append(res)

    payload = json.dumps(summary.as_dict(), indent=2).encode("utf-8")
    jsonl = ("\n".join(jsonl_lines) + "\n").encode("utf-8") if jsonl_lines else b""
    if data_dir:
        out = Path(data_dir) / snapshot_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "chunk.json").write_bytes(payload)
        if jsonl:
            (out / "chunks.jsonl").write_bytes(jsonl)
    if not dry_run:
        s3.put_object(Bucket=bucket, Key=f"{SNAPSHOT_PREFIX}/{snapshot_id}/chunk.json", Body=payload, ContentType="application/json")
        if jsonl:
            s3.put_object(Bucket=bucket, Key=f"{SNAPSHOT_PREFIX}/{snapshot_id}/chunks.jsonl", Body=jsonl, ContentType="application/x-ndjson")
    return summary
