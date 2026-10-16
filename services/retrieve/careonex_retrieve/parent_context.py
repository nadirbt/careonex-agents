"""Read-only, opt-in parent context for *staging* hierarchical chunks.

Parent text is NOT a ranked retrieval hit. We fetch it only after ranking
children, and only from an exact, validated experiment S3 path. Never read
production S3 document locations or user-supplied arbitrary S3 URIs.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

_PARENT_RE = re.compile(r"^[0-9a-f]{20}$")
_RUN_RE = re.compile(r"^chunk-abc-[0-9]{8}-[0-9]{6}$")


def parent_object(s3_uri: str | None, parent_id: str | None) -> tuple[str, str] | None:
    if not s3_uri or not parent_id or not _PARENT_RE.fullmatch(parent_id):
        return None
    parsed = urlsplit(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc or parsed.query or parsed.fragment:
        return None
    key = parsed.path.lstrip("/")
    parts = key.split("/")
    # experiments/chunking/<run>/hierarchical/chunks/<doc-directory>/<chunk>.md
    if (len(parts) < 8 or parts[:2] != ["experiments", "chunking"]
            or not _RUN_RE.fullmatch(parts[2]) or parts[3:5] != ["hierarchical", "chunks"]
            or not key.endswith(".md") or any(p in ("", ".", "..") for p in parts)):
        return None
    parent_key = "/".join(parts[:4] + ["parents"] + parts[5:-1] + [parent_id + ".md"])
    return parsed.netloc, parent_key


def fetch_parent(s3_client, s3_uri: str | None, parent_id: str | None, *,
                 allowed_bucket: str | None = None, max_chars: int = 3000) -> str | None:
    obj = parent_object(s3_uri, parent_id)
    if obj is None:
        return None
    bucket, key = obj
    if allowed_bucket and bucket != allowed_bucket:
        return None
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    raw = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read(max_chars * 4 + 1)
    text = raw.decode("utf-8")
    if not text.strip():
        return None
    return text[:max_chars]
