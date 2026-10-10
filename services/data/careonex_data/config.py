"""Runtime configuration: region, account, bucket naming.

Bucket names must be globally unique, so defaults are suffixed with the AWS
account id resolved at runtime. Override with the env var on each spec when a
bucket already exists under another name.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass

import boto3

REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "us-east-1"
CATALOG_PATH = os.environ.get("CAREONEX_CATALOG") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "catalog", "ragfile_list.csv"
)
DATA_DIR = os.environ.get("CAREONEX_DATA_DIR") or "data"


@dataclass(frozen=True)
class BucketSpec:
    key: str
    env_var: str
    default_suffix: str
    description: str
    versioning: bool = True


KNOWLEDGE = BucketSpec(
    key="knowledge",
    env_var="CAREONEX_KB_BUCKET",
    default_suffix="program-kb",
    description="Public program documents + .metadata.json sidecars; source for the Bedrock Knowledge Base. No PII.",
)

# Only one bucket for now. Client-supplied documents (PHI), if the product ever takes them,
# get their own KMS-encrypted bucket in a separate account and are never ingested here.
BUCKETS: dict[str, BucketSpec] = {KNOWLEDGE.key: KNOWLEDGE}

# Object layout inside the knowledge bucket. Each pipeline step owns one prefix and
# never writes to another step's prefix.
RAW_PREFIX = "raw"            # services/data: untouched source bytes + .metadata.json sidecars
TEXT_PREFIX = "text"          # services/extract: clean Markdown per document, same sidecars
CHUNKS_PREFIX = "chunks"      # services/chunk: section-aware JSONL; the Knowledge Base data source
SNAPSHOT_PREFIX = "snapshots" # services/data: manifest per dataset snapshot id


def session() -> boto3.Session:
    return boto3.Session(region_name=REGION)


@functools.lru_cache(maxsize=1)
def account_id() -> str:
    return session().client("sts").get_caller_identity()["Account"]


def caller_arn() -> str:
    return session().client("sts").get_caller_identity()["Arn"]


def bucket_name(spec: BucketSpec) -> str:
    override = os.environ.get(spec.env_var)
    if override:
        return override
    # "ac215-" prefix on purpose: every production bucket in the shared account starts with
    # "careonex-", and the AC215 IAM policy is scoped to arn:aws:s3:::ac215-*. Never change this
    # to "careonex-" or the course role gains access to production data.
    return f"ac215-{spec.default_suffix}-{account_id()}"
