"""HTTP API used by the voice agent's lookup_program_info tool.

POST /retrieve {"query": "...", "program": "MLTSS", "year": 2026, "top_k": 5}
GET  /health
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from careonex_retrieve import config
from careonex_retrieve.retriever import build_filter, retrieve

log = logging.getLogger(__name__)
app = FastAPI(title="CareOneX retrieve", version="0.1.0")
_runtime = None


def runtime():
    global _runtime
    if _runtime is None:
        _runtime = config.session().client("bedrock-agent-runtime")
    return _runtime


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    program: str | None = None
    year: int | None = None
    jurisdiction: str | None = None
    source_id: str | None = None
    top_k: int = Field(default=config.DEFAULT_TOP_K, ge=1, le=20)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/retrieve")
def post_retrieve(req: RetrieveRequest) -> dict:
    try:
        kb_id = config.knowledge_base_id()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"knowledge base not available: {exc}") from exc
    flt = build_filter(req.program, req.year, req.jurisdiction, req.source_id)
    return retrieve(runtime(), kb_id, req.query, req.top_k, flt).as_dict()
