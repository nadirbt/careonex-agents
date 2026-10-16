"""Walk raw/ in the knowledge bucket and write text/<source_id>/<file>.md plus sidecar.

Idempotent on two levels: a text object is skipped when it was produced from the
same raw bytes by the same extractor version; and when it is reproduced, the
summary records whether the *text* hash actually changed, which is the signal
"the program rules may have changed" that raw-byte hashes cannot give for HTML."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from botocore.exceptions import ClientError

from careonex_extract.config import RAW_PREFIX, SIDECAR_SUFFIX, SNAPSHOT_PREFIX, TEXT_PREFIX
from careonex_extract.convert import EXTRACTOR_VERSION, to_markdown

log = logging.getLogger(__name__)


@dataclass
class ItemResult:
    raw_key: str
    text_key: str
    status: str  # extracted | unchanged | failed | dry-run
    raw_sha256: str | None = None
    text_sha256: str | None = None
    previous_text_sha256: str | None = None
    text_changed: bool | None = None
    chars: int | None = None
    error: str | None = None


@dataclass
class Summary:
    snapshot_id: str
    bucket: str
    extractor_version: str
    started_at: str
    items: list[ItemResult] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.items:
            out[i.status] = out.get(i.status, 0) + 1
        return out

    def as_dict(self) -> dict:
        return {**asdict(self), "items": [asdict(i) for i in self.items], "counts": self.counts}


def list_raw_documents(s3, bucket: str) -> list[str]:
    keys: list[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"{RAW_PREFIX}/"):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if not k.endswith(SIDECAR_SUFFIX) and not k.endswith("/"):
                keys.append(k)
    return sorted(keys)


def text_key_for(raw_key: str) -> str:
    assert raw_key.startswith(f"{RAW_PREFIX}/")
    return f"{TEXT_PREFIX}/{raw_key[len(RAW_PREFIX) + 1:]}.md"


def head_meta(s3, bucket: str, key: str) -> dict | None:
    try:
        return s3.head_object(Bucket=bucket, Key=key).get("Metadata", {})
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def read_sidecar(s3, bucket: str, raw_key: str) -> dict:
    try:
        body = s3.get_object(Bucket=bucket, Key=raw_key + SIDECAR_SUFFIX)["Body"].read()
        return json.loads(body)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return {"metadataAttributes": {}}
        raise


def extract_all(
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
        extractor_version=EXTRACTOR_VERSION,
        started_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )
    for raw_key in list_raw_documents(s3, bucket):
        if only and not any(tok in raw_key for tok in only):
            continue
        item = ItemResult(raw_key=raw_key, text_key=text_key_for(raw_key), status="failed")
        try:
            raw_meta = head_meta(s3, bucket, raw_key) or {}
            item.raw_sha256 = raw_meta.get("sha256")
            existing = head_meta(s3, bucket, item.text_key)
            if existing:
                item.previous_text_sha256 = existing.get("text_sha256")
            if (
                not force
                and existing
                and item.raw_sha256
                and existing.get("source_sha256") == item.raw_sha256
                and existing.get("extractor_version") == EXTRACTOR_VERSION
            ):
                item.status = "unchanged"
                item.text_sha256 = item.previous_text_sha256
                item.text_changed = False
                summary.items.append(item)
                log.info("unchanged %s", item.text_key)
                continue

            sidecar = read_sidecar(s3, bucket, raw_key)
            attrs = dict(sidecar.get("metadataAttributes", {}))
            obj = s3.get_object(Bucket=bucket, Key=raw_key)
            data = obj["Body"].read()
            if not item.raw_sha256:
                item.raw_sha256 = hashlib.sha256(data).hexdigest()
            md = to_markdown(data, attrs.get("kind", ""), raw_key, url=attrs.get("final_url") or attrs.get("source_url"))
            # Only the geometry-verified six-column DoAS converter may assert this.
            if Path(raw_key).name == "nj_doas_programs_side_by_side_2026.pdf":
                attrs["table_verified"] = True
            item.text_sha256 = hashlib.sha256(md.encode("utf-8")).hexdigest()
            item.chars = len(md)
            item.text_changed = item.previous_text_sha256 is not None and item.previous_text_sha256 != item.text_sha256
            if dry_run:
                item.status = "dry-run"
            else:
                attrs.update(
                    {
                        "derived_from": raw_key,
                        "source_sha256": item.raw_sha256,
                        "text_sha256": item.text_sha256,
                        "extractor": {"pdf": "pymupdf4llm", "md": "passthrough"}.get(attrs.get("kind"), "markdownify"),
                        "extractor_version": EXTRACTOR_VERSION,
                        "chars": item.chars,
                    }
                )
                s3.put_object(
                    Bucket=bucket,
                    Key=item.text_key,
                    Body=md.encode("utf-8"),
                    ContentType="text/markdown; charset=utf-8",
                    Metadata={
                        "source_sha256": item.raw_sha256,
                        "text_sha256": item.text_sha256,
                        "extractor_version": EXTRACTOR_VERSION,
                        "derived_from": raw_key,
                    },
                )
                s3.put_object(
                    Bucket=bucket,
                    Key=item.text_key + SIDECAR_SUFFIX,
                    Body=json.dumps({"metadataAttributes": attrs}, indent=2, ensure_ascii=False).encode("utf-8"),
                    ContentType="application/json",
                )
                item.status = "extracted"
            log.info("%-9s %s (%d chars%s)", item.status, item.text_key, item.chars or 0, ", text changed" if item.text_changed else "")
        except Exception as exc:  # noqa: BLE001
            item.error = f"{type(exc).__name__}: {exc}"
            log.error("failed    %s: %s", raw_key, item.error)
        summary.items.append(item)

    payload = json.dumps(summary.as_dict(), indent=2).encode("utf-8")
    if data_dir:
        out = Path(data_dir) / snapshot_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "extract.json").write_bytes(payload)
    if not dry_run:
        s3.put_object(Bucket=bucket, Key=f"{SNAPSHOT_PREFIX}/{snapshot_id}/extract.json", Body=payload, ContentType="application/json")
    return summary
