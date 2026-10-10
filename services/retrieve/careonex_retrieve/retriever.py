"""Thin, typed wrapper over bedrock-agent-runtime Retrieve with metadata filtering and latency logging."""

from __future__ import annotations

import logging
import os
import re
import time
import hashlib
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from careonex_retrieve.rerank import rerank_passages
from careonex_retrieve.query_expansion import plan_queries, plan_intent_queries
from careonex_retrieve.feedback_expansion import plan_feedback_queries, corroborates_original_question
from careonex_retrieve.parent_context import fetch_parent
from careonex_retrieve.parent_selection import select_best_parent_contexts

log = logging.getLogger(__name__)


@dataclass
class Passage:
    text: str
    score: float | None
    source_url: str | None
    title: str | None
    program: str | None
    effective_date: str | None
    heading_path: str | None
    s3_key: str | None
    table_verified: bool | None = None  # required for the ambiguous DoAS side-by-side PDF
    rerank_score: float | None = None  # opt-in similarity score, not raw Bedrock cosine
    fusion_score: float | None = None  # RRF score in expanded mode; not a Bedrock score
    parent_id: str | None = None
    parent_text: str | None = None  # read-only staging context; never counts as another hit


@dataclass
class RetrievalResult:
    query: str
    knowledge_base_id: str
    filter: dict | None
    top_k: int
    latency_ms: int
    passages: list[Passage] = field(default_factory=list)
    superseded: list[dict] = field(default_factory=list)  # older-year passages withheld from the answer set
    search_mode: str = "baseline"
    queries_used: list[str] = field(default_factory=list)
    planning_latency_ms: int = 0
    parent_fetch_latency_ms: int = 0
    expansion_status: str = "not_requested"  # used, no_expansion, fallback, partial_fallback, not_requested
    planning_reason: str = "not_requested"  # human-readable, model-free planner decision

    def as_dict(self) -> dict:
        return {**asdict(self), "passages": [asdict(p) for p in self.passages]}


_YEAR_RE = re.compile(r"\b(20[2-3]\d)\b")


def figure_year(p: Passage) -> int | None:
    """The year a passage's figures apply to: the latest year named in its text (documents often quote
    last year's limits), else the year of its effective date."""
    years = [int(y) for y in _YEAR_RE.findall(p.text or "")]
    if years:
        return max(years)
    if p.effective_date and p.effective_date[:4].isdigit():
        return int(p.effective_date[:4])
    return None


def prefer_latest(passages: list[Passage]) -> tuple[list[Passage], list[dict]]:
    """Policy: when a newer year's passage exists for the same program, older-year passages are not
    offered to the model. They stay in the index; this only shapes the answer set. Passages with no
    detectable year are kept."""
    newest: dict[str, int] = {}
    for p in passages:
        y = figure_year(p)
        if y is not None:
            key = p.program or ""
            newest[key] = max(newest.get(key, 0), y)
    kept, dropped = [], []
    for p in passages:
        y = figure_year(p)
        key = p.program or ""
        if y is not None and y < newest.get(key, y):
            dropped.append({"program": p.program, "year": y, "superseded_by_year": newest[key], "title": p.title, "s3_key": p.s3_key})
        else:
            kept.append(p)
    return kept, dropped


