"""Regressions based on two live calls where the assistant saved 4704770477
while the caller intended 4704774777 and explicitly said the readback was wrong.
"""
import asyncio
import json

import nova_sonic.session as session_mod
from nova_sonic.intake_confirmation import CallbackConfirmationGate
from nova_sonic.session import NovaSonicSession
from nova_sonic.tools import intake_next_question_sync


class FakeInput:
    def __init__(self):
        self.sent = []

    async def send(self, part):
        self.sent.append(json.loads(part.value.bytes_.decode('utf-8'))['event'])


class FakeStream:
    def __init__(self):
        self.input_stream = FakeInput()


def test_never_saves_without_readback_and_explicit_yes():
    gate = CallbackConfirmationGate()
    args = {"caller_name": "Marco", "callback_phone": "4704770477"}
    assert gate.check(args, [])[1]["confirmation_required"]
    assert gate.check(args, [("USER", "yes")])[1]["confirmation_required"]
    # An already-completed exact readback+confirmation needs no extra prompt.
    assert gate.check(args, [("ASSISTANT", "Is your phone 4704770477?"), ("USER", "yes")])[0]["callback_phone"] == "4704770477"

    utterances = [("ASSISTANT", "I have 470-477-0477. Is that right?"), ("USER", "no that is incorrect")]
    assert gate.check(args, utterances)[1]["confirmation_required"]
    assert gate.check(args, utterances + [("USER", "yes")])[1]["confirmation_required"]  # mixed contradictory answer


def test_caller_correction_restarts_confirmation():
    gate = CallbackConfirmationGate()
    wrong = {"caller_name": "Marco", "callback_phone": "4704770477"}
    right = {"caller_name": "Marco", "callback_phone": "4704774777"}
    gate.check(wrong, [])
    t = [("ASSISTANT", "The phone number I have is 4704770477."), ("USER", "no that is incorrect")]
    assert gate.check(wrong, t)[0] is None
    assert gate.check(right, t)[1]["confirmation_required"]  # candidate changed
    t += [("ASSISTANT", "Is 470-477-4777 correct?"), ("USER", "yes that is correct")]
    approved, response = gate.check(right, t)
    assert response is None and approved["callback_phone"] == "4704774777"
    gate.mark_saved(right, {"saved": True, "intake_id": "abc"})
    assert gate.check(right, t)[1]["already_saved"]


def test_partial_phone_or_sentinel_unknown_are_not_saved():
    gate = CallbackConfirmationGate()
    assert gate.check({"callback_phone": "470477"}, [])[1]["error"] == "invalid callback_phone"
    intake = intake_next_question_sync({"relationship":"son","care_recipient_age":"74","county":"Monmouth","kind_of_help":"bathing","hours_per_week":"not specified"})
    assert intake["field"] == "timeline"  # hours remain optional for a callback


def test_session_blocks_save_until_number_confirmed(monkeypatch):
    saved = []

    async def stub_tool(name, data):
        if name == "save_intake":
            saved.append(json.loads(data))
            return json.dumps({"saved": True, "intake_id": "one"})
        return json.dumps({})

    monkeypatch.setattr(session_mod, 'handle_tool', stub_tool)

    async def scenario():
        s = NovaSonicSession()
        s.stream = FakeStream()
        s.transcripts.extend([
            ('USER', "I am her son. She needs care in Hudson County starting within a month."),
            ('ASSISTANT', "What name should the coordinator ask for?"),
            ('USER', "Marco"),
        ])
        p = '{"caller_name":"Marco","relationship":"son","county":"Hudson","timeline":"within a month","callback_phone":"4704770477"}'
        await s._start_tool({"toolName":"save_intake","toolUseId":"s1","content":p})
        s.transcripts.extend([('ASSISTANT', 'The number is 470-477-0477. Is this correct?'), ('USER', 'no, wrong')])
        await s._start_tool({"toolName":"save_intake","toolUseId":"s2","content":p})
        assert saved == []
        corrected = '{"caller_name":"Marco","relationship":"son","county":"Hudson","timeline":"within a month","callback_phone":"4704774777"}'
        await s._start_tool({"toolName":"save_intake","toolUseId":"s3","content":corrected})
        assert saved == []
        s.transcripts.extend([('ASSISTANT', '470-477-4777. Is this phone number correct?'), ('USER', 'yes that is correct')])
        await s._start_tool({"toolName":"save_intake","toolUseId":"s4","content":corrected})
        await s._start_tool({"toolName":"save_intake","toolUseId":"s5","content":corrected})
        return s

    session = asyncio.run(scenario())
    assert len(saved) == 1
    assert saved[0]['callback_phone'] == '4704774777'
    assert json.loads(session.tool_calls[0]['output'])['saved'] is False
    assert json.loads(session.tool_calls[-1]['output'])['already_saved'] is True


