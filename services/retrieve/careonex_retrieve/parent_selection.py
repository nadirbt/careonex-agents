"""Select short, non-duplicative evidence from a staging-only parent section.

This does not infer missing facts, rerank children, or fetch S3 itself. It only
extracts verbatim additional sentences/bullets from a validated parent.
"""
from __future__ import annotations

import re
from careonex_retrieve.feedback_expansion import (_BROAD, _terms, _specific_qualifiers, _conflicting_qualifiers, _source_scope_conflicts)


def select_parent_excerpt(question: str, child: str, parent: str | None,
                          *, max_chars: int = 850) -> str | None:
    if not parent or not question.strip() or max_chars < 80:
        return None
    keywords = _terms(question) - _BROAD - {'caregiver', 'caregiv', 'family'}
    if not keywords:
        return None
    if _conflicting_qualifiers(_specific_qualifiers(question), '', parent):
        return None
    child_norm = ' '.join(child.casefold().split())
    entries: list[tuple[float, str]] = []
    seen: set[str] = set()
    # Preserve a complete source line or sentence; no generated content.
    for raw in parent.splitlines()[:160]:
        raw = raw.strip()
        if raw.startswith('```') or raw.startswith('|') or not raw:
            continue
        parts = re.split(r'(?<=[.!?])\s+(?=[A-Z])', raw)
        for item in parts:
            phrase = item.strip(' -•*\t')
            if len(phrase) < 18 or len(phrase) > 320:
                continue
            if phrase.casefold() in seen or ' '.join(phrase.casefold().split()) in child_norm:
                continue
            if re.search(r'(?i)ignore previous|system prompt|assistant:|https?://|\bexecute\b', phrase):
                continue
            if _source_scope_conflicts(question, phrase):
                continue
            tokens = _terms(phrase) - _BROAD
            overlap = tokens & keywords
            if not overlap:
                continue
            # A partial topical word is not sufficient for a multi-facet
            # question: especially fees, insurance coverage and scheduling.
            if len(keywords) >= 3 and len(overlap) == 1:
                important = {'meal', 'prepar', 'prepare', 'cook', 'dress', 'shower',
                             'bath', 'mov', 'move'}
                if not (overlap & important and not _source_scope_conflicts(question, phrase)):
                    continue
            # Avoid a superficially similar parent clause that merely repeats
            # a word already present in the child but adds no new evidence.
            new_tokens = tokens - _terms(child)
            if not new_tokens:
                continue
            score = 4 * len(overlap) + min(3, len(new_tokens)) - len(phrase) / 250
            key = phrase.casefold()
            if key not in seen:
                entries.append((score, phrase))
                seen.add(key)
    entries.sort(key=lambda x: -x[0])
    chosen = []
    current = 0
    for _, phrase in entries:
        if current + len(phrase) + (1 if chosen else 0) > max_chars:
            continue
        chosen.append(phrase)
        current += len(phrase) + (1 if len(chosen) > 1 else 0)
        if len(chosen) >= 2:
            break
    return '\n'.join(chosen) if chosen else None


def select_best_parent_contexts(question: str, child_texts: list[str],
                                parent_texts: list[str | None],
                                *, max_excerpts: int = 1) -> list[str | None]:
    """Choose the most question-matching **new** excerpt across ranked hits.

    Returns one slot per child; no new ranked results. This may choose a
    lower-ranked child's parent when it actually contains the missing detail.
    """
    if len(child_texts) != len(parent_texts):
        raise ValueError('child and parent lengths must match')
    if not 1 <= max_excerpts <= 2:
        raise ValueError('max_excerpts must be 1 or 2')
    all_children = '\n'.join(child_texts)
    keywords = _terms(question) - _BROAD - {'caregiv', 'caregiver'}
    candidates = []
    for i, parent in enumerate(parent_texts):
        if not parent:
            continue
        snippet = select_parent_excerpt(question, all_children, parent)
        if not snippet:
            continue
        tokens = _terms(snippet) - _BROAD
        match = tokens & keywords
        # Coverage before rank is critical: 'preparing meals' in the fifth
        # parent outranks 'daily meals in a group setting' in the first.
        score = 6 * len(match) / max(1, len(keywords)) + len(match) - i * .08
        if re.search(r'(?i)\bat home\b', question) and re.search(
                r'(?i)\b(?:group setting|congregate|at a center)\b', snippet):
            score -= 3
        candidates.append((score, i, snippet))
    candidates.sort(key=lambda x: (-x[0], x[1]))
    result: list[str | None] = [None] * len(child_texts)
    seen = set()
    for _, i, snippet in candidates:
        if snippet.casefold() in seen:
            continue
        result[i] = snippet
        seen.add(snippet.casefold())
        if len(seen) == max_excerpts:
            break
    return result
