"""Regression: Sonic reads phone digits aloud as words, not numeric characters."""
import json

from nova_sonic.intake_confirmation import CallbackConfirmationGate
from nova_sonic import tools


def _readback(digits="one, two, three, four, five, six, seven, eight, nine, zero"):
    return [
        ("ASSISTANT", f"Is that callback number correct: {digits}?"),
        ("USER", "yes"),
    ]


def test_spoken_english_confirmation_saved_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "INTAKE_DIR", str(tmp_path))
    record = {"caller_name": "Example Caller", "callback_phone": "1234567890"}
    gate = CallbackConfirmationGate()
    before, denied = gate.check(record, [])
    assert before is None and denied["saved"] is False
    after, denied = gate.check(record, _readback())
    assert denied is None
    saved = tools.save_intake_sync(after)
    assert saved["saved"] is True
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))["callback_phone"] == "1234567890"


def test_readback_mismatch_is_rejected():
    gate = CallbackConfirmationGate()
    record = {"callback_phone": "1234567890"}
    approved, denied = gate.check(record, _readback("one, two, three, four, five, six, seven, eight, nine, nine"))
    assert approved is None and denied["saved"] is False


def test_readback_without_confirmation_is_rejected():
    gate = CallbackConfirmationGate()
    record = {"callback_phone": "1234567890"}
    approved, denied = gate.check(record, [("ASSISTANT", "My number is one two three four five six seven eight nine zero."), ("USER", "yes")])
    assert approved is None and denied["saved"] is False


def test_non_affirmative_reply_is_rejected():
    gate = CallbackConfirmationGate()
    record = {"callback_phone": "1234567890"}
    transcript = _readback()
    transcript[-1] = ("USER", "yes but that's the wrong number")
    approved, denied = gate.check(record, transcript)
    assert approved is None and denied["saved"] is False


def test_mixed_digits_and_words_readback():
    gate = CallbackConfirmationGate()
    record = {"callback_phone": "1234567890"}
    transcript = _readback("1 2 three four five six seven eight nine oh")
    approved, denied = gate.check(record, transcript)
    assert denied is None and approved["callback_phone"] == "1234567890"


def test_three_digit_readback_not_enough():
    gate = CallbackConfirmationGate()
    record = {"callback_phone": "1234567890"}
    approved, denied = gate.check(record, _readback("eight nine zero"))
    assert approved is None and denied["saved"] is False
