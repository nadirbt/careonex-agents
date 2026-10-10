"""Idempotent S3 bucket provisioning.

`ensure_bucket` creates a bucket if it does not exist and applies the baseline
settings (versioning, public access block, default encryption). If the bucket
already exists and we own it, it is left alone unless `enforce=True`, in which
case the baseline settings are re-applied. A bucket that exists but belongs to
someone else raises, because the name is simply not available to us.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from botocore.exceptions import ClientError

from careonex_data.config import BucketSpec

log = logging.getLogger(__name__)


class BucketNotOurs(RuntimeError):
    pass


@dataclass
class BucketStatus:
    key: str
    name: str
    exists: bool
    created: bool
    versioning: str | None = None
    encryption: str | None = None
    public_access_blocked: bool | None = None
    region: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def probe(s3, name: str) -> str:
    """Return 'owned', 'missing' or 'forbidden' for a bucket name."""
    try:
        s3.head_bucket(Bucket=name)
        return "owned"
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("404", "NoSuchBucket", "NotFound") or status == 404:
            return "missing"
        if code in ("403", "Forbidden", "AccessDenied") or status == 403:
            return "forbidden"
        raise


def create(s3, name: str, region: str) -> None:
    kwargs: dict = {"Bucket": name}
    if region != "us-east-1":  # us-east-1 rejects an explicit LocationConstraint
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**kwargs)
    s3.get_waiter("bucket_exists").wait(Bucket=name)
    log.info("created bucket %s in %s", name, region)


def apply_baseline(s3, spec: BucketSpec, name: str) -> None:
    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    if spec.versioning:
        s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": "Enabled"})
    rule: dict = {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}, "BucketKeyEnabled": True}
    s3.put_bucket_encryption(Bucket=name, ServerSideEncryptionConfiguration={"Rules": [rule]})
    s3.put_bucket_tagging(
        Bucket=name,
        Tagging={"TagSet": [{"Key": "project", "Value": "careonex-agents"}, {"Key": "component", "Value": spec.key}]},
    )


def describe(s3, spec: BucketSpec, name: str, created: bool = False) -> BucketStatus:
    st = BucketStatus(key=spec.key, name=name, exists=True, created=created)
    try:
        st.versioning = s3.get_bucket_versioning(Bucket=name).get("Status", "Disabled")
    except ClientError:
        st.versioning = None
    try:
        rules = s3.get_bucket_encryption(Bucket=name)["ServerSideEncryptionConfiguration"]["Rules"]
        st.encryption = rules[0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
    except ClientError:
        st.encryption = None
    try:
        cfg = s3.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
        st.public_access_blocked = all(cfg.values())
    except ClientError:
        st.public_access_blocked = None
    try:
        loc = s3.get_bucket_location(Bucket=name).get("LocationConstraint")
        st.region = loc or "us-east-1"
    except ClientError:
        st.region = None
    return st


def ensure_bucket(s3, spec: BucketSpec, name: str, region: str, enforce: bool = False) -> BucketStatus:
    state = probe(s3, name)
    if state == "forbidden":
        raise BucketNotOurs(
            f"Bucket '{name}' exists but this identity cannot access it. Either it belongs to another "
            f"account or the role lacks s3:ListBucket. Set {spec.env_var} to a bucket you own."
        )
    created = False
    if state == "missing":
        create(s3, name, region)
        apply_baseline(s3, spec, name)
        created = True
    elif enforce:
        apply_baseline(s3, spec, name)
        log.info("re-applied baseline settings on %s", name)
    else:
        log.info("bucket %s already exists; leaving settings untouched", name)
    return describe(s3, spec, name, created=created)
