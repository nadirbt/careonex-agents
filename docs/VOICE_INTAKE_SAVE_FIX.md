# Voice intake save correction

The voice confirmation gate previously checked only numeric characters in the assistant's spoken phone-number readback. Nova Sonic often transcribes the readback as English words ("one, two, three..."). The gate therefore rejected the save even after the caller said "yes", while the model could incorrectly claim that data was saved.

## Changes (voice only)

- `services/voice/nova_sonic/intake_confirmation.py`: recognize numeric and English spoken-digit readbacks; still require a complete exact phone number, an explicit confirmation question, and a subsequent unambiguous affirmative user reply.
- `services/voice/nova_sonic/session.py`: print `Intake NOT saved` when the confirmation gate rejects a tool request. A `Tool: save_intake(...)` log is a request, **not** a success confirmation.
- `services/voice/tests/test_spoken_phone_readback.py` and updated `test_callback_confirmation.py`: regression coverage.

All RAG/chunking/Knowledge Base/retrieval/evaluation code remains byte-for-byte unchanged.

## How to apply on Windows

Stop your voice session, not the separate Docker retrieval service. Extract the PATCH ZIP into the root folder of the existing `careonex-agents-rag` project, preserving the `services/voice/...` structure and replacing the indicated files. Restart the microphone voice command as usual.

**Verify:** A confirmed, successful intake prints `Intake saved: data\intakes\...json` in the voice PowerShell window. A denied request prints `Intake NOT saved: ...`. Use `Get-ChildItem .\services\voice\data\intakes\*.json` to check the records. No existing intake records or AWS resources are modified by applying this patch.

Note: This does not implement editing an intake after it is saved. Any county/timeline volunteered afterward is not automatically written to the existing record.