# Exact `program` labels as they appear in the catalog sidecars. S3 Vectors supports only exact-match
# filters (equals / in), so caller-friendly names are mapped to these labels.
PROGRAM_LABELS: dict[str, list[str]] = {
    "mltss": ["NJ FamilyCare / Medicaid MLTSS", "All DoAS programs"],
    "medicaid": ["NJ FamilyCare / Medicaid MLTSS", "All DoAS programs"],
    "nj familycare": ["NJ FamilyCare / Medicaid MLTSS", "All DoAS programs"],
    "familycare": ["NJ FamilyCare / Medicaid MLTSS", "All DoAS programs"],
    "pca": ["NJ FamilyCare / Medicaid MLTSS", "All DoAS programs"],
    "jacc": ["JACC", "All DoAS programs"],
    "pace": ["PACE", "All DoAS programs"],
    "respite": ["Statewide Respite Care Program", "All DoAS programs"],
    "srcp": ["Statewide Respite Care Program", "All DoAS programs"],
    "alzheimer": ["Alzheimer's Adult Day Services Program", "All DoAS programs"],
    "aadsp": ["Alzheimer's Adult Day Services Program", "All DoAS programs"],
    "adult day": ["Alzheimer's Adult Day Services Program", "All DoAS programs"],
    "adrc": ["County Offices on Aging / ADRC", "All DoAS programs"],
    "county": ["County Offices on Aging / ADRC", "All DoAS programs"],
    "medicare": ["Medicare home health benefit"],
    "va": ["VA Homemaker and Home Health Aide Care", "VA Home and Community Based Services", "VA Aid and Attendance / Housebound"],
    "veteran": ["VA Homemaker and Home Health Aide Care", "VA Home and Community Based Services", "VA Aid and Attendance / Housebound"],
}


def program_labels(program: str | None) -> list[str]:
    """Map a caller-supplied program name to exact catalog labels. Unknown names return [] (no filter),
    so semantic search still runs rather than filtering everything out."""
    if not program:
        return []
    key = program.strip().lower()
    for alias, labels in PROGRAM_LABELS.items():  # avoid 'va' matching 'private', etc.
        if re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", key):
            return labels
    exact = {lbl for labels in PROGRAM_LABELS.values() for lbl in labels}
    if program.strip() in exact:
        return [program.strip()]
    return []


def _equals_any(key: str, values: list) -> dict:
    if len(values) == 1:
        return {"equals": {"key": key, "value": values[0]}}
    return {"orAll": [{"equals": {"key": key, "value": v}} for v in values]}


def build_filter(program: str | None = None, year: int | None = None, jurisdiction: str | None = None, source_id: str | None = None) -> dict | None:
    """Bedrock KB filter syntax, restricted to what S3 Vectors supports: equals, combined with andAll/orAll."""
    clauses: list[dict] = []
    labels = program_labels(program)
    if labels:
        clauses.append(_equals_any("program", labels))
    if year is not None:
        clauses.append({"equals": {"key": "year", "value": year}})
    if jurisdiction:
        clauses.append({"equals": {"key": "jurisdiction", "value": jurisdiction}})
    if source_id:
        clauses.append({"equals": {"key": "source_id", "value": source_id}})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"andAll": clauses}


def _passage(item: dict) -> Passage:
    md = item.get("metadata", {}) or {}
    loc = item.get("location", {}) or {}
    s3_uri = (loc.get("s3Location") or {}).get("uri")
    return Passage(
        text=(item.get("content") or {}).get("text", ""),
        score=item.get("score"),
        source_url=md.get("source_url") or md.get("final_url"),
        title=md.get("title"),
        program=md.get("program"),
        effective_date=md.get("effective_date"),
        heading_path=md.get("heading_path"),
        s3_key=s3_uri.split("/", 3)[-1] if s3_uri and s3_uri.startswith("s3://") else s3_uri,
        table_verified=(md.get("table_verified") is True),
        parent_id=md.get("parent_id") if isinstance(md.get("parent_id"), str) else None,
    )


CANDIDATE_MULTIPLIER = 2
MIN_CANDIDATES = 10


