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

from nova_sonic.config import INTAKE_DIR, RETRIEVE_TIMEOUT_S, RETRIEVE_URL

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
                        "age": {"type": "string", "description": "Age of the person who needs care, if the caller said it."},
                        "situation": {"type": "string", "description": "Other facts the caller gave that affect eligibility: has Medicaid, veteran, dementia, lives with family, income if stated."},
                    },
                    "required": ["query"],
                }
            )
        },
    }
}

INTAKE_FIELDS = {
    "caller_name": "Caller's name.",
    "relationship": "Caller's relationship to the person who needs care (self, daughter, son, spouse, friend...).",
    "care_recipient_age": "Age of the person who needs care, if given.",
    "county": "New Jersey county where care is needed.",
    "kind_of_help": "What help is needed: personal care (bathing, dressing), companionship, housekeeping, medication reminders, dementia care, live-in...",
    "hours_per_week": "Rough hours of care per week, or 'live-in'.",
    "timeline": "When care should start: now, within a month, planning ahead.",
    "payer": "How they expect to pay: Medicaid/MLTSS, Medicare, VA, long-term care insurance, private pay, not sure.",
    "callback_phone": "Best phone number to call back.",
    "language": "Preferred language for the callback (English, Spanish...).",
    "notes": "Anything else important the caller said, in one or two sentences.",
}

SAVE_INTAKE = {
    "toolSpec": {
        "name": "save_intake",
        "description": (
            "Save the family's details so a CareOneX coordinator can call back and arrange home care. Call it once, "
            "after reading the key details back to the caller. Missing fields may be left out."
        ),
        "inputSchema": {
            "json": json.dumps(
                {
                    "type": "object",
                    "properties": {k: {"type": "string", "description": v} for k, v in INTAKE_FIELDS.items()},
                    "required": ["callback_phone"],
                }
            )
        },
    }
}

TOOLS: list[dict] = [LOOKUP_PROGRAM_INFO, SAVE_INTAKE]


def save_intake_sync(args: dict[str, Any]) -> dict:
    import datetime as dt
    import pathlib
    import uuid

    record = {k: str(args.get(k, "")).strip() for k in INTAKE_FIELDS if args.get(k)}
    if not record.get("callback_phone"):
        return {"saved": False, "error": "callback_phone missing", "guidance": "Ask for the best phone number to call back, then save again."}
    record.update({"intake_id": uuid.uuid4().hex[:12], "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "channel": "voice", "status": "new"})
    out_dir = pathlib.Path(INTAKE_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{record['created_at'].replace(':', '')}-{record['intake_id']}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    log.info("intake saved: %s", path)
    print(f"Intake saved: {path}")
    return {"saved": True, "intake_id": record["intake_id"], "guidance": "Tell the caller a CareOneX coordinator will call them back within one business day, and thank them."}


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
    facts = []
    if args.get("age"):
        facts.append(f"person aged {args['age']}")
    if args.get("situation"):
        facts.append(str(args["situation"]))
    if args.get("county"):
        facts.append(f"{args['county']} County")
    payload = {"query": f"{query} ({'; '.join(facts)})" if facts else query, "top_k": 5}
    if args.get("program"):
        payload["program"] = str(args["program"])
    queries = [payload]
    if args.get("age"):
        # "How do I pay" phrasings pull payer documents and miss the eligibility-by-age summary;
        # a second, age-specific lookup guarantees the model sees which programs the person can use.
        queries.append({"query": f"which New Jersey home care programs can a {args['age']} year old use", "top_k": 3})
    raw: list[dict] = []
    latency_total = 0
    try:
        for q in queries:
            result = _post_json(f"{RETRIEVE_URL}/retrieve", q, RETRIEVE_TIMEOUT_S)
            raw.extend(result.get("passages", []))
            latency_total += int(result.get("latency_ms") or 0)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        log.warning("retrieve failed: %s", exc)
        return {"error": f"retrieve failed: {exc}", "guidance": "Tell the caller a CareOneX team member will follow up with the exact figures.", "passages": []}
    seen: set[str] = set()
    deduped = []
    for p in raw:
        key = p.get("s3_key") or p.get("text", "")[:80]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    result = {"passages": deduped[:6], "latency_ms": latency_total}
    passages = [
        {
            "text": p.get("text", "")[:1200],
            "document": _spoken_title(p.get("title")),
            "program": p.get("program"),
            "effective_date": p.get("effective_date"),
            "year": (p.get("effective_date") or "")[:4] or None,
        }
        for p in result.get("passages", [])
    ]
    # Newest first so the model's default is the current figure.
    passages.sort(key=lambda p: p.get("effective_date") or "", reverse=True)
    return {
        "passages": passages,
        "latency_ms": result.get("latency_ms"),
        "guidance": (
            "Answer only from these passages. Before naming a program, check every fact the caller gave (age, Medicaid "
            "status, veteran, dementia, caregiver at home) against that program's requirements in the passages; do not "
            "suggest a program the person does not meet, and say in one short sentence why it is out (e.g. 'JACC starts "
            "at 60'). Prefer the passage with the latest effective_date when figures differ and say the year. Refer to "
            "the document in plain words (e.g. 'the state's 2026 program table'); do not say 'source', do not read URLs "
            "or symbols. If the passages do not answer the question, say a person will follow up."
        )
        + (f" Caller facts to check against: {'; '.join(facts)}." if facts else ""),
    }


def _spoken_title(title: str | None) -> str | None:
    """Make a document title speakable: drop site-name prefixes separated by '|' and file-ish noise."""
    if not title:
        return None
    parts = [p.strip() for p in title.split("|") if p.strip()]
    best = parts[-1] if parts else title
    return best.replace("(Web-English)", "").replace(".pdf", "").strip()


async def handle_tool(name: str, args_json: str) -> str:
    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        args = {"query": args_json}
    if name == "lookup_program_info":
        result = await asyncio.get_running_loop().run_in_executor(None, lookup_program_info_sync, args)
    elif name == "save_intake":
        result = await asyncio.get_running_loop().run_in_executor(None, save_intake_sync, args)
    else:
        result = {"error": f"unknown tool {name}"}
    return json.dumps(result, ensure_ascii=False)
