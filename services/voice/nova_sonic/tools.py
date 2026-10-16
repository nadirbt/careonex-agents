"""Tools Nova Sonic may call during a conversation, and their handlers.

There are four tools: knowledge retrieval, guided intake, local intake saving, and language switching.
Retrieval failures are reported explicitly; they must not be treated as evidence."""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
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
            "Search verified CareOneX knowledge-base documents for ANY specific factual home-care question, "
            "not just government programs. Topics may include personal assistance, dementia care, companion "
            "care, caregiver support, scheduling, home-care payment, costs, service areas, language preferences, "
            "insurance, JACC, Medicaid, Medicare and related options. Returns source-linked passages; "
            "absence of evidence is NOT evidence that a service is offered or unavailable. "
            "Only use for a complete factual question within home-care scope, never for greetings, "
            "language selection, small talk, intake, or medical emergencies. Search once per distinct "
            "question; additional safe searches are handled internally."
        ),
        "inputSchema": {
            "json": json.dumps(
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": (
                            "The caller's complete factual home-care question, retaining preferences, qualifiers, "
                            "negations, timing and location. Do not invent program names, reword away the "
                            "caller's constraints, or search unfinished speech."
                        )},
                        "program": {"type": "string", "description": "Optional named payment program if explicitly provided or confidently known from conversation. Leave blank for general home-care questions; NEVER guess a program."},
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
    "relationship": "Caller's explicitly stated relationship to the person needing care (self, daughter, son, spouse, friend...). Never guess gender or relationship from 'my mom'/'my dad'.",
    "care_recipient_age": "Age of the person who needs care, if given.",
    "county": "New Jersey county where care is needed. If caller genuinely does not know after attempts, use 'not sure' for intake_next_question only (omit county from save_intake).",
    "kind_of_help": "What help is needed: personal care (bathing, dressing), companionship, housekeeping, medication reminders, dementia care, a caregiver living in the home...",
    "hours_per_week": "Rough hours of care per week (1-168), or 'caregiver lives in the home'. For 200 hours/week, clarify rather than record.",
    "timeline": "When care should start: now, within a month, planning ahead.",
    "payer": "How they expect to pay: Medicaid/MLTSS, Medicare, VA, long-term care insurance, private pay, not sure.",
    "callback_phone": "Best phone number to call back. Complete 10-digit number, confirmed digit by digit by caller before saving.",
    "language": "Preferred language for the STAFF CALLBACK, if given. This may differ from the language used for this AI conversation.",
    "notes": "Anything else important the caller said, in one or two sentences.",
}

