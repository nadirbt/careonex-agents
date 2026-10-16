"""Shared validation and safe persistence for read-only batch RAG comparisons.

This module never grades evidence automatically. Previously reviewed grades may
be reused only for the same query, KB, passage ID, and *exact passage text*.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


def load_questions(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ValueError("Questions file must contain a 'questions' list")
    questions = data["questions"]
    if not questions:
        raise ValueError("Questions list cannot be empty")
    ids = set()
    allowed = {"id", "query", "program", "year", "jurisdiction", "source_id"}
    for i, case in enumerate(questions):
        if not isinstance(case, dict):
            raise ValueError(f"Question {i + 1} must be an object")
        case_id, question = case.get("id"), case.get("query")
        if not isinstance(case_id, str) or not ID_PATTERN.fullmatch(case_id):
            raise ValueError(f"Question {i + 1} has invalid id; use lowercase letters, numbers, - or _")
        if case_id in ids:
            raise ValueError(f"Duplicate question id: {case_id}")
        ids.add(case_id)
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"Question {case_id} must have a nonempty query")
        if len(question) > 2000:
            raise ValueError(f"Question {case_id} is too long")
        unknown = set(case) - allowed
        if unknown:
            raise ValueError(f"Question {case_id} has unsupported fields: {sorted(unknown)}")
        for name in ("program", "jurisdiction", "source_id"):
            if case.get(name) is not None and (not isinstance(case[name], str) or not case[name].strip()):
                raise ValueError(f"Question {case_id} has invalid {name}")
        if "year" in case and case["year"] is not None and (type(case["year"]) is not int or not 2020 <= case["year"] <= 2100):
            raise ValueError(f"Question {case_id} has invalid year")
    return questions


def write_json(path: Path, data: dict) -> None:
    """Write using an atomic replace, retaining completed files after interruption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".tmp")
    staged.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(staged, path)


def ensure_matching_report(report: dict, case: dict, kb_id: str, top_k: int, candidate_k: int,
                           candidate_mode: str = "expanded") -> None:
    if (report.get("query") != case["query"] or report.get("knowledge_base_id") != kb_id
            or report.get("top_k") != top_k or report.get("candidate_k") != candidate_k
            or report.get("candidate_mode", "expanded") != candidate_mode):
        raise ValueError(f"Existing report for {case['id']} uses different settings/question; use another output folder")


def reuse_labels(report: dict, sources: list[dict]) -> int:
    """Exact-match reuse only, never automatic judging of new or changed text."""
    reusable: dict[tuple[str, str], int] = {}
    for old in sources:
        if (old.get("query") != report.get("query") or
                old.get("knowledge_base_id") != report.get("knowledge_base_id")):
            continue
        for passage in old.get("candidate_pool", []):
            grade = passage.get("relevance")
            if type(grade) is int and grade in (0, 1, 2):
                reusable[(passage["id"], passage["text"])] = grade
    reused = 0
    for passage in report["candidate_pool"]:
        key = (passage["id"], passage["text"])
        if passage.get("relevance") is None and key in reusable:
            passage["relevance"] = reusable[key]
            reused += 1
    return reused


def load_seeds(folder: Path | None) -> list[dict]:
    if folder is None:
        return []
    if not folder.is_dir():
        raise ValueError(f"Seed folder does not exist: {folder}")
    seeds = []
    for path in sorted(folder.glob("*.json")):
        old = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(old, dict) and isinstance(old.get("candidate_pool"), list):
            seeds.append(old)
    return seeds
