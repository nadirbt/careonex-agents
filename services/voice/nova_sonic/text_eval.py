"""Evaluate voice-to-voice *content* using user/assistant transcripts and human gold text.

Works on existing voice-smoke.json, WITHOUT an AWS call. Does not evaluate
actual voice/audio quality, latency, pronunciation, interruptions or true
factual entailment. Human review is REQUIRED for safety/eligibility claims.

Usage:
  uv run --python 3.12 --directory services/voice python -m nova_sonic.text_eval \\
    --report voice-smoke-output/voice-smoke.json --gold evaluation/medicaid_voice_gold.json
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from math import sqrt

WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def words(text: str) -> list[str]:
    return WORD_RE.findall(text.casefold())


def word_error_rate(reference: str, hypothesis: str) -> float | None:
    ref, hyp = words(reference), words(hypothesis)
    if not ref:
        return None
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        nxt = [i]
        for j, h in enumerate(hyp, 1):
            nxt.append(min(row[j] + 1, nxt[j - 1] + 1, row[j - 1] + (r != h)))
        row = nxt
    return round(row[-1] / len(ref), 4)


def text_cosine(a: str, b: str) -> float:
    """Bag-of-words cosine for descriptive comparison, not semantic entailment."""
    x, y = Counter(words(a)), Counter(words(b))
    dot = sum(v * y.get(k, 0) for k, v in x.items())
    denom = sqrt(sum(v*v for v in x.values()) * sum(v*v for v in y.values()))
    return round(dot / denom, 4) if denom else 0.0


def _contains_phrase(text: str, phrase: str) -> bool:
    return " ".join(words(phrase)) in " ".join(words(text))


def evaluate(report: dict, gold: dict) -> dict:
    transcripts = report.get("transcripts", [])
    user_text = " ".join(t[1] for t in transcripts if len(t) >= 2 and t[0] == "USER")
    assistant_text = " ".join(t[1] for t in transcripts if len(t) >= 2 and t[0] == "ASSISTANT")
    if not assistant_text:
        raise ValueError("voice report contains no assistant transcript")
    calls = [x for x in report.get("tool_calls", []) if x.get("name") == "lookup_program_info"]
    usable = []
    for call in calls:
        try:
            payload = json.loads(call.get("output", "{}"))
        except (ValueError, TypeError):
            payload = {}
        if not payload.get("error") and call.get("result_sent"):
            usable.extend(p for p in payload.get("passages", []) if p.get("text"))
    required = gold.get("required_phrases", [])
    prohibited = gold.get("prohibited_phrases", [])
    if not isinstance(required, list) or not isinstance(prohibited, list):
        raise ValueError("gold required_phrases and prohibited_phrases must be lists")
    return {
        "user_transcript": user_text, "assistant_transcript": assistant_text,
        "input_wer_vs_script": word_error_rate(gold.get("reference_question", ""), user_text),
        "answer_word_cosine_vs_gold": text_cosine(assistant_text, gold.get("reference_answer", "")) if gold.get("reference_answer") else None,
        "answer_word_cosine_vs_text_baseline": text_cosine(assistant_text, gold["text_baseline_answer"]) if gold.get("text_baseline_answer") else None,
        "required_phrases_found": {p: _contains_phrase(assistant_text, p) for p in required},
        "prohibited_phrases_found": {p: _contains_phrase(assistant_text, p) for p in prohibited},
        "rag_invoked": bool(calls), "rag_passages_returned_and_sent": len(usable),
        "retrieval_evidence_word_overlap": round(max((text_cosine(assistant_text, p["text"]) for p in usable), default=0.0), 4) if usable else None,
        "human_review_required": True,
        "limitations": "Text metrics do not prove factual faithfulness, legal/medical correctness, speech quality or end-to-end voice latency. The Sonic transcript is not an independent ASR measurement.",
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = evaluate(json.loads(args.report.read_text(encoding="utf-8")), json.loads(args.gold.read_text(encoding="utf-8")))
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