SAVE_INTAKE = {
    "toolSpec": {
        "name": "save_intake",
        "description": (
            "Record the family's intake locally for future staff review. This tool does not dispatch a "
            "coordinator or schedule a callback; never promise that staff were notified. Call it once, "
            "only AFTER reading back the entire callback number digit by digit AND hearing the caller explicitly say it is correct. "
            "Missing fields may be left out. The client rejects saving without verified confirmation."
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

# A brief CALLBACK intake, not a clinical or benefits-assessment interview.
# Other fields remain accepted when volunteered, but are not demanded just to record a callback.
INTAKE_ORDER: list[tuple[str, str]] = [
    ("kind_of_help", "What kind of help does your family need most?"),
    ("county", "Which New Jersey county is care needed in?"),
    ("relationship", "What is your relationship to the person who needs care?"),
    ("timeline", "When would you like care to start?"),
    ("caller_name", "What name should the coordinator ask for?"),
    ("callback_phone", "What is the best phone number to call you back?"),
]

# Common aliases only, not an allowlist. The caller may request ANY language.
# Nova 2 Sonic may not reliably recognize or synthesize every requested language;
# accept the preference without falsely promising that speech support is guaranteed.
LANGUAGE_ALIASES = {
    "english": "English", "en": "English", "en-us": "English", "en-gb": "English",
    "spanish": "Spanish", "es": "Spanish", "español": "Spanish", "espanol": "Spanish", "es-us": "Spanish",
    "french": "French", "français": "French", "francais": "French", "fr": "French", "fr-fr": "French",
    "german": "German", "deutsch": "German", "de": "German", "de-de": "German",
    "italian": "Italian", "italiano": "Italian", "it": "Italian", "it-it": "Italian",
    "portuguese": "Portuguese", "português": "Portuguese", "portugues": "Portuguese", "pt": "Portuguese", "pt-br": "Portuguese",
    "hindi": "Hindi", "हिन्दी": "Hindi", "हिंदी": "Hindi", "hi": "Hindi", "hi-in": "Hindi",
    "mandarin": "Mandarin Chinese", "mandarin chinese": "Mandarin Chinese", "chinese": "Mandarin Chinese",
    "中文": "Mandarin Chinese", "汉语": "Mandarin Chinese", "漢語": "Mandarin Chinese", "普通话": "Mandarin Chinese", "普通話": "Mandarin Chinese",
    "cantonese": "Cantonese", "粤语": "Cantonese", "粵語": "Cantonese",
    "japanese": "Japanese", "日本語": "Japanese", "korean": "Korean", "한국어": "Korean",
    "hebrew": "Hebrew", "עברית": "Hebrew", "希伯来语": "Hebrew", "希伯來語": "Hebrew", "西伯来语": "Hebrew", "西伯來語": "Hebrew",
    "arabic": "Arabic", "العربية": "Arabic", "阿拉伯语": "Arabic", "阿拉伯語": "Arabic",
    "russian": "Russian", "русский": "Russian", "俄语": "Russian", "俄語": "Russian",
    "vietnamese": "Vietnamese", "tiếng việt": "Vietnamese", "越南语": "Vietnamese", "越南語": "Vietnamese",
}


# The model sometimes mistakes a Mandarin greeting (e.g. "你好") for a request to
# switch to Spanish. Do not let model-generated tool arguments override the caller.
# Require an explicit language-switch request in the latest recognized user utterance.
# This is a guardrail on the model's decision, not a substitute for speech recognition.
_LANGUAGE_MENTIONS: dict[str, tuple[str, ...]] = {
    "english": ("english", "ingles", "inglés", "inglês", "anglais", "englisch", "inglese", "英语", "英文", "英語"),
    "spanish": ("spanish", "espanol", "español", "espagnol", "spanisch", "spagnolo", "西班牙语", "西班牙語"),
    "french": ("french", "francais", "français", "französisch", "francese", "法语", "法語"),
    "german": ("german", "deutsch", "alemán", "aleman", "allemand", "德语", "德語"),
    "italian": ("italian", "italiano", "italien", "意大利语", "義大利語"),
    "portuguese": ("portuguese", "português", "portugues", "portugais", "葡萄牙语", "葡萄牙語"),
    "hindi": ("hindi", "हिंदी", "हिन्दी", "印地语", "印地語"),
    "chinese": ("chinese", "mandarin", "chino", "chinois", "中文", "汉语", "漢語", "普通话", "普通話"),
    "mandarin chinese": ("chinese", "mandarin", "chino", "chinois", "中文", "汉语", "漢語", "普通话", "普通話"),
    "cantonese": ("cantonese", "粤语", "粵語"),
    "japanese": ("japanese", "japonés", "japones", "japonais", "日本語", "日语", "日語"),
    "korean": ("korean", "coreano", "coréen", "韩语", "韓語", "한국어"),
    "hebrew": ("hebrew", "עברית", "希伯来语", "希伯來語", "西伯来语", "西伯來語"),
    "arabic": ("arabic", "العربية", "阿拉伯语", "阿拉伯語"),
    "russian": ("russian", "русский", "俄语", "俄語"),
    "vietnamese": ("vietnamese", "tiếng việt", "越南语", "越南語"),
}
_LANGUAGE_REQUEST_VERBS = re.compile(
    r"\b(?:speak|talk|use|switch|change|respond|reply|answer|continue|prefer|language|"
    r"please|hablar|habla|puedes|podrías|podrias|cambia|cambiar|responde|responder|"
    r"parler|parlez|parle|sprechen|sprich|sprache|parla|parlare|fala|falar|"
    r"बोल|बात)\b|[请請能会會用说說讲講回复回覆回答切换切換]",
    re.IGNORECASE,
)


def explicit_language_request(user_text: str, requested_language: str, context_text: str = "") -> bool:
    """True only when recognized user speech asks to use the specified language.

    Intent detection deliberately errs toward NOT switching if the transcript is
    uncertain. It is better to ask for clarification than choose the wrong voice.
    """
    text = user_text.casefold()
    target = requested_language.strip().casefold()
    canonical = LANGUAGE_ALIASES.get(target, requested_language.strip()).casefold()
    if canonical in ("mandarin", "chino", "chinois", "中文", "汉语", "普通话", "普通話"):
        canonical = "mandarin chinese"
    mentions = _LANGUAGE_MENTIONS.get(canonical, (requested_language.strip(),))
    present = any(
        (re.search(r"(?<!\w)" + re.escape(word.casefold()) + r"(?!\w)", text) if word.isascii()
         else word.casefold() in text)
        for word in mentions if word
    )
    if present and _LANGUAGE_REQUEST_VERBS.search(text):
        return True
    # A language name alone is a valid answer ONLY after the assistant explicitly
    # asked the caller which language to use. The context prevents accidental
    # switching when a language is merely mentioned during normal conversation.
    if present and re.search(r"(?:which|what|choose|prefer).{0,35}language|"
                             r"language.{0,35}(?:prefer|choose|want|switch)|"
                             r"(?:哪种|什么|哪个|哪個)语言|(?:哪種|什麼|哪個)語言|"
                             r"希望用.{0,20}(?:语言|語言)", context_text, re.I):
        rest = text
        for mention in mentions:
            rest = rest.replace(mention.casefold(), "")
        rest = re.sub(r"[\s\W_啊呀呢哦吧嘛请請是用​]+", "", rest)
        return not rest
    return False

SET_CONVERSATION_LANGUAGE = {
    "toolSpec": {
        "name": "set_conversation_language",
        "description": (
            "Use ONLY when the caller EXPLICITLY requests a different spoken language; a greeting "
            "or conversation in another language is not a language-switch request. Before replying to "
            "anything else. Caller may request ANY language, including Mandarin Chinese and languages "
            "not included among common aliases. Attempt the requested language without guaranteeing speech quality. "
            "Do not use RAG for a language request. Keep the chosen language for "
            "all future spoken replies until the caller asks to change it."
        ),
        "inputSchema": {
            "json": json.dumps({
                "type": "object",
                "properties": {
                    "language": {"type": "string", "description": "The language the caller explicitly requested, e.g. Spanish, French, Hindi, Mandarin."},
                },
                "required": ["language"],
            }, ensure_ascii=False)
        },
    }
}


def set_conversation_language_sync(args: dict[str, Any]) -> dict:
    """Store any clearly named language as a best-effort preference (no allowlist)."""
    requested = args.get("language", "")
    if not isinstance(requested, str) or not requested.strip():
        return {
            "changed": False, "error": "language not specified",
            "guidance": "Ask which language the caller wants; do not guess. Stay in the current language.",
        }
    requested = " ".join(requested.strip().split())
    # Reject tool-argument garbage, not languages. A reasonable language name in any
    # script can pass. Never interpolate control characters into an instruction.
    if len(requested) > 80 or any(ord(c) < 32 for c in requested):
        return {"changed": False, "error": "invalid language name",
                "guidance": "Ask the caller to state the language briefly and clearly."}
    language = LANGUAGE_ALIASES.get(requested.casefold(), requested)
    return {
        "changed": True, "language": language,
        "guidance": (
            f"LANGUAGE PREFERENCE: {language}. From the next spoken response, TRY speaking {language} "
            "and continue until the caller changes languages. This is best effort: recognition or speech "
            "synthesis may be unreliable for some languages. Never invent words or guarantee support. "
            "If communication fails, politely ask them to repeat or suggest another language. "
            "Keep the conversation facts; do not search the knowledge base for a language preference."
        ),
    }


INTAKE_NEXT_QUESTION = {
    "toolSpec": {
        "name": "intake_next_question",
        "description": (
            "Use ONLY when the caller explicitly asks for a callback, wants to arrange care, or agrees to have details recorded. "
            "A simple 'yes' after an intake offer means start intake now, not repeat the last answer. "
            "Pass all clearly understood facts. If you missed the answer to the previous field, set "
            "repeat_field to that field name, ask the returned question again, and wait. "
            "Ask one question per turn. Brief callback needs care type, optional county, explicit relationship, start timeline, name, phone; "
            "other fields are voluntary. Never guess a relationship, name, county or phone number. If caller asks about the pending field, "
            "set help_topic instead of interpreting the question as a missing answer."
        ),
        "inputSchema": {
            "json": json.dumps(
                {
                    "type": "object",
                    "properties": {
                        **{k: {"type": "string", "description": v} for k, v in INTAKE_FIELDS.items()},
                        "repeat_field": {"type": "string", "enum": [f for f, _ in INTAKE_ORDER], "description": "Set ONLY if the previous question was missed, unclear, or cut off. Repeat this field question instead of advancing."},
                        "retry_count": {"type": "integer", "minimum": 1, "description": "Number of unclear replies to this same field. After 2 tries vary the question or offer a workaround."},
                        "help_topic": {"type": "string", "description": "Set to 'county' or 'hours_per_week' if caller asks what the field means or needs examples. This answers their question WITHOUT advancing the intake."},
                    },
                    "required": [],
                }
            )
        },
    }
}


def parse_hours_per_week(value: Any) -> float | None:
    """Parse plainly numeric weekly-hour estimates; leave nonnumeric/24-7 descriptions alone."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)?(?:\s*/\s*week|\s*per\s*week)?\s*", value, flags=re.I)
    return float(match.group(1)) if match else None


def intake_next_question_sync(args: dict[str, Any]) -> dict:
    # Clarification is not a failed answer; respond to the caller and pause intake.
    if args.get("help_topic") == "county":
        return {
            "field": "county", "clarification": True,
            "ask": ("New Jersey has 21 counties, including Middlesex, Monmouth, Bergen and Hudson. "
                    "You can also give a town or ZIP code, or skip this for now."),
            "guidance": "Answer the county question first; do NOT repeat the county prompt or move to the next intake field yet.",
        }
    if args.get("help_topic") == "hours_per_week":
        return {
            "field": "hours_per_week", "clarification": True,
            "ask": ("An estimate is optional. A coordinator can help work out care hours later."),
            "guidance": "Do not demand an exact number of hours or imply hours of service are guaranteed.",
        }
    known = {k: str(v).strip() for k, v in args.items() if k in INTAKE_FIELDS and v is not None and str(v).strip()}
    county_candidate = None
    # A caller can explicitly decline to supply a county. Skip the optional
    # field for now, but do not store a non-county as a geographic fact.
    county_skipped = known.get("county", "").casefold() in (
        "not sure", "unknown", "unconfirmed", "skip", "ask staff", "don't know", "do not know"
    )
    if "county" in known:
        county = normalize_county(known["county"])
        if county:
            known["county"] = county
        else:
            county_candidate = None if county_skipped else possible_county(known["county"])
            known.pop("county")  # a guess is NOT a confirmed NJ county
    # Catch physically impossible numeric requests before advancing the intake.
    hours = parse_hours_per_week(known.get("hours_per_week", ""))
    if hours is not None and not 0 < hours <= 168:
        return {
            "field": "hours_per_week", "validation_required": True,
            "ask": ("A week has 168 hours, so I want to double-check that. "
                    "Did you mean hours per month, or do you need around-the-clock care?"),
            "guidance": "Do not record or infer a corrected number. Wait for the caller to clarify; then ask the next intake question.",
        }
    # Explicit recovery when a previous answer was not captured reliably.
    repeat_field = args.get("repeat_field")
    for field_name, question in INTAKE_ORDER:
        if field_name == repeat_field:
            if field_name == "county":
                retries = args.get("retry_count", 1)
                if not isinstance(retries, int):
                    retries = 1
                return {
                    "field": "county", "repeated": True,
                    "ask": (f"Did you mean {county_candidate} County?" if county_candidate else
                            "Could you spell the county name, or tell me the town instead?" if retries >= 2 else question),
                    "guidance": (
                        "Ask only this question. A suggested county must be confirmed by the caller "
                        "before recording it. If still unclear after two attempts, offer to leave county "
                        "unconfirmed for staff to verify; don't keep repeating the same request."
                    ),
                }
            return {
                "field": field_name,
                "ask": question,
                "repeated": True,
                "guidance": (
                    "Briefly say you didn't catch the answer, repeat ONLY this question in the caller's "
                    "current language, then wait. Do not pretend you heard a value or ask a new question. "
                    "For a phone number ask for digits slowly."
                ),
            }
    # Unknown/sentinel values are not answers and must not silently skip a question.
    for unknown_field in ("hours_per_week", "timeline", "payer", "relationship", "caller_name", "callback_phone"):
        if known.get(unknown_field, "").casefold() in ("not specified", "unknown", "unspecified", "not provided", "none", "n/a", ""):
            known.pop(unknown_field, None)
    for field_name, question in INTAKE_ORDER:
        if field_name == "county" and county_skipped:
            continue
        if field_name not in known:
            if field_name == "county" and county_candidate:
                return {
                    "field": "county", "ask": f"Did you mean {county_candidate} County?",
                    "county_candidate": county_candidate, "requires_confirmation": True,
                    "guidance": "This is only a phonetic guess; wait for the caller's yes before treating this as their county."
                }
            remaining = sum(1 for f, _ in INTAKE_ORDER if f not in known and not (f == "county" and county_skipped))
            return {
                "field": field_name,
                "ask": question,
                "remaining": remaining,
                "guidance": "Ask only this one question, in your own warm words, then stop and wait. Do not add a second question.",
            }
    return {
        "complete": True,
        "known": known,
        "unconfirmed_fields": (["county"] if county_skipped else []),
        "guidance": "Brief callback details collected. Read back the ten-digit phone number and ask specifically whether it is correct. WAIT for a clear yes before save_intake. Do not collect other optional details unless volunteered.",
    }


TOOLS: list[dict] = [LOOKUP_PROGRAM_INFO, INTAKE_NEXT_QUESTION, SAVE_INTAKE, SET_CONVERSATION_LANGUAGE]


def save_intake_sync(args: dict[str, Any]) -> dict:
    import datetime as dt
    import pathlib
    import uuid

    record = {k: str(args.get(k, "")).strip() for k in INTAKE_FIELDS if args.get(k)}
    if record.get("county"):
        county = normalize_county(record["county"])
        if county is None:
            return {"saved": False, "error": "invalid NJ county", "guidance": "Ask which New Jersey county needs care; do not save a state or town as the county."}
        record["county"] = county
    hours = parse_hours_per_week(record.get("hours_per_week", ""))
    if hours is not None and not 0 < hours <= 168:
        return {"saved": False, "error": "invalid hours_per_week",
                "guidance": "Hours per week must be positive and no more than 168. Clarify whether they meant per month or around-the-clock care; do not silently correct the estimate."}
    if not record.get("callback_phone"):
        return {"saved": False, "error": "callback_phone missing", "guidance": "Ask for the best phone number to call back, then save again."}
    # A partial/garbled phone number must not be treated as a completed intake.
    digits = re.sub(r"\D", "", record["callback_phone"])
    if not (len(digits) == 10 or (len(digits) == 11 and digits.startswith("1"))):
        return {
            "saved": False, "error": "invalid callback_phone",
            "guidance": "The number was not captured clearly. Ask the caller to repeat all 10 digits slowly; read them back and confirm before saving.",
        }
    record.update({"intake_id": uuid.uuid4().hex[:12], "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "channel": "voice", "status": "new"})
    out_dir = pathlib.Path(INTAKE_DIR).expanduser().resolve()
    path = out_dir / f"{record['created_at'].replace(':', '')}-{record['intake_id']}.json"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        # A failure must never be reported as saved=true. Exclusive creation avoids
        # overwriting a previously saved intake (and names already have random IDs).
        with path.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, ensure_ascii=False)
    except OSError as exc:
        log.error("intake write failed at %s: %s", path, exc)
        print(f"Intake NOT saved: could not write to {out_dir} ({exc})")
        return {"saved": False, "error": "local intake file write failed", "guidance": "The details could not be stored locally. Do not claim they were saved or promise a callback. Ask the operator to check CAREONEX_INTAKE_DIR and file permissions."}
    log.info("intake saved: %s", path)
    print(f"Intake saved: {path}")
    return {"saved": True, "intake_id": record["intake_id"], "guidance": "The details were saved locally for staff review. Thank the caller, but do not promise a callback time or claim a coordinator was notified; no staff dispatch is connected yet."}


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


# Common telephone-ASR renderings of Monmouth; these are suggestions only.
# Do not silently turn ordinary phrases such as "my mom's" into a county.
_COUNTY_SPOKEN_HINTS = {
    "mom mouse": "Monmouth", "mom mouth": "Monmouth", "mon mouse": "Monmouth",
    "mon mouth": "Monmouth", "mom mous": "Monmouth", "momos": "Monmouth",
    "monmoth": "Monmouth", "montmouth": "Monmouth",
}


def possible_county(value: Any) -> str | None:
    """Suggest (never automatically accept) a county from ambiguous ASR."""
    if not isinstance(value, str):
        return None
    key = " ".join(re.sub(r"[^a-z ]", " ", value.lower()).split())
    key = re.sub(r"\b(?:county|new jersey|nj)\b", "", key).strip()
    if key in _COUNTY_SPOKEN_HINTS:
        return _COUNTY_SPOKEN_HINTS[key]
    if len(key) < 5 or key.startswith("my "):
        return None
    matches = difflib.get_close_matches(key, _COUNTY_BY_KEY.keys(), n=2, cutoff=0.82)
    if len(matches) == 1:
        return _COUNTY_BY_KEY[matches[0]]
    return None


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
    # Voice factual home-care requests (not just program queries) use model-free feedback.
    # It searches the caller's exact question first, derives at most two extra
    # searches from relevant retrieved headings, and merges with RRF. The
    # retrieval layer retains the baseline if planning or supplementary search
    # fails; no Bedrock InvokeModel permission is required.
    # Roll back instantly with CAREONEX_VOICE_SEARCH_MODE=baseline.
    mode = os.environ.get("CAREONEX_VOICE_SEARCH_MODE", "feedback").strip().lower()
    if mode not in ("baseline", "feedback"):
        log.warning("unknown voice search mode %r; using baseline", mode)
        mode = "baseline"
    payload["search_mode"] = mode
    if args.get("program"):
        payload["program"] = str(args["program"])
    # Staging-only opt-in. Production voice requests remain identical by default.
    if os.environ.get("CAREONEX_VOICE_PARENT_CONTEXT", "").strip().lower() == "true":
        payload["include_parent_context"] = True
    # One retrieval request per tool invocation. A supplied age should refine this
    # question, not trigger an unrelated search across all NJ programs. Previously
    # every age-bearing lookup silently doubled the number of Bedrock queries.
    raw: list[dict] = []
    latency_total = 0
    try:
        result = _post_json(f"{RETRIEVE_URL}/retrieve", payload, RETRIEVE_TIMEOUT_S)
        raw.extend(result.get("passages", []))
        latency_total = int(result.get("latency_ms") or 0)
        # Operational metrics are logged, not sent to Sonic as medical evidence.
        # Avoid logging caller question or personal details.
        log.info("voice rag mode=%s expansion=%s searches=%d retrieval_ms=%d hits=%d",
                 result.get("search_mode", mode), result.get("expansion_status", "unknown"),
                 len(result.get("queries_used") or [query]), latency_total, len(raw))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        log.warning("retrieve failed: %s", exc)
        return {"error": f"retrieve failed: {exc}", "guidance": _UNVERIFIED_GUIDANCE, "passages": []}
    seen: set[str] = set()
    deduped = []
    for p in raw:
        if not isinstance(p, dict) or not isinstance(p.get("text"), str) or not p["text"].strip():
            continue  # never present blank or malformed retrieval results as evidence
        key = p.get("s3_key") or p.get("text", "")[:80]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    result = {"passages": deduped[:6], "latency_ms": latency_total}
    if not result["passages"]:
        return {"error": "no verified passages found", "passages": [], "latency_ms": latency_total, "guidance": _UNVERIFIED_GUIDANCE}
    passages = [
        {
            "text": speakable_passage(p.get("text", "")),
            **({"parent_context": speakable_passage(p["parent_text"][:2400])} if p.get("parent_text") else {}),
            "document": _spoken_title(p.get("title")),
            "program": p.get("program"),
            "effective_date": p.get("effective_date"),
            "source_url": p.get("source_url"),  # retain traceability in tool result; never speak the URL
            "year": (p.get("effective_date") or "")[:4] or None,
        }
        for p in result.get("passages", [])
    ]
    # Feedback ranking is the RRF result: keep its order so the improved
    # retrieval ranking reaches Sonic. The retrieve service already excludes
    # superseded figures for the same program via latest_only=True.
    # Preserve the historical newest-first behavior for explicit baseline
    # rollback, keeping that behavior unchanged for existing integrations.
    if mode == "baseline":
        passages.sort(key=lambda p: p.get("effective_date") or "", reverse=True)
    return {
        "passages": passages,
        "latency_ms": result.get("latency_ms"),
        "guidance": (
            "Answer ONLY the caller's question in one or two short sentences, ideally under 35 spoken words "
            "before at most one relevant question. Don't volunteer unrelated program details, unsolicited comparisons, "
            "or an intake offer unless relevant. No lists, headings or asterisks. "
            "Answer only from these passages. Before naming a program, check every fact the caller gave (age, Medicaid "
            "status, veteran, dementia, caregiver at home) against that program's requirements in the passages; do not "
            "suggest a program the person does not meet, and say in one short sentence why it is out (e.g. 'JACC starts "
            "at 60'). Prefer the passage with the latest effective_date when figures differ and say the year. Refer to "
            "the document in plain words (e.g. 'the state's 2026 program table'); do not say 'source', do not read URLs "
            "or symbols. Keep each condition with the program it belongs to: when you mention two programs, give each its "
            "own sentence that names it, and never say a condition the passages give for one program (an assessment, "
            "a provider type, an age or income rule) about both. If the passages do not answer the "
            "question, say you could not verify that; offer the callback rather than promising a follow-up. "
            "Never say the caller meets income, asset or clinical eligibility from age or vague agreement; "
            "do not treat a question about Medicaid coverage as choosing Medicaid as the payer."
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
    if not isinstance(args, dict):
        return json.dumps({"error": "tool arguments must be a JSON object"})
    if name == "lookup_program_info":
        result = await asyncio.get_running_loop().run_in_executor(None, lookup_program_info_sync, args)
    elif name == "intake_next_question":
        result = intake_next_question_sync(args)
    elif name == "save_intake":
        result = await asyncio.get_running_loop().run_in_executor(None, save_intake_sync, args)
    elif name == "set_conversation_language":
        result = set_conversation_language_sync(args)
    else:
        result = {"error": f"unknown tool {name}"}
    return json.dumps(result, ensure_ascii=False)
