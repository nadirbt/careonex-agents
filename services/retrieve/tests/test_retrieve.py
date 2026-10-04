import os

import boto3
from botocore.stub import Stubber
from fastapi.testclient import TestClient

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ["CAREONEX_KB_ID"] = "KBTEST1234"
os.environ.pop("AWS_PROFILE", None)

from careonex_retrieve import app as appmod  # noqa: E402
from careonex_retrieve.retriever import build_filter, retrieve  # noqa: E402

HIT = {
    "content": {"text": "2026 DoAS Programs > JACC\n\n| Individual | $4,855 | $40,000 |"},
    "score": 0.71,
    "location": {"type": "S3", "s3Location": {"uri": "s3://ac215-program-kb-1/chunks/nj_doas/x/0003-abc.md"}},
    "metadata": {"program": "JACC", "source_url": "https://www.nj.gov/jacc", "title": "JACC", "effective_date": "2026-03-12", "heading_path": "2026 DoAS Programs > JACC", "year": 2026},
}


def test_filter_building():
    assert build_filter() is None
    assert build_filter(program="MLTSS") == {"stringContains": {"key": "program", "value": "MLTSS"}}
    f = build_filter(program="JACC", year=2026)
    assert f["andAll"][1] == {"equals": {"key": "year", "value": 2026}}


def test_retrieve_maps_passages_and_measures_latency():
    rt = boto3.client("bedrock-agent-runtime", region_name="us-east-1")
    with Stubber(rt) as stub:
        stub.add_response("retrieve", {"retrievalResults": [HIT]}, {
            "knowledgeBaseId": "KBTEST1234",
            "retrievalQuery": {"text": "What is the JACC income limit?"},
            "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 3, "filter": {"stringContains": {"key": "program", "value": "JACC"}}}},
        })
        r = retrieve(rt, "KBTEST1234", "What is the JACC income limit?", 3, build_filter(program="JACC"))
    assert len(r.passages) == 1
    p = r.passages[0]
    assert p.program == "JACC" and p.source_url == "https://www.nj.gov/jacc" and p.s3_key == "chunks/nj_doas/x/0003-abc.md"
    assert "$4,855" in p.text and r.latency_ms >= 0


def test_http_api(monkeypatch):
    rt = boto3.client("bedrock-agent-runtime", region_name="us-east-1")
    stub = Stubber(rt)
    stub.add_response("retrieve", {"retrievalResults": [HIT]}, {
        "knowledgeBaseId": "KBTEST1234",
        "retrievalQuery": {"text": "Does JACC have an income limit?"},
        "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 5, "filter": {"stringContains": {"key": "program", "value": "JACC"}}}},
    })
    stub.activate()
    monkeypatch.setattr(appmod, "_runtime", rt)
    client = TestClient(appmod.app)
    assert client.get("/health").json() == {"ok": True}
    resp = client.post("/retrieve", json={"query": "Does JACC have an income limit?", "program": "JACC"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["knowledge_base_id"] == "KBTEST1234" and body["passages"][0]["program"] == "JACC"
    assert client.post("/retrieve", json={"query": "x"}).status_code == 422
