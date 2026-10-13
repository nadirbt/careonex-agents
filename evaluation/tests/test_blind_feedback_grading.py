"""Offline tests for blind judging; no network or model grading."""
import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blind_feedback_grading import export_csv, score_csv


@pytest.fixture
def sample():
    from hashlib import sha256
    def hit(text):
        return {"text": text, "sha256": sha256(text.encode()).hexdigest(), "relevance": None}
    return {"planner": "feedback", "top_k": 5, "cases": [{
        "id": "q1", "query": "How can I get bathing help?",
        "runs": {"baseline_child": {"passages": [hit("Same evidence"), hit("Other evidence")], "latency_ms": 10},
                 "feedback_child": {"passages": [hit("Same evidence"), hit("New evidence")], "latency_ms": 20}}
    }]}


def test_export_is_blind_and_shared_passages_are_graded_once(sample, tmp_path):
    csv_path = tmp_path / 'review.csv'
    assert export_csv(sample, csv_path) == 3
    with csv_path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert set(reader.fieldnames) == {"item_id", "question_id", "question", "passage_text", "grade"}
    assert all(not r['grade'] for r in rows)
    assert len([r for r in rows if r['passage_text'] == 'Same evidence']) == 1
    for r in rows:
        r['grade'] = '2' if r['passage_text'] == 'Same evidence' else '0'
    with csv_path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, reader.fieldnames)
        w.writeheader(); w.writerows(rows)
    score = score_csv(sample, csv_path)
    assert score['fully_graded_cases'] == 1
    assert score['averages']['baseline_child']['precision_at_5'] == .2
    assert score['averages']['feedback_child']['mrr_at_5'] == 1
    assert sample['cases'][0]['runs']['baseline_child']['passages'][0]['relevance'] is None


def test_partial_grade_not_falsely_reported_as_complete(sample, tmp_path):
    path = tmp_path / 'review.csv'
    export_csv(sample, path)
    summary = score_csv(sample, path)
    assert summary['fully_graded_cases'] == 0
    assert summary['ungraded_cases'] == 1


def test_grade_input_tampering_rejected(sample, tmp_path):
    path = tmp_path / 'review.csv'
    export_csv(sample, path)
    original = path.read_text(encoding='utf-8-sig')
    with path.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    rows[0]['passage_text'] = 'Altered evidence'
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, rows[0].keys());w.writeheader();w.writerows(rows)
    with pytest.raises(ValueError, match='changed'):
        score_csv(sample, path)
