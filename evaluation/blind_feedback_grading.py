"""Offline blind passage grading for the saved baseline/feedback retrieval experiment.

Export deduplicates passage texts by question and hides arm/rank. Independent
judges fill 0/1/2. Score verifies every row against the source snapshot,
propagates each grade to both child arms, and computes metrics only for fully
judged questions. Neither command calls AWS or modifies the input fixture.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from intent_parent_experiment import score_retrieval

FIELDS = ("item_id", "question_id", "question", "passage_text", "grade")


def item_id(case_id: str, question: str, sha: str) -> str:
    return hashlib.sha256(json.dumps([case_id, question, sha], ensure_ascii=False).encode()).hexdigest()


def make_rows(data: dict) -> list[dict[str, str]]:
    if data.get("planner") != "feedback" or data.get("top_k") != 5:
        raise ValueError("expected a saved feedback experiment with top_k=5")
    output: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for case in data["cases"]:
        pooled: dict[str, str] = {}
        for arm in ("baseline_child", "feedback_child"):
            for passage in case["runs"][arm]["passages"]:
                sha = hashlib.sha256(passage["text"].encode("utf-8")).hexdigest()
                if sha != passage["sha256"]:
                    raise ValueError(f"passage SHA mismatch in {case['id']}")
                if sha in pooled and pooled[sha] != passage["text"]:
                    raise ValueError("inconsistent passage content")
                pooled[sha] = passage["text"]
        for sha, passage_text in pooled.items():
            iid = item_id(case["id"], case["query"], sha)
            if iid in seen_ids:
                raise ValueError("duplicate case or passage ID")
            seen_ids.add(iid)
            output.append({"item_id": iid, "question_id": case["id"],
                           "question": case["query"], "passage_text": passage_text, "grade": ""})
    # Deterministic shuffle: prevents rank/order and arm being revealed to the grader.
    return sorted(output, key=lambda row: hashlib.sha256(("blind-feedback-v1:" + row["item_id"]).encode()).hexdigest())


def export_csv(data: dict, out_path: Path) -> int:
    rows = make_rows(data)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def score_csv(data: dict, grades_path: Path) -> dict:
    expected = {row["item_id"]: row for row in make_rows(data)}
    reviewed: dict[str, int] = {}
    with grades_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or set(reader.fieldnames) != set(FIELDS):
            raise ValueError("CSV columns changed; expected exact blind grading columns")
        for row in reader:
            iid = row.get("item_id", "")
            if iid not in expected or iid in reviewed:
                raise ValueError(f"unknown or duplicate grading item: {iid}")
            record = expected[iid]
            if any(row.get(key) != record[key] for key in ("question_id", "question", "passage_text")):
                raise ValueError(f"question/passage was changed: {iid}")
            raw = row.get("grade", "").strip()
            if raw not in ("", "0", "1", "2"):
                raise ValueError(f"grade must be blank, 0, 1, or 2: {iid}")
            if raw:
                reviewed[iid] = int(raw)
    # Rows must not have been removed, even if some grades remain blank.
    # Compare IDs from a second reading to distinguish blanks vs missing rows.
    with grades_path.open(encoding="utf-8-sig", newline="") as f:
        input_ids = [row["item_id"] for row in csv.DictReader(f)]
    if set(input_ids) != set(expected) or len(input_ids) != len(expected):
        raise ValueError("grading sheet missing or duplicated items")
    evaluated = json.loads(json.dumps(data))
    for case in evaluated["cases"]:
        for arm in ("baseline_child", "feedback_child"):
            for passage in case["runs"][arm]["passages"]:
                iid = item_id(case["id"], case["query"], passage["sha256"])
                passage["relevance"] = reviewed.get(iid)
    summary = score_retrieval(evaluated)
    summary["grading"] = {"judged_unique_passages": len(reviewed),
                          "total_unique_passages": len(expected),
                          "judging_method": "blind offline human grades, 0/1/2; ungraded cases excluded",
                          "source_kb": "historical experiment snapshot; no AWS calls"}
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "score"):
        sp = sub.add_parser(name)
        sp.add_argument("--input", required=True, type=Path)
        sp.add_argument("--output", required=True, type=Path)
        if name == "score":
            sp.add_argument("--grades", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.input.resolve() == args.output.resolve():
        parser.error("never overwrite the historical input")
    source = json.loads(args.input.read_text(encoding="utf-8"))
    if args.command == "export":
        count = export_csv(source, args.output)
        print(f"Exported {count} blind judging items; all 40 cases require independent human grades.")
    else:
        summary = score_csv(source, args.grades)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({k: summary[k] for k in ("total_cases", "fully_graded_cases", "grading")}, indent=2))


if __name__ == "__main__":
    main()
