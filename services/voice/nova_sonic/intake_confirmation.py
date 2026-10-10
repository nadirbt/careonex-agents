"""Fail-closed callback-number confirmation gate for live Nova Sonic sessions.

The language model is not allowed to save a guessed phone number merely because
it has ten digits. The client requires a readback of those exact digits followed
by an explicit caller affirmation in a later turn. Each new candidate resets
confirmation; repeated identical saves are idempotent within the session.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Treat only affirmative *answers*, not language-model interpretations, as approval.
_AFFIRMATIVE = re.compile(r"^(?:yes|yeah|yep|yup|correct|right|that's right|that is right|that's correct|that is correct|yes that's correct|yes that is correct|yeah that's right|yeah that is correct|yes it is|sure that's right)[.!\s]*$", re.I)
_NEGATIVE = re.compile(r"\b(?:no|not|wrong|incorrect|mistake|change|different)\b", re.I)


def _digits(number: Any) -> str:
    raw = re.sub(r"\D", "", str(number or ""))
    return raw[1:] if len(raw) == 11 and raw.startswith("1") else raw


# Nova Sonic transcribes spoken readbacks as either digits ("1 2 3") or
# words ("one, two, three"). Normalize *only* the assistant's immediately
# preceding spoken readback, never the caller's confirmation or tool payload.
_SPOKEN_DIGITS = {
    "zero": "0", "oh": "0", "o": "0",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def _has_full_phone_readback(phone: str, spoken: str) -> bool:
    """Check for a contiguous full ten-digit number, written or spoken.

    Non-digit words break the sequence: merely mentioning the last four
    digits, or a confirmation question with no full readback, cannot pass.
    """
    current = ""
    for token in re.findall(r"\d+|[A-Za-z]+", spoken.lower()):
        digits = token if token.isdecimal() else _SPOKEN_DIGITS.get(token)
        if digits is None:
            current = ""
            continue
        current += digits
        if phone in current:
            return True
        # Avoid accumulating unrelated long numeric runs.
        if len(current) > 20:
            current = current[-10:]
    return False


def _clean_record(raw: dict) -> dict:
    # Non-intake/private fields are not submitted to the save tool.
    from nova_sonic.tools import INTAKE_FIELDS
    cleaned = {key: str(raw[key]).strip() for key in INTAKE_FIELDS if raw.get(key) is not None and str(raw[key]).strip()}
    if "callback_phone" in cleaned:
        cleaned["callback_phone"] = _digits(cleaned["callback_phone"])
    return cleaned


class CallbackConfirmationGate:
    def __init__(self) -> None:
        self.pending: tuple[str, str, int] | None = None
        self.saved: dict[str, dict] = {}

    def check(self, raw: Any, transcripts: list[tuple[str, str]]) -> tuple[dict | None, dict | None]:
        """Return (validated_args_to_save, response_to_agent).

        Exactly one is non-None. Only a completed number with a matching spoken
        readback and subsequent positive user reply can reach the save handler.
        """
        if not isinstance(raw, dict):
            return None, {"saved": False, "error": "invalid intake payload", "guidance": "Ask again for the callback phone number."}
        record = _clean_record(raw)
        phone = _digits(record.get("callback_phone"))
        if len(phone) != 10:
            self.pending = None
            return None, {"saved": False, "error": "invalid callback_phone", "guidance": "I need all ten digits. Ask for the complete phone number slowly; do not save anything yet."}
        record["callback_phone"] = phone
        signature = json.dumps(record, sort_keys=True, ensure_ascii=False)
        if signature in self.saved:
            return None, {**self.saved[signature], "already_saved": True, "guidance": "This exact intake was already saved. Do not save it twice."}
        if self.pending is None or self.pending[0] != signature:
            # If the agent already read back this exact number and the caller
            # clearly confirmed it, do not ask for a second confirmation.
            # Otherwise stage the candidate, requiring a NEW readback.
            if self._confirmed_since(phone, transcripts, 0):
                return record, None
            self.pending = (signature, phone, len(transcripts))
            return None, self._request_confirmation(phone)

        _, _, start = self.pending
        if not self._confirmed_since(phone, transcripts, start):
            return None, self._request_confirmation(phone)
        return record, None

    @staticmethod
    def _confirmed_since(phone: str, transcripts: list[tuple[str, str]], start: int) -> bool:
        """Require an immediate, explicit yes to the *most recent* full phone readback.

        A generic "yes" to a later, unrelated question must never confirm a
        previously spoken phone number. Consecutive transcript chunks from the
        same role are treated as one turn.
        """
        tail = transcripts[start:]
        if not tail:
            return False
        turns: list[tuple[str, str]] = []
        for role, message in tail:
            if role not in ("USER", "ASSISTANT"):
                continue
            if turns and turns[-1][0] == role:
                turns[-1] = (role, turns[-1][1] + " " + message)
            else:
                turns.append((role, message))
        if len(turns) < 2 or turns[-2][0] != "ASSISTANT" or turns[-1][0] != "USER":
            return False
        readback = turns[-2][1]
        if not _has_full_phone_readback(phone, readback):
            return False
        # A readback is not enough: it must explicitly ask the caller to verify.
        if not re.search(r"\b(?:correct|right|confirm|accurate)\b|\bis (?:that|this|your|the) (?:phone|number)\b", readback, re.I):
            return False
        answer = " ".join(re.sub(r"[,!?]", " ", turns[-1][1]).strip().split())
        return not _NEGATIVE.search(answer) and bool(_AFFIRMATIVE.fullmatch(answer))

    @staticmethod
    def _request_confirmation(phone: str) -> dict:
        return {
            "saved": False,
            "confirmation_required": True,
            "callback_phone": phone,
            "guidance": (
                f"Before saving, read the ENTIRE number {phone} back digit by digit and ask: "
                "'Is that callback number correct?' Then STOP and wait for a clear yes. "
                "If the caller says no or corrects any digit, ask for the complete number again. "
                "Do not say the intake is saved yet."
            ),
        }

    def mark_saved(self, raw: dict, result: dict) -> None:
        record = _clean_record(raw)
        signature = json.dumps(record, sort_keys=True, ensure_ascii=False)
        self.saved[signature] = result
        self.pending = None
