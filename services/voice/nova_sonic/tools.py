"""Tools Nova Sonic may call during a conversation, and their handlers.

Only one for now: lookup_program_info, which asks the retrieve service for cited passages.
The handler never raises; a failure becomes a JSON result that tells the model to promise a
follow-up instead of guessing."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from nova_sonic.config import RETRIEVE_TIMEOUT_S, RETRIEVE_URL

log = logging.getLogger(__name__)

LOOKUP_PROGRAM_INFO = {
    "toolSpec": {
        "name": "lookup_program_info",
        "description": (
            "Look up facts about New Jersey home-care payment programs (NJ FamilyCare/Medicaid MLTSS, PCA, "
            "Personal Preference Program, JACC, Statewide Respite, Alzheimer's Adult Day Services, PACE, "
            "Medicare home health, VA benefits): eligibility, income and asset limits, covered services, "
            "cost share, how and where to apply. Returns passages from official documents with their source."
        ),
        "inputSchema": {
            "json": json.dumps(
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The caller's question, rephrased as a search query."},
                        "program": {"type": "string", "description": "Program name if known, e.g. MLTSS, JACC, PACE, PCA, Medicare, VA."},
                        "county": {"type": "string", "description": "New Jersey county if the caller mentioned one."},
                    },
                    "required": ["query"],
                }
            )
        },
    }
}

TOOLS: list[dict] = [LOOKUP_PROGRAM_INFO]


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - internal service URL
        return json.loads(resp.read().decode("utf-8"))


def lookup_program_info_sync(args: dict[str, Any]) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "empty query", "guidance": "Ask the caller to repeat the question."}
    if not RETRIEVE_URL:
        return {"error": "knowledge base unavailable", "guidance": "Tell the caller a CareOneX team member will follow up with the exact figures.", "passages": []}
    payload = {"query": query, "top_k": 4}
    if args.get("program"):
        payload["program"] = str(args["program"])
    try:
        result = _post_json(f"{RETRIEVE_URL}/retrieve", payload, RETRIEVE_TIMEOUT_S)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        log.warning("retrieve failed: %s", exc)
        return {"error": f"retrieve failed: {exc}", "guidance": "Tell the caller a CareOneX team member will follow up with the exact figures.", "passages": []}
    passages = [
        {
            "text": p.get("text", "")[:1200],
            "source": p.get("title") or p.get("source_url"),
            "source_url": p.get("source_url"),
            "program": p.get("program"),
            "effective_date": p.get("effective_date"),
        }
        for p in result.get("passages", [])
    ]
    return {"passages": passages, "latency_ms": result.get("latency_ms"), "guidance": "Answer only from these passages and name the source. If they do not answer the question, say a person will follow up."}


async def handle_tool(name: str, args_json: str) -> str:
    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        args = {"query": args_json}
    if name == "lookup_program_info":
        result = await asyncio.get_running_loop().run_in_executor(None, lookup_program_info_sync, args)
    else:
        result = {"error": f"unknown tool {name}"}
    return json.dumps(result, ensure_ascii=False)
