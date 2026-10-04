"""Names and ARNs for the retrieval layer. Everything course-owned is ac215-*."""

from __future__ import annotations

import functools
import os

import boto3

REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "us-east-1"
DATA_DIR = os.environ.get("CAREONEX_DATA_DIR") or "data"

CHUNKS_PREFIX = "chunks"
CONFIG_KEY = "config/knowledge-base.json"  # written by kb-sync, read by retrieve

EMBEDDING_MODEL_ID = os.environ.get("CAREONEX_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
EMBEDDING_DIMENSIONS = int(os.environ.get("CAREONEX_EMBEDDING_DIMENSIONS", "1024"))

KB_NAME = os.environ.get("CAREONEX_KB_NAME", "ac215-program-kb")
DATA_SOURCE_NAME = "chunks"
INDEX_NAME = os.environ.get("CAREONEX_VECTOR_INDEX", "program-kb")


def session() -> boto3.Session:
    return boto3.Session(region_name=REGION)


@functools.lru_cache(maxsize=1)
def account_id() -> str:
    return session().client("sts").get_caller_identity()["Account"]


def bucket_name() -> str:
    return os.environ.get("CAREONEX_KB_BUCKET") or f"ac215-program-kb-{account_id()}"


def vector_bucket_name() -> str:
    return os.environ.get("CAREONEX_VECTOR_BUCKET") or f"ac215-program-vectors-{account_id()}"


def kb_role_arn() -> str:
    return os.environ.get("CAREONEX_KB_ROLE_ARN") or f"arn:aws:iam::{account_id()}:role/AC215-KnowledgeBaseRole"


def embedding_model_arn() -> str:
    return f"arn:aws:bedrock:{REGION}::foundation-model/{EMBEDDING_MODEL_ID}"
