"""Batch evaluation is testable without AWS calls or expensive embedding models."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from careonex_retrieve import cli
from careonex_retrieve.batch_compare import (load_questions, reuse_labels,
                                            ensure_matching_report, write_json)
from careonex_retrieve.batch_eval import evaluate_batch
from careonex_retrieve.retriever import Passage


def fixture_questions(tmp_path: Path) -> Path:
    filename = tmp_path / "questions.json"
    write_json(filename, {"questions": [
        {"id": "medicaid_bathing", "query": "Does Medicaid cover bathing at home?", "program": "Medicaid"},
        {"id": "jacc_limits", "query": "How much income can I have for JACC?", "program": "JACC"},
    ]})
    return filename


def fake_passage(name: str) -> Passage:
    return Passage(name, None, "https://nj.gov", name, "All DoAS programs",
                   "2026-01-01", name, name)


def test_question_dataset_is_valid_and_varied():
    root = Path(__file__).resolve().parents[3]
    cases = load_questions(root / "evaluation" / "batch_questions.json")
    assert len(cases) == 25
    assert len({c["id"] for c in cases}) == 25
    assert any(c["program"] == "Medicaid" for c in cases)
    assert any(c["program"] == "JACC" for c in cases)
    assert any(c["program"] == "PACE" for c in cases)
    assert any(c["program"] is None for c in cases)


def test_invalid_questions_rejected(tmp_path):
    path = tmp_path / "invalid.json"
    for cases in ([{"id": "a/b", "query": "x"}],
                  [{"id": "same", "query": "x"}, {"id": "same", "query": "y"}],
                  [{"id": "ab", "query": ""}],
                  [{"id": "ab", "query": "hi", "year": True}],
                  [{"id": "ab", "query": "hi", "relevance": 2}]):
        write_json(path, {"questions": cases})
        with pytest.raises(ValueError):
            load_questions(path)


def test_exact_label_reuse_but_not_changed_passages():
    old = {"query": "q", "knowledge_base_id": "KB", "candidate_pool": [
        {"id": "one", "text": "PCA", "relevance": 2},
        {"id": "two", "text": "MLTSS", "relevance": 1}]}
    new = {"query": "q", "knowledge_base_id": "KB", "candidate_pool": [
        {"id": "one", "text": "PCA", "relevance": None},
        {"id": "two", "text": "different content", "relevance": None},
        {"id": "three", "text": "JACC", "relevance": None}]}
    assert reuse_labels(new, [old]) == 1
    assert [p["relevance"] for p in new["candidate_pool"]] == [2, None, None]
    assert reuse_labels(new, [{**old, "query": "different"}]) == 0
    assert reuse_labels(new, [{**old, "knowledge_base_id": "other"}]) == 0


def test_existing_settings_must_match():
    with pytest.raises(ValueError):
        ensure_matching_report({"query": "wrong", "knowledge_base_id": "KB", "top_k": 3, "candidate_k": 10},
                               {"id": "test", "query": "right"}, "KB", 3, 10)


def test_batch_e2e_resume_grade_and_summarize(tmp_path, monkeypatch):
    questions = fixture_questions(tmp_path)
    calls = []
    monkeypatch.setattr(cli.config, "knowledge_base_id", lambda: "KB")
    monkeypatch.setattr(cli.config, "session", lambda: SimpleNamespace(client=lambda x: "FAKE"))

    def fake_retrieve(runtime, kb, query, top_k, flt, search_mode, program_hint):
        calls.append((query, search_mode))
        passages = [fake_passage("irrelevant"), fake_passage("useful")] if search_mode == "baseline" else [fake_passage("useful"), fake_passage("irrelevant")]
        return SimpleNamespace(passages=passages, latency_ms=1000 if search_mode == "baseline" else 600,
                               queries_used=[query] if search_mode == "baseline" else [query, query + " expanded"])
    monkeypatch.setattr(cli, "retrieve", fake_retrieve)

    out = tmp_path / "results"
    args = SimpleNamespace(questions=questions, output_dir=out, top_k=2, candidate_k=5,
                           limit=None, seed_dir=None, force=False)
    assert cli.cmd_batch_compare(args) == 0
    assert len(calls) == 4
    assert (out / "manifest.json").exists()

    first = out / "cases" / "medicaid_bathing.json"
    doc = json.loads(first.read_text())
    assert doc["runs"]["baseline"]["ids"] == ["s3:irrelevant", "s3:useful"]
    assert all(p["relevance"] is None for p in doc["candidate_pool"])
    assert evaluate_batch(out)["graded_questions"] == 0
    assert evaluate_batch(out)["ungraded_questions"] == 2

    # A human labels one case. Restart does not overwrite any saved grades,
    # issue AWS requests or artificially include ungraded cases in macro means.
    for passage in doc["candidate_pool"]:
        passage["relevance"] = 2 if passage["id"] == "s3:useful" else 0
    write_json(first, doc)
    assert cli.cmd_batch_compare(args) == 0
    assert len(calls) == 4
    assert json.loads(first.read_text())["candidate_pool"][1]["relevance"] == 2
    summary = evaluate_batch(out)
    assert summary["graded_questions"] == 1
    assert summary["ungraded_questions"] == 1
    assert summary["summary_all_graded"]["baseline"]["mrr_at_k"] == .5
    assert summary["summary_all_graded"]["expanded"]["mrr_at_k"] == 1.
    assert summary["summary_all_graded"]["expanded"]["median_latency_ms"] == 600
    assert summary["summary_expansion_triggered_only"]["expanded"]["ndcg_at_k"] == 1
    # Force replaces old files only when explicitly requested.
    args.force = True
    assert cli.cmd_batch_compare(args) == 0
    assert json.loads(first.read_text())["candidate_pool"][1]["relevance"] is None


def test_seed_reuse_in_real_batch(tmp_path, monkeypatch):
    questions = fixture_questions(tmp_path)
    monkeypatch.setattr(cli.config, "knowledge_base_id", lambda: "KB")
    monkeypatch.setattr(cli.config, "session", lambda: SimpleNamespace(client=lambda x: "FAKE"))
    monkeypatch.setattr(cli, "retrieve", lambda *a, search_mode=None, **k: SimpleNamespace(
        passages=[fake_passage("right")], latency_ms=1, queries_used=[a[2]]))
    seed_dir = tmp_path / "seeds"
    write_json(seed_dir / "saved.json", {
        "query": "Does Medicaid cover bathing at home?", "knowledge_base_id": "KB",
        "candidate_pool": [{"id": "s3:right", "text": "right", "relevance": 2}]})
    args = SimpleNamespace(questions=questions, output_dir=tmp_path / "batch", top_k=1,
                           candidate_k=5, limit=1, seed_dir=seed_dir, force=False)
    assert cli.cmd_batch_compare(args) == 0
    doc = json.loads((args.output_dir / "cases" / "medicaid_bathing.json").read_text())
    assert doc["candidate_pool"][0]["relevance"] == 2
    assert evaluate_batch(args.output_dir)["graded_questions"] == 1


def test_manifest_rejects_path_traversal(tmp_path):
    write_json(tmp_path / "manifest.json", {"cases": [{"id": "../bad", "file": "../bad"}],
                                             "knowledge_base_id": "KB", "top_k": 3, "candidate_k": 10})
    with pytest.raises(ValueError):
        evaluate_batch(tmp_path)
