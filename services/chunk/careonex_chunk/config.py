"""Bucket resolution and prefixes (mirrors the other services on purpose)."""

from __future__ import annotations

import functools
import os

import boto3

REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "us-east-1"
DATA_DIR = os.environ.get("CAREONEX_DATA_DIR") or "data"

TEXT_PREFIX = "text"
CHUNKS_PREFIX = "chunks"
SNAPSHOT_PREFIX = "snapshots"
SIDECAR_SUFFIX = ".metadata.json"

# Chunk sizing in characters (~4 chars per token): target ~400 tokens, hard cap ~700.
TARGET_CHARS = int(os.environ.get("CAREONEX_CHUNK_TARGET_CHARS", "1600"))
MAX_CHARS = int(os.environ.get("CAREONEX_CHUNK_MAX_CHARS", "2800"))
MIN_CHARS = int(os.environ.get("CAREONEX_CHUNK_MIN_CHARS", "200"))


def session() -> boto3.Session:
    return boto3.Session(region_name=REGION)


@functools.lru_cache(maxsize=1)
def account_id() -> str:
    return session().client("sts").get_caller_identity()["Account"]


def bucket_name() -> str:
    return os.environ.get("CAREONEX_KB_BUCKET") or f"ac215-program-kb-{account_id()}"
