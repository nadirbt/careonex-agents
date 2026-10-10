import datetime as dt
import os

import boto3
import pytest
from botocore.stub import Stubber

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.pop("AWS_PROFILE", None)

from careonex_kb import config  # noqa: E402
from careonex_kb.provision import MissingServiceRole, ensure_index, ensure_knowledge_base, ensure_vector_bucket, find_knowledge_base  # noqa: E402


def test_names_are_ac215_scoped(monkeypatch):
    monkeypatch.setenv("CAREONEX_KB_BUCKET", "ac215-program-kb-123")
    monkeypatch.setenv("CAREONEX_VECTOR_BUCKET", "ac215-program-vectors-123")
    monkeypatch.setenv("CAREONEX_KB_ROLE_ARN", "arn:aws:iam::123:role/AC215-KnowledgeBaseRole")
    assert config.bucket_name().startswith("ac215-")
    assert config.vector_bucket_name().startswith("ac215-")
    assert config.kb_role_arn().endswith("AC215-KnowledgeBaseRole")
    assert config.embedding_model_arn() == "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"


def test_vector_bucket_get_or_create():
    s3v = boto3.client("s3vectors", region_name="us-east-1")
    with Stubber(s3v) as stub:
        stub.add_client_error("get_vector_bucket", service_error_code="NotFoundException", http_status_code=404)
        stub.add_response("create_vector_bucket", {"vectorBucketArn": "arn:v"}, {"vectorBucketName": "ac215-vectors", "encryptionConfiguration": {"sseType": "AES256"}})
        assert ensure_vector_bucket(s3v, "ac215-vectors") == "arn:v"
    with Stubber(s3v) as stub:
        stub.add_response("get_vector_bucket", {"vectorBucket": {"vectorBucketName": "ac215-vectors", "vectorBucketArn": "arn:v", "creationTime": dt.datetime(2026, 10, 4)}}, {"vectorBucketName": "ac215-vectors"})
        assert ensure_vector_bucket(s3v, "ac215-vectors") == "arn:v"


def test_index_dimension_mismatch_is_loud():
    s3v = boto3.client("s3vectors", region_name="us-east-1")
    idx = {"vectorBucketName": "ac215-vectors", "indexName": "program-kb", "indexArn": "arn:i", "creationTime": dt.datetime(2026, 10, 4), "dataType": "float32", "dimension": 256, "distanceMetric": "cosine"}
    with Stubber(s3v) as stub:
        stub.add_response("get_index", {"index": idx}, {"vectorBucketName": "ac215-vectors", "indexName": "program-kb"})
        with pytest.raises(RuntimeError, match="dimension 256"):
            ensure_index(s3v, "ac215-vectors", "program-kb", 1024)


def test_find_knowledge_base_by_name_and_missing_role_message():
    agent = boto3.client("bedrock-agent", region_name="us-east-1")
    summary = {"knowledgeBaseId": "KB1", "name": "ac215-program-kb", "status": "ACTIVE", "updatedAt": dt.datetime(2026, 10, 4)}
    with Stubber(agent) as stub:
        stub.add_response("list_knowledge_bases", {"knowledgeBaseSummaries": [summary]}, {"maxResults": 100})
        stub.add_response("get_knowledge_base", {"knowledgeBase": {**summary, "knowledgeBaseArn": "arn:kb", "roleArn": "arn:r", "knowledgeBaseConfiguration": {"type": "VECTOR"}, "createdAt": dt.datetime(2026, 10, 4)}}, {"knowledgeBaseId": "KB1"})
        assert find_knowledge_base(agent, "ac215-program-kb")["knowledgeBaseId"] == "KB1"
    with Stubber(agent) as stub:
        stub.add_response("list_knowledge_bases", {"knowledgeBaseSummaries": []}, {"maxResults": 100})
        stub.add_client_error("create_knowledge_base", service_error_code="ValidationException", service_message="The knowledge base service role with name AC215-KnowledgeBaseRole does not exist or cannot be assumed", http_status_code=400)
        with pytest.raises(MissingServiceRole, match="TEAM_SETUP.md"):
            ensure_knowledge_base(agent, "ac215-program-kb", "arn:aws:iam::1:role/AC215-KnowledgeBaseRole", "arn:i", 1024)
