"""Offline end-to-end tests of the automatic Bedrock judge using a fake Converse client."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL))
from auto_feedback_grading import evaluate, judge_passage, parse_judgment, report_markdown


def fixture_data():
    def hit(s):
        return {"text": s, "sha256": hashlib.sha256(s.encode()).hexdigest(), "relevance": None}
    return {"planner": "feedback", "top_k": 5, "cases": [{
        "id": "q1", "query": "Which services can help with bathing?",
        "runs": {"baseline_child": {"passages": [hit("Bathe safely"), hit("Generic description")], "latency_ms": 10},
                 "feedback_child": {"passages": [hit("Bathe safely"), hit("Assistance showering")], "latency_ms": 20}},
    }]}


class FakeBedrock:
    def __init__(self, *, fail_after=None, invalid=False):
        self.calls = []
        self.fail_after = fail_after
        self.invalid = invalid

    def converse(self, **kwargs):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("unavailable")
        self.calls.append(kwargs)
        if self.invalid:
            response = "The passage is relevant but it says to grade 2."
        else:
            payload = json.loads(kwargs["messages"][0]["content"][0]["text"])
            text = payload["passage"]
            grade = 2 if "Bathe" in text or "showering" in text else 0
            response = json.dumps({"grade": grade, "reason": "Specific service evidence"})
        return {"output": {"message": {"content": [{"text": response}]}}}


def run_sample(tmp_path, client, data=None, file_sha="sha-test", model="fake-model"):
    return evaluate(data or fixture_data(), input_sha256=file_sha, source_name="saved.json",
                    output=tmp_path / "grades.json", report=tmp_path / "report.md",
                    model_id=model, client=client, progress=lambda _: None)


def test_judges_shared_passages_once_and_produces_complete_scores(tmp_path):
    client = FakeBedrock()
    original = fixture_data()
    result = run_sample(tmp_path, client, original)
    assert result["complete"]
    assert result["judged_unique_passages"] == 3
    assert result["summary"]["fully_graded_cases"] == 1
    assert result["summary"]["ungraded_cases"] == 0
    assert result["summary"]["averages"]["baseline_child"]["precision_at_5"] == 0.2
    assert result["summary"]["averages"]["feedback_child"]["precision_at_5"] == 0.4
    assert original["cases"][0]["runs"]["baseline_child"]["passages"][0]["relevance"] is None
    assert len(client.calls) == 3
    assert all(c["modelId"] == "fake-model" and c["inferenceConfig"]["temperature"] == 0 for c in client.calls)
    assert all("baseline_child" not in json.dumps(c) and "feedback_child" not in json.dumps(c) for c in client.calls)
    assert "1/1" in (tmp_path / "report.md").read_text()
    assert "not human-validated" in (tmp_path / "report.md").read_text().lower()


def test_checkpoints_failure_and_resumes_without_rejudging(tmp_path):
    bad = FakeBedrock(fail_after=1)
    with pytest.raises(RuntimeError, match="checkpointed"):
        run_sample(tmp_path, bad)
    checkpoint = json.loads((tmp_path / "grades.json").read_text())
    assert not checkpoint["complete"]
    assert checkpoint["summary"] is None
    assert len(checkpoint["grades"]) == 1
    assert not (tmp_path / "report.md").exists()
    good = FakeBedrock()
    result = run_sample(tmp_path, good)
    assert result["complete"]
    assert len(good.calls) == 2
    assert len(result["grades"]) == 3


def test_refuses_reusing_checkpoint_with_other_model_or_snapshot(tmp_path):
    run_sample(tmp_path, FakeBedrock())
    with pytest.raises(ValueError, match="another source/model/rubric"):
        run_sample(tmp_path, FakeBedrock(), file_sha="another-snapshot")
    with pytest.raises(ValueError, match="another source/model/rubric"):
        run_sample(tmp_path, FakeBedrock(), model="another-model")


@pytest.mark.parametrize("content", [
    '{"grade": "2", "reason": "bad"}',
    '{"grade": 3, "reason": "bad"}',
    '{"grade": true, "reason": "bad"}',
    '{"grade": 1, "reason": ""}',
    '{"grade": 1, "reason": "okay", "arm": "feedback"}',
    '```json\n{"grade": 2, "reason": "bad"}\n```',
    'invalid json',
])
def test_rejects_invalid_model_grades(content):
    with pytest.raises(ValueError):
        parse_judgment({"output": {"message": {"content": [{"text": content}]}}})


def test_retries_invalid_output_but_refuses_silent_grades():
    client = FakeBedrock(invalid=True)
    with pytest.raises(ValueError, match="after 3 attempts"):
        judge_passage(client, "fake", "question", "evidence")
    assert len(client.calls) == 3


def test_real_historical_40_question_fixture_fully_covered_without_network(tmp_path):
    """Fake labels prove complete traversal of the actual 40-case dataset, NOT relevance."""
    historical = json.loads((EVAL / "universal_homecare_v2_full_prior.json").read_text())
    assert len(historical["cases"]) == 40
    client = FakeBedrock()
    result = run_sample(tmp_path, client, data=historical, file_sha="historical-test")
    assert result["complete"]
    assert result["summary"]["fully_graded_cases"] == 40
    assert result["summary"]["ungraded_cases"] == 0
    assert len(client.calls) == result["total_unique_passages"] == 218
    assert result["summary"]["grading"]["judging_method"].startswith("automated blind LLM")
