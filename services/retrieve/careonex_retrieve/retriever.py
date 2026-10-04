"""Thin, typed wrapper over bedrock-agent-runtime Retrieve with metadata filtering and latency logging."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field

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


@dataclass
class RetrievalResult:
    query: str
    knowledge_base_id: str
    filter: dict | None
    top_k: int
    latency_ms: int
    passages: list[Passage] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {**asdict(self), "passages": [asdict(p) for p in self.passages]}


def build_filter(program: str | None = None, year: int | None = None, jurisdiction: str | None = None, source_id: str | None = None) -> dict | None:
    """Bedrock KB filter syntax: {"equals": {"key":..., "value":...}} combined with {"andAll": [...]}.
    `program` uses stringContains so "MLTSS" matches "NJ FamilyCare / Medicaid MLTSS"."""
    clauses: list[dict] = []
    if program:
        clauses.append({"stringContains": {"key": "program", "value": program}})
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
    )


def retrieve(runtime, kb_id: str, query: str, top_k: int = 5, flt: dict | None = None) -> RetrievalResult:
    vcfg: dict = {"numberOfResults": top_k}
    if flt:
        vcfg["filter"] = flt
    t0 = time.perf_counter()
    resp = runtime.retrieve(knowledgeBaseId=kb_id, retrievalQuery={"text": query}, retrievalConfiguration={"vectorSearchConfiguration": vcfg})
    latency_ms = int((time.perf_counter() - t0) * 1000)
    result = RetrievalResult(query=query, knowledge_base_id=kb_id, filter=flt, top_k=top_k, latency_ms=latency_ms,
                             passages=[_passage(i) for i in resp.get("retrievalResults", [])])
    log.info("retrieve %dms k=%d filter=%s hits=%d q=%r", latency_ms, top_k, bool(flt), len(result.passages), query[:80])
    return result
