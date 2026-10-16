"""Tools Nova Sonic may call during a conversation, and their handlers.

Only one for now: lookup_program_info, which asks the retrieve service for cited passages.
The handler never raises; a failure becomes a JSON result that tells the model to promise a
follow-up instead of guessing."""

from __future__ import annotations

import asyncio
import json
import logging
import re
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
            "cost share, how and where to apply. Returns passages from official documents with their source. "
            "Call it before stating any of these facts, even a short yes or no."
        ),
        "inputSchema": {
            "json": json.dumps(
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": (
                            "The caller's question, rephrased as a search query about one program. To compare programs, "
                            "call once per program with a query about that program alone (e.g. 'PCA benefit who qualifies', "
                            "then 'MLTSS who qualifies'), never the whole comparison as one query."
                        )},
                        "program": {"type": "string", "description": "Program name if known, e.g. MLTSS, JACC, PACE, PCA, Medicare, VA."},
                        "county": {"type": "string", "description": "One of New Jersey's 21 counties (e.g. Monmouth, Bergen, Hudson), only if the caller named it. Never the state name 'New Jersey' and never a town; leave it out if unknown."},
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
    "kind_of_help": "What help is needed: personal care (bathing, dressing), companionship, housekeeping, medication reminders, dementia care, a caregiver living in the home...",
    "hours_per_week": "Rough hours of care per week, or 'caregiver lives in the home'.",
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

# Intake as a state machine owned by the client: the model reports what it already knows and gets back the
# ONE next question to ask. Order matters for a worried caller: who and what first, phone number last.
INTAKE_ORDER: list[tuple[str, str]] = [
    ("relationship", "Who is the care for, and how are you related to them?"),
    ("care_recipient_age", "How old are they?"),
    ("county", "Which New Jersey county do they live in?"),
    ("kind_of_help", "What kind of help do they need most, for example bathing and dressing, meals, company, or memory care?"),
    ("hours_per_week", "Roughly how many hours a week, or would you want a caregiver who lives in the home with them?"),
    ("timeline", "When would you like care to start?"),
    ("payer", "How do you expect to pay for it: Medicaid, Medicare, VA, insurance, out of pocket, or not sure yet?"),
    ("caller_name", "What is your name?"),
    ("callback_phone", "What is the best phone number for the coordinator to call you back?"),
]

INTAKE_NEXT_QUESTION = {
    "toolSpec": {
        "name": "intake_next_question",
        "description": (
            "Use while helping someone arrange care. Pass every detail you already know; it returns the single next "
            "question to ask, or tells you the intake is complete. Ask exactly that one question and nothing else."
        ),
        "inputSchema": {
            "json": json.dumps(
                {
                    "type": "object",
                    "properties": {k: {"type": "string", "description": v} for k, v in INTAKE_FIELDS.items()},
                    "required": [],
                }
            )
        },
    }
}


def intake_next_question_sync(args: dict[str, Any]) -> dict:
    known = {k: str(v).strip() for k, v in args.items() if k in INTAKE_FIELDS and str(v).strip()}
    for field_name, question in INTAKE_ORDER:
        if field_name not in known:
            remaining = sum(1 for f, _ in INTAKE_ORDER if f not in known)
            return {
                "field": field_name,
                "ask": question,
                "remaining": remaining,
                "guidance": "Ask only this one question, in your own warm words, then stop and wait. Do not add a second question.",
            }
    return {
        "complete": True,
        "known": known,
        "guidance": "All details collected. Read the key details back in one or two sentences, ask if they are right, then call save_intake.",
    }


TOOLS: list[dict] = [LOOKUP_PROGRAM_INFO, INTAKE_NEXT_QUESTION, SAVE_INTAKE]


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


# Retrieval failed, so nothing is verified: the model must say so instead of answering from memory. No staff
# follow-up is scheduled by a failed lookup, so the model offers the callback (save_intake) rather than promising one.
_UNVERIFIED_GUIDANCE = (
    "The program information could not be looked up. Tell the caller you could not verify that information "
    "right now. Do not state figures, coverage or eligibility from memory. You may offer to take their details "
    "so a CareOneX coordinator can call them back; do not say anyone will follow up unless save_intake succeeds."
)

# The 21 New Jersey counties. The model sometimes passes the state ("New Jersey") or a town as the county;
# only a real county may shape the retrieval query.
NJ_COUNTIES = (
    "Atlantic", "Bergen", "Burlington", "Camden", "Cape May", "Cumberland", "Essex", "Gloucester", "Hudson",
    "Hunterdon", "Mercer", "Middlesex", "Monmouth", "Morris", "Ocean", "Passaic", "Salem", "Somerset", "Sussex",
    "Union", "Warren",
)
_COUNTY_BY_KEY = {c.lower(): c for c in NJ_COUNTIES}


def normalize_county(value: Any) -> str | None:
    """'monmouth', 'Monmouth County', 'Monmouth County, NJ' -> 'Monmouth'; anything else (including 'New Jersey') -> None."""
    if not isinstance(value, str):
        return None
    key = re.sub(r"[.,]", " ", value).lower()
    key = re.sub(r"\b(new jersey|nj|county|co)\b", " ", key)
    key = " ".join(key.split())
    return _COUNTY_BY_KEY.get(key)


