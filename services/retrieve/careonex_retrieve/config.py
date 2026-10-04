from __future__ import annotations

import functools
import json
import os

import boto3

REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "us-east-1"
DATA_DIR = os.environ.get("CAREONEX_DATA_DIR") or "data"
CONFIG_KEY = "config/knowledge-base.json"
DEFAULT_TOP_K = int(os.environ.get("CAREONEX_RETRIEVE_TOP_K", "5"))
PORT = int(os.environ.get("PORT", "8080"))


def session() -> boto3.Session:
    return boto3.Session(region_name=REGION)


@functools.lru_cache(maxsize=1)
def account_id() -> str:
    return session().client("sts").get_caller_identity()["Account"]


def bucket_name() -> str:
    return os.environ.get("CAREONEX_KB_BUCKET") or f"ac215-program-kb-{account_id()}"


@functools.lru_cache(maxsize=1)
def knowledge_base_id() -> str:
    """CAREONEX_KB_ID wins; otherwise read what kb-sync published to the bucket."""
    env = os.environ.get("CAREONEX_KB_ID")
    if env:
        return env
    body = session().client("s3").get_object(Bucket=bucket_name(), Key=CONFIG_KEY)["Body"].read()
    kb_id = json.loads(body).get("knowledge_base_id")
    if not kb_id:
        raise RuntimeError("config/knowledge-base.json has no knowledge_base_id; run kb-sync first")
    return kb_id
