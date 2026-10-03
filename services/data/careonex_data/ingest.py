"""Fetch every catalog source and put it in the knowledge bucket with its sidecar.

Idempotent: an object whose stored sha256 matches the freshly fetched bytes is
skipped. Each run writes a snapshot manifest (snapshots/<id>/manifest.json)
that records exactly which object versions make up the dataset, which is the
dataset version id used downstream.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests
from botocore.exceptions import ClientError

from careonex_data.catalog import SourceRow, catalog_sha256, load_catalog, sidecar_bytes
from careonex_data.config import SNAPSHOT_PREFIX

log = logging.getLogger(__name__)
USER_AGENT = "careonex-agents data service (+https://github.com/nadirbt/careonex-agents)"


@dataclass
class ItemResult:
    source_id: str
    file_name: str
    key: str
    status: str  # uploaded | unchanged | failed | dry-run
    sha256: str | None = None
    sha_matches_catalog: bool | None = None
    version_id: str | None = None
    bytes: int | None = None
    error: str | None = None


@dataclass
class Manifest:
    snapshot_id: str
    bucket: str
    catalog_path: str
    catalog_sha256: str
    started_at: str
    items: list[ItemResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {**asdict(self), "items": [asdict(i) for i in self.items]}

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.items:
            out[i.status] = out.get(i.status, 0) + 1
        return out


def default_snapshot_id(catalog_path: str | Path) -> str:
    return f"{dt.date.today().isoformat()}-{catalog_sha256(catalog_path)[:8]}"


def fetch_bytes(row: SourceRow, local_dir: Path | None, timeout: float) -> bytes:
    if local_dir:
        candidate = local_dir / row.source_id / row.file_name
        if not candidate.is_file():
            candidate = local_dir / row.file_name
        if candidate.is_file():
            return candidate.read_bytes()
    resp = requests.get(row.final_url or row.source_url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.content


def stored_sha256(s3, bucket: str, key: str) -> str | None:
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return head.get("Metadata", {}).get("sha256")


def put_source(s3, bucket: str, row: SourceRow, body: bytes, sha: str) -> str | None:
    resp = s3.put_object(
        Bucket=bucket,
        Key=row.object_key,
        Body=body,
        ContentType=row.content_type,
        Metadata={"sha256": sha, "source_id": row.source_id, "fetch_date": row.fetch_date, "source_url": row.source_url[:1024]},
    )
    s3.put_object(Bucket=bucket, Key=row.sidecar_key, Body=sidecar_bytes(row), ContentType="application/json")
    return resp.get("VersionId")


def ingest(
    s3,
    bucket: str,
    catalog_path: str | Path,
    snapshot_id: str | None = None,
    local_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
    dry_run: bool = False,
    timeout: float = 60.0,
    only: set[str] | None = None,
) -> Manifest:
    rows = load_catalog(catalog_path)
    snapshot_id = snapshot_id or default_snapshot_id(catalog_path)
    manifest = Manifest(
        snapshot_id=snapshot_id,
        bucket=bucket,
        catalog_path=str(catalog_path),
        catalog_sha256=catalog_sha256(catalog_path),
        started_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )
    local = Path(local_dir) if local_dir else None

    for row in rows:
        if only and row.source_id not in only and row.file_name not in only:
            continue
        item = ItemResult(source_id=row.source_id, file_name=row.file_name, key=row.object_key, status="failed")
        try:
            body = fetch_bytes(row, local, timeout)
            sha = hashlib.sha256(body).hexdigest()
            item.sha256, item.bytes = sha, len(body)
            item.sha_matches_catalog = (sha == row.sha256) if row.sha256 else None
            if item.sha_matches_catalog is False:
                log.warning("%s: content differs from catalog sha256 (source changed since %s)", row.file_name, row.fetch_date)
            if dry_run:
                item.status = "dry-run"
            elif stored_sha256(s3, bucket, row.object_key) == sha:
                item.status = "unchanged"
            else:
                item.version_id = put_source(s3, bucket, row, body, sha)
                item.status = "uploaded"
            log.info("%-9s %s (%d bytes)", item.status, row.object_key, len(body))
        except Exception as exc:  # noqa: BLE001 - one bad source must not stop the run
            item.error = f"{type(exc).__name__}: {exc}"
            log.error("failed    %s: %s", row.object_key, item.error)
        manifest.items.append(item)

    payload = json.dumps(manifest.as_dict(), indent=2).encode("utf-8")
    if data_dir:
        out = Path(data_dir) / snapshot_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "manifest.json").write_bytes(payload)
        log.info("wrote %s", out / "manifest.json")
    if not dry_run:
        s3.put_object(Bucket=bucket, Key=f"{SNAPSHOT_PREFIX}/{snapshot_id}/manifest.json", Body=payload, ContentType="application/json")
    return manifest
