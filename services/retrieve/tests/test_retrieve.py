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
from careonex_retrieve.retriever import Passage, build_filter, figure_year, prefer_latest, retrieve  # noqa: E402

HIT = {
    "content": {"text": "2026 DoAS Programs > JACC\n\n| Individual | $4,855 | $40,000 |"},
    "score": 0.71,
    "location": {"type": "S3", "s3Location": {"uri": "s3://ac215-program-kb-1/chunks/nj_doas/x/0003-abc.md"}},
    "metadata": {"program": "JACC", "source_url": "https://www.nj.gov/jacc", "title": "JACC", "effective_date": "2026-03-12", "heading_path": "2026 DoAS Programs > JACC", "year": 2026},
}


def test_filter_building():
    assert build_filter() is None
    f = build_filter(program="MLTSS")
    assert f["orAll"][0] == {"equals": {"key": "program", "value": "NJ FamilyCare / Medicaid MLTSS"}}
    f = build_filter(program="JACC", year=2026)
    assert f["andAll"][0]["orAll"][0] == {"equals": {"key": "program", "value": "JACC"}}
    assert f["andAll"][1] == {"equals": {"key": "year", "value": 2026}}
    assert build_filter(program="something unknown") is None  # unknown name: no filter, not zero results
    assert build_filter(program="Medicare") == {"equals": {"key": "program", "value": "Medicare home health benefit"}}


def test_retrieve_maps_passages_and_measures_latency():
    rt = boto3.client("bedrock-agent-runtime", region_name="us-east-1")
    with Stubber(rt) as stub:
        stub.add_response("retrieve", {"retrievalResults": [HIT]}, {
            "knowledgeBaseId": "KBTEST1234",
            "retrievalQuery": {"text": "What is the JACC income limit?"},
            "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 10, "filter": {"orAll": [{"equals": {"key": "program", "value": "JACC"}}, {"equals": {"key": "program", "value": "All DoAS programs"}}]}}},
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
        "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 10, "filter": {"orAll": [{"equals": {"key": "program", "value": "JACC"}}, {"equals": {"key": "program", "value": "All DoAS programs"}}]}}},
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


def _p(text, program="JACC", eff="2026-03-23", title="t"):
    return Passage(text=text, score=0.5, source_url=None, title=title, program=program, effective_date=eff, heading_path=None, s3_key=None)


def test_figure_year_prefers_year_named_in_text():
    assert figure_year(_p("Countable monthly income no more than $4,760 in 2025", eff="2026-03-23")) == 2025
    assert figure_year(_p("| Individual | $4,855 |", eff="2026-03-01")) == 2026
    assert figure_year(_p("no year here", eff="")) is None


def test_prefer_latest_withholds_older_year_for_same_program_only():
    guide = _p("JACC income $4,760 (2025)", title="2026 Program Guide")
    table = _p("JACC income $4,855 (2026)", title="Side-by-Side (2026)")
    other = _p("PACE serves people 55 and older (2024)", program="PACE")
    kept, dropped = prefer_latest([guide, table, other])
    assert [p.title for p in kept] == ["Side-by-Side (2026)", "t"]  # PACE untouched: no newer PACE passage
    assert dropped[0]["year"] == 2025 and dropped[0]["superseded_by_year"] == 2026


def test_retrieve_applies_policy_and_reports_superseded():
    rt = boto3.client("bedrock-agent-runtime", region_name="us-east-1")
    old = {**HIT, "content": {"text": "JACC income limit $4,760 for 2025"}, "metadata": {**HIT["metadata"], "effective_date": "2026-03-23"}}
    new = {**HIT, "content": {"text": "JACC income limit $4,855 for 2026"}, "metadata": {**HIT["metadata"], "effective_date": "2026-03-01"}}
    with Stubber(rt) as stub:
        stub.add_response("retrieve", {"retrievalResults": [old, new]})
        r = retrieve(rt, "KBTEST1234", "JACC income limit", 2, None)
    assert len(r.passages) == 1 and "4,855" in r.passages[0].text and len(r.superseded) == 1
    with Stubber(rt) as stub:
        stub.add_response("retrieve", {"retrievalResults": [old, new]}, {"knowledgeBaseId": "KBTEST1234", "retrievalQuery": {"text": "JACC income limit"}, "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 2}}})
        r2 = retrieve(rt, "KBTEST1234", "JACC income limit", 2, None, latest_only=False)
    assert len(r2.passages) == 2
