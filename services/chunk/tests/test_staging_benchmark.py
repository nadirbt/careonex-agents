"""Smoke tests for offline scoring; no Bedrock calls."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "evaluation" / "chunking_retrieval_benchmark.py"
spec = importlib.util.spec_from_file_location("chunk_benchmark", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _passage(grade, content):
    import hashlib
    return {"rank": 1, "text": content, "sha256": hashlib.sha256(content.encode()).hexdigest(), "relevance": grade}


def test_benchmark_requires_all_manual_labels(tmp_path):
    data = {"top_k": 3, "cases": [{"id": "c", "runs": {
        "legacy": {"latency_ms": 10, "passages": [_passage(0, "irrelevant")]},
        "section": {"latency_ms": 11, "passages": [_passage(2, "relevant")]},
        "hierarchical": {"latency_ms": 12, "passages": [_passage(None, "third")]}}}]}
    file = tmp_path / "batch.json"
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Unreviewed"):
        module.score(file)
    data["cases"][0]["runs"]["hierarchical"]["passages"][0]["relevance"] = 1
    file.write_text(json.dumps(data))
    out = module.score(file)
    assert out["averages"]["section"]["mrr_at_k"] == 1
    assert out["averages"]["legacy"]["precision_at_k"] == 0
    assert out["averages"]["hierarchical"]["ndcg_at_k"] > 0


def test_parent_uri_reconstruction(monkeypatch):
    class FakeS3:
        def get_object(self, **kwargs):
            assert kwargs["Bucket"] == "ac215-kb"
            assert kwargs["Key"] == "experiments/chunking/abc/hierarchical/parents/doc/parent1.md"
            return {"Body": type("Body", (), {"read": lambda self: b"full parent text"})()}
    uri = "s3://ac215-kb/experiments/chunking/abc/hierarchical/chunks/doc/0001.md"
    assert module._parent_context(FakeS3(), uri, "parent1") == "full parent text"
