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
    for alias, labels in PROGRAM_LABELS.items():  # aliases first: "JACC" also pulls in the DoAS program guide
        if alias in key:
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