def test_only_explicit_phone_yes_counts():
    gate = CallbackConfirmationGate()
    record = {"callback_phone": "4704774777"}
    assert gate.check(record, [("ASSISTANT", "Is 470-477-4777 the correct phone number?"), ("USER", "yes, that is correct")])[0] is not None
    gate2 = CallbackConfirmationGate()
    assert gate2.check(record, [("ASSISTANT", "Is 470-477-4777 the correct phone number?"), ("USER", "yes, but I need to change it")])[0] is None


def test_unrelated_yes_does_not_confirm_old_phone():
    gate = CallbackConfirmationGate()
    args = {"callback_phone": "4704774777"}
    history = [
        ("ASSISTANT", "I have 4704774777. Is that right?"),
        ("USER", "not sure"),
        ("ASSISTANT", "Do you want to continue the intake?"),
        ("USER", "yes"),
    ]
    assert gate.check(args, history)[0] is None


def test_phone_readback_must_ask_for_confirmation():
    gate = CallbackConfirmationGate()
    history = [
        ("ASSISTANT", "Your callback number is 4704774777."),
        ("USER", "yes"),
    ]
    assert gate.check({"callback_phone": "4704774777"}, history)[0] is None


def test_candidate_phone_requires_new_approval_after_negative():
    gate = CallbackConfirmationGate()
    record = {"callback_phone": "4704774777"}
    history = [("ASSISTANT", "Is 4704774777 correct?"), ("USER", "no")]
    assert gate.check(record, history)[0] is None
    history.extend([("ASSISTANT", "Is 4704774777 correct now?"), ("USER", "yes")])
    assert gate.check(record, history)[0] is not None


def test_full_phone_readback_in_spoken_english_words():
    gate = CallbackConfirmationGate()
    intake = {"caller_name": "Test Caller", "callback_phone": "1234567890"}
    assert gate.check(intake, [])[1]["confirmation_required"]
    transcript = [
        ("ASSISTANT", "Is that callback number correct: one, two, three, four, five, six, seven, eight, nine, zero?"),
        ("USER", "yes"),
    ]
    approved, error = gate.check(intake, transcript)
    assert error is None and approved["callback_phone"] == "1234567890"


def test_word_readback_must_be_complete_and_confirmed():
    intake = {"callback_phone": "1234567890"}
    readback = "Is your callback number one, two, three, four, five, six, seven, eight, nine correct?"
    assert CallbackConfirmationGate().check(intake, [("ASSISTANT", readback), ("USER", "yes")])[0] is None
    readback = "Is your callback number one two three four five six seven eight nine zero correct?"
    assert CallbackConfirmationGate().check(intake, [("ASSISTANT", readback), ("USER", "no")])[0] is None
    assert CallbackConfirmationGate().check(intake, [("ASSISTANT", readback), ("USER", "yes but change it")])[0] is None


def test_mixed_numeric_and_word_readback():
    intake = {"callback_phone": "1234567890"}
    readback = "Is your number 1 2 3 four five six seven eight nine oh correct?"
    assert CallbackConfirmationGate().check(intake, [("ASSISTANT", readback), ("USER", "that's correct")])[0] is not None
