import pytest
from nova_sonic.text_eval import word_error_rate, text_cosine, evaluate


def test_word_error_rate_and_similarity():
    assert word_error_rate("hello world", "hello world") == 0
    assert word_error_rate("hello world", "hello") == 0.5
    assert word_error_rate("", "hello") is None
    assert text_cosine("medicaid covers care", "medicaid covers care") == 1
    assert text_cosine("blue green", "medicaid care") == 0


def test_text_to_text_voice_evaluation_from_smoke_report():
    report = {"transcripts": [["USER", "Does Medicaid cover bathing"],
                              ["ASSISTANT", "Medicaid may cover bathing with assessment"]],
              "tool_calls": [{"name": "lookup_program_info", "result_sent": True,
                              "output": '{"passages": [{"text": "Medicaid covers bathing after assessment"}]}' }]}
    gold = {"reference_question": "Does Medicaid cover bathing",
            "reference_answer": "Medicaid may cover bathing with assessment",
            "required_phrases": ["bathing"], "prohibited_phrases": ["guaranteed eligible"]}
    r = evaluate(report, gold)
    assert r["input_wer_vs_script"] == 0
    assert r["answer_word_cosine_vs_gold"] == 1
    assert r["rag_invoked"] is True and r["rag_passages_returned_and_sent"] == 1
    assert r["required_phrases_found"]["bathing"] is True
    assert r["prohibited_phrases_found"]["guaranteed eligible"] is False
    assert r["human_review_required"] is True


def test_no_hallucinated_grounding_for_failed_tool():
    report = {"transcripts": [["ASSISTANT", "I cannot verify the details"]],
              "tool_calls": [{"name": "lookup_program_info", "result_sent": False, "output": '{"error":"timed out"}'}]}
    r = evaluate(report, {"reference_answer":"Please follow up"})
    assert r["rag_invoked"] is True
    assert r["rag_passages_returned_and_sent"] == 0
    assert r["retrieval_evidence_word_overlap"] is None
    with pytest.raises(ValueError):
        evaluate({"transcripts": []}, {})


def test_cli_is_opt_in_for_sensitive_live_transcripts():
    from pathlib import Path
    code = (Path(__file__).parents[1] / "nova_sonic" / "__main__.py").read_text()
    assert 'eval_report: Path | None = None' in code
    assert 'if eval_report is not None' in code
    assert '--eval-report' in code