# Passage text is Markdown from PDF/HTML extraction. Sonic echoes what it reads, so formatting is removed before
# the text reaches it; the words, numbers, conditions and footnote marks are kept. Tables become one line per
# row with each cell labelled by its column header, so a value stays attached to its program.
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
PASSAGE_MAX_CHARS = 2800  # the chunker's hard cap: a normal chunk is never cut


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _plain_inline(s: str) -> str:
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)  # [text](url) -> text
    # Only paired emphasis is formatting; a lone * or ** is a footnote mark and must stay with its cell.
    s = re.sub(r"\*\*(\S(?:.*?\S)?)\*\*", r"\1", s)
    s = re.sub(r"__(\S(?:.*?\S)?)__", r"\1", s)
    s = s.replace("`", "")
    return " ".join(s.split())


def _sentence(parts: list[str]) -> str:
    line = "; ".join(parts)
    return line if line[-1:] in ".!?:" else line + "."


def _table_lines(rows: list[str]) -> list[str]:
    has_header = len(rows) > 1 and bool(_TABLE_SEP.match(rows[1].strip()))
    header = [_plain_inline(c) for c in _cells(rows[0])] if has_header else []
    # Label cells with their column header only when every column has its own header. Extracted tables often
    # have a merged group-title row instead; labelling from it would attach the wrong heading to a value.
    label_cells = bool(header) and all(header)
    out = []
    if header and not label_cells and any(header):
        out.append(_sentence([h for h in header if h]))
    for row in rows[2:] if has_header else rows:
        if _TABLE_SEP.match(row.strip()):
            continue
        cells = [_plain_inline(c) for c in _cells(row)]
        parts = []
        for i, cell in enumerate(cells):
            if not cell:
                continue
            label = header[i] if label_cells and 0 < i < len(header) else ""
            parts.append(f"{label}: {cell}" if label else cell)
        if parts:
            out.append(_sentence(parts))
    return out


def speakable_passage(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            out.extend(_table_lines(lines[i:j]))
            i = j
            continue
        line = re.sub(r"^\s*#{1,6}\s+", "", lines[i])  # headings
        line = re.sub(r"^\s*[-+]\s+", "", line)  # bullet markers; "* " / "** " lines are footnotes and stay
        line = re.sub(r"^\s*(\d+)\.\s+", r"\1) ", line)  # "1. " list numbers read as list syntax
        line = _plain_inline(line)
        if line:
            out.append(line)
        i += 1
    plain = "\n".join(out)
    if len(plain) <= PASSAGE_MAX_CHARS:
        return plain
    # Over the cap (rare): cut at the last sentence end and say so, never mid-sentence.
    cut = plain[:PASSAGE_MAX_CHARS]
    end = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind("; "))
    return (cut[: end + 1] if end > PASSAGE_MAX_CHARS // 2 else cut).rstrip() + " [passage continues; not all of it is shown]"


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - internal service URL
        return json.loads(resp.read().decode("utf-8"))


def lookup_program_info_sync(args: dict[str, Any]) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "empty query", "guidance": "Ask the caller to repeat the question."}
    if not RETRIEVE_URL:
        return {"error": "knowledge base unavailable", "guidance": _UNVERIFIED_GUIDANCE, "passages": []}
    facts = []
    if args.get("age"):
        facts.append(f"person aged {args['age']}")
    if args.get("situation"):
        facts.append(str(args["situation"]))
    county = normalize_county(args.get("county"))
    ignored_county = args.get("county") if args.get("county") and not county else None
    if county:
        facts.append(f"{county} County")
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
        return {"error": f"retrieve failed: {exc}", "guidance": _UNVERIFIED_GUIDANCE, "passages": []}
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
            "text": speakable_passage(p.get("text", "")),
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
            "Spoken reply: answer only the question asked, in two or three short sentences (about 40 to 60 words), "
            "then at most one question. Mention only the one or two programs that answer it, unless the caller asked "
            "for a comparison. Plain sentences only: no lists, numbering, headings or asterisks. "
            "Answer only from these passages. Before naming a program, check every fact the caller gave (age, Medicaid "
            "status, veteran, dementia, caregiver at home) against that program's requirements in the passages; do not "
            "suggest a program the person does not meet, and say in one short sentence why it is out (e.g. 'JACC starts "
            "at 60'). Prefer the passage with the latest effective_date when figures differ and say the year. Refer to "
            "the document in plain words (e.g. 'the state's 2026 program table'); do not say 'source', do not read URLs "
            "or symbols. Keep each condition with the program it belongs to: when you mention two programs, give each its "
            "own sentence that names it, and never say a condition the passages give for one program (an assessment, "
            "a provider type, an age or income rule) about both. If the passages do not answer the "
            "question, say you could not verify that; offer the callback rather than promising a follow-up."
        )
        + (f" Caller facts to check against: {'; '.join(facts)}." if facts else "")
        + (f" '{ignored_county}' is not a New Jersey county, so no county was used; ask which county if it matters." if ignored_county else ""),
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
    elif name == "intake_next_question":
        result = intake_next_question_sync(args)
    elif name == "save_intake":
        result = await asyncio.get_running_loop().run_in_executor(None, save_intake_sync, args)
    else:
        result = {"error": f"unknown tool {name}"}
    return json.dumps(result, ensure_ascii=False)