def _passage_key(p: Passage) -> str:
    """Stable deduplication across the query variants without conflating documents."""
    if p.s3_key:
        return "s3:" + p.s3_key
    data = "\x00".join((p.source_url or "", p.heading_path or "", p.text))
    return "content:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def reciprocal_rank_fusion(runs: list[list[Passage]], k: int = 60,
                           weights: list[float] | None = None) -> list[Passage]:
    """Fuse ranked lists. Doesn't compare opaque Bedrock scores between queries.

    One passage found by multiple queries receives reciprocal-rank credit from
    each. A duplicate inside a single query contributes only once.
    """
    if k < 1:
        raise ValueError("RRF smoothing constant must be positive")
    if weights is None:
        weights = [1.0] * len(runs)
    if len(weights) != len(runs) or any(weight <= 0 for weight in weights):
        raise ValueError("RRF weights must be positive and match the number of runs")
    records: dict[str, Passage] = {}
    scores: dict[str, float] = {}
    initial_order: dict[str, int] = {}
    for run, weight in zip(runs, weights):
        seen: set[str] = set()
        for rank, passage in enumerate(run, start=1):
            key = _passage_key(passage)
            if key in seen:
                continue
            seen.add(key)
            if key not in records:
                records[key] = passage
                initial_order[key] = len(initial_order)
                scores[key] = 0.0
            scores[key] += weight / (k + rank)
    for key, passage in records.items():
        passage.fusion_score = round(scores[key], 8)
    return sorted(records.values(), key=lambda p: (-scores[_passage_key(p)], initial_order[_passage_key(p)]))


def without_program_filter(flt: dict | None) -> dict | None:
    """Remove ONLY program constraints for genuine cross-program discovery.

    Keep jurisdiction, year and source-id restrictions, if any. Never broaden
    other access or source constraints.
    """
    if not flt:
        return None
    if "equals" in flt:
        return None if flt["equals"].get("key") == "program" else flt
    for op in ("andAll", "orAll"):
        if op in flt:
            remaining = [x for item in flt[op] if (x := without_program_filter(item)) is not None]
            if not remaining:
                return None
            if len(remaining) == 1:
                return remaining[0]
            return {op: remaining}
    return flt


def _new_query_model_client():
    """A bounded Bedrock model call so failed expansion doesn't stall a live call."""
    from botocore.config import Config
    from careonex_retrieve.config import session
    return session().client("bedrock-runtime", config=Config(
        connect_timeout=2, read_timeout=4, retries={"max_attempts": 0}
    ))


def _fetch_one(runtime, kb_id: str, query: str, n: int, flt: dict | None) -> list[Passage]:
    config: dict = {"numberOfResults": n}
    if flt:
        config["filter"] = flt
    response = runtime.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": config},
    )
    return [_passage(item) for item in response.get("retrievalResults", [])]


