"""Bucket resolution and prefixes. Mirrors services/data/careonex_data/config.py on purpose:
each service is its own container and must not import another service's code."""

from __future__ import annotations

import functools
import os

import boto3

REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "us-east-1"
DATA_DIR = os.environ.get("CAREONEX_DATA_DIR") or "data"

RAW_PREFIX = "raw"
TEXT_PREFIX = "text"
SNAPSHOT_PREFIX = "snapshots"
SIDECAR_SUFFIX = ".metadata.json"


def session() -> boto3.Session:
    return boto3.Session(region_name=REGION)


@functools.lru_cache(maxsize=1)
def account_id() -> str:
    return session().client("sts").get_caller_identity()["Account"]


def bucket_name() -> str:
    # "ac215-" prefix: production buckets are "careonex-*" and the IAM policy only covers ac215-*.
    return os.environ.get("CAREONEX_KB_BUCKET") or f"ac215-program-kb-{account_id()}"