def retrieve(runtime, kb_id: str, query: str, top_k: int = 5,
             flt: dict | None = None, latest_only: bool = True,
             search_mode: str | None = None, program_hint: str | None = None,
             query_model=None, include_parent_context: bool = False,
             parent_s3_client=None, initial_passages: list[Passage] | None = None) -> RetrievalResult:
    """Retrieve passages. `feedback` is model-free pseudo-relevance expansion.

    Baseline remains the default for deployment safety. Set the server env
    CAREONEX_RETRIEVE_SEARCH_MODE=feedback, or use --search-mode feedback.
    The historical `expanded` and opt-in `intent` (LLM) modes are retained
    for benchmark reproducibility; the default stays baseline.
    """
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if initial_passages is not None and (search_mode or os.environ.get("CAREONEX_RETRIEVE_SEARCH_MODE", "baseline")).strip().lower() != "feedback":
        raise ValueError("initial_passages can only be used in feedback mode")
    if not query or not query.strip():
        raise ValueError("query must not be empty")
    mode = (search_mode or os.environ.get("CAREONEX_RETRIEVE_SEARCH_MODE", "baseline")).strip().lower()
    if mode not in ("baseline", "expanded", "intent", "feedback"):
        raise ValueError(f"Unknown search mode {mode!r}; use baseline, expanded, intent or feedback")
    n = max(top_k * CANDIDATE_MULTIPLIER, MIN_CANDIDATES) if latest_only else top_k
    queries = [query]
    planning_latency_ms = 0
    expansion_status = "not_requested"
    planning_reason = "not_requested"
    start = time.perf_counter()
    effective_filter = flt
    if mode == "expanded":
        queries = plan_queries(query, program_hint if program_hint is not None else _filter_program_hint(flt))
        expansion_status = "used" if len(queries) > 1 else "no_expansion"
    elif mode == "intent":
        planning_start = time.perf_counter()
        try:
            model = query_model if query_model is not None else _new_query_model_client()
            plan = plan_intent_queries(
                query, model_client=model,
                program=program_hint if program_hint is not None else _filter_program_hint(flt),
                model_id=os.environ.get("CAREONEX_QUERY_MODEL_ID", "amazon.nova-lite-v1:0"),
            )
            queries = plan.queries
            if plan.broaden_program_filter:
                effective_filter = without_program_filter(flt)
            expansion_status = "used" if len(queries) > 1 else "no_expansion"
        except Exception as exc:  # Model access, timeout, invalid output -> baseline retrieval.
            log.warning("intent query planning unavailable (%s); using original question", type(exc).__name__)
            queries = [query]
            expansion_status = "fallback"
        planning_latency_ms = int((time.perf_counter() - planning_start) * 1000)
    if mode == "feedback":
        # Pseudo-relevance feedback needs a FIRST retrieval to discover actual
        # terminology from this KB. Reuse it as RRF run 1 (don't search twice).
        original_run = (copy.deepcopy(initial_passages) if initial_passages is not None
                        else _fetch_one(runtime, kb_id, query, n, effective_filter))
        planning_start = time.perf_counter()
        try:
            plan = plan_feedback_queries(query, original_run, known_program_aliases=tuple(PROGRAM_LABELS))
            queries = plan.queries
            planning_reason = plan.reason
            expansion_status = "used" if len(queries) > 1 else "no_expansion"
        except Exception as exc:
            log.warning("feedback planning failed (%s); using original results", type(exc).__name__)
            queries = [query]
            expansion_status = "fallback"
            planning_reason = "planning_error"
        planning_latency_ms = int((time.perf_counter() - planning_start) * 1000)
        if len(queries) == 1:
            passages = original_run
        else:
            # Supplemental calls are parallel and isolated from the successful
            # original result; AWS errors can never discard that baseline.
            runs = [original_run]
            partial_fallback = False
            with ThreadPoolExecutor(max_workers=min(2, len(queries)-1)) as executor:
                futures = [executor.submit(_fetch_one, runtime, kb_id, q, n, effective_filter)
                           for q in queries[1:]]
                for future in futures:
                    try:
                        runs.append(future.result())
                    except Exception as exc:
                        partial_fallback = True
                        log.warning("supplementary feedback retrieval failed (%s)", type(exc).__name__)
            if partial_fallback:
                expansion_status = "partial_fallback"
            # Keep baseline hits, but require a novel supplemental-only hit
            # to address the original question before RRF can promote it.
            # This is a candidate-admission gate, not an LLM answer validator.
            baseline_keys = {_passage_key(p) for p in original_run}
            admitted = [runs[0]]
            for extra_run in runs[1:]:
                admitted.append([p for p in extra_run if _passage_key(p) in baseline_keys
                                 or corroborates_original_question(query, p)])
            passages = reciprocal_rank_fusion(admitted, weights=[1.5] + [1.0] * (len(admitted)-1)) if len(admitted) > 1 else original_run
    elif len(queries) == 1:
        passages = _fetch_one(runtime, kb_id, queries[0], n, effective_filter)
    else:
        with ThreadPoolExecutor(max_workers=min(3, len(queries))) as executor:
            futures = [executor.submit(_fetch_one, runtime, kb_id, q, n, effective_filter) for q in queries]
            # If one supplementary query fails, preserve the successful
            # original search; do not silently turn an error into 'no evidence'.
            runs = []
            partial_fallback = False
            for i, future in enumerate(futures):
                try:
                    runs.append(future.result())
                except Exception as exc:
                    if i == 0:
                        raise
                    partial_fallback = True
                    log.warning("supplementary retrieval failed (%s)", type(exc).__name__)
            if partial_fallback:
                expansion_status = "partial_fallback"
            passages = reciprocal_rank_fusion(runs)
    latency_ms = int((time.perf_counter() - start) * 1000)

    passages = [p for p in passages if not (
        "nj_doas_programs_side_by_side_2026" in (p.s3_key or "") and not p.table_verified
    )]
    superseded: list[dict] = []
    if latest_only:
        passages, superseded = prefer_latest(passages)
    rerank_mode = os.environ.get("CAREONEX_RETRIEVE_RERANK", "none").strip().lower()
    if rerank_mode != "none":
        embed_client = None
        if rerank_mode == "titan":
            from careonex_retrieve.config import session
            embed_client = session().client("bedrock-runtime")
        passages = rerank_passages(query, passages, mode=rerank_mode, embed_client=embed_client,
                                  model_id=os.environ.get("CAREONEX_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0"))
    passages = passages[:top_k]
    parent_fetch_latency_ms = 0
    if include_parent_context:
        parent_start = time.perf_counter()
        # An explicit staging KB opt-in prevents an HTTP request from attempting
        # to fetch S3 context from the live Knowledge Base by accident.
        allowed_kb = os.environ.get("CAREONEX_PARENT_CONTEXT_KB_ID", "")
        if not allowed_kb or kb_id != allowed_kb:
            raise ValueError("parent context is restricted to the configured staging KB")
        from careonex_retrieve.config import session
        s3 = parent_s3_client if parent_s3_client is not None else session().client("s3")
        bucket = os.environ.get("CAREONEX_PARENT_S3_BUCKET")
        parent_cache: dict[tuple[str, str], str | None] = {}
        full_parents: list[str | None] = []
        # Stage only. Never attach every parent wholesale to the voice model.
        for passage in passages:
            if not passage.parent_id or not passage.s3_key:
                full_parents.append(None)
                continue
            try:
                if not bucket:
                    raise ValueError('CAREONEX_PARENT_S3_BUCKET must be set')
                cache_key = (passage.s3_key, passage.parent_id)
                if cache_key not in parent_cache:
                    parent_cache[cache_key] = fetch_parent(
                        s3, f's3://{bucket}/{passage.s3_key}', passage.parent_id,
                        allowed_bucket=bucket)
                full_parents.append(parent_cache[cache_key])
            except Exception as exc:
                log.warning('staging parent fetch failed (%s); child retained', type(exc).__name__)
                full_parents.append(None)
        selected = select_best_parent_contexts(
            query, [p.text for p in passages], full_parents, max_excerpts=1)
        for passage, excerpt in zip(passages, selected):
            passage.parent_text = excerpt
        parent_fetch_latency_ms = int((time.perf_counter() - parent_start) * 1000)
        latency_ms = int((time.perf_counter() - start) * 1000)
    result = RetrievalResult(query=query, knowledge_base_id=kb_id, filter=effective_filter, top_k=top_k,
                             latency_ms=latency_ms, passages=passages, superseded=superseded,
                             search_mode=mode, queries_used=queries, planning_latency_ms=planning_latency_ms,
                             expansion_status=expansion_status, planning_reason=planning_reason,
                             parent_fetch_latency_ms=parent_fetch_latency_ms)
    log.info("retrieve %dms (planning %dms) mode=%s status=%s searches=%d k=%d filter=%s hits=%d superseded=%d q=%r",
             latency_ms, planning_latency_ms, mode, expansion_status, len(queries), top_k,
             bool(effective_filter), len(passages), len(superseded), query[:80])
    return result


def _filter_program_hint(flt: dict | None) -> str | None:
    """Infer only a known broad context from an existing metadata filter.

    We never use a narrow program value to override a caller's actual query.
    """
    if not flt:
        return None
    def values(node):
        if isinstance(node, dict):
            if "equals" in node and node["equals"].get("key") == "program":
                yield node["equals"].get("value", "")
            for child in [*node.get("orAll", ()), *node.get("andAll", ())]:
                yield from values(child)
    v = list(values(flt))
    if "NJ FamilyCare / Medicaid MLTSS" in v:
        return "Medicaid"
    return None
