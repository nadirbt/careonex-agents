# services/voice — Nova 2 Sonic client

The speech-to-speech layer. Streams microphone (or a WAV) to `amazon.nova-2-sonic-v1:0` over
Bedrock's bidirectional stream, plays the spoken reply, handles server-side barge-in, and answers
the model's `lookup_program_info` tool calls by asking the `retrieve` service for cited passages.

Two ways to run:

```bash
# Container: mic-free smoke test (what `make smoke` runs). Needs the retrieve service up for a real
# tool round trip; without CAREONEX_RETRIEVE_URL the tool reports "knowledge base unavailable" and
# the model promises a follow-up, which still proves the protocol.
docker compose run --rm voice
docker compose run --rm voice --wav /data/my-question.wav

# Laptop: interactive microphone client (PyAudio needs PortAudio; not in the image on purpose)
brew install portaudio
cd services/voice && uv sync --extra mic
export AWS_PROFILE=careonex-team AWS_DEFAULT_REGION=us-east-1 CAREONEX_RETRIEVE_URL=http://localhost:8080
uv run careonex-voice            # speak; Enter to quit
# Speakers instead of headphones? The gate predicts echo from what is being played right now and
# forwards only mic audio 1.8x louder than that (NOVA_SONIC_ECHO_RATIO; lower it if interruptions are
# ignored, raise it if the assistant interrupts itself). NOVA_SONIC_ECHO_GATE=0 disables the gate;
# NOVA_SONIC_HALF_DUPLEX=1 mutes the mic while it speaks (no barge-in at all).
uv run careonex-voice-smoke --question "Does JACC have an income limit?" --play
```

Files: `session.py` stream lifecycle, credentials (env -> shared file -> SSO via botocore),
response loop, barge-in, tool dispatch; `events.py` JSON event builders including
`toolConfiguration` on promptStart and the contentStart/toolResult/contentEnd triplet;
`tools.py` the tool spec and handler; `audio.py` full-duplex PyAudio (optional); `smoke.py` the
test; `fixtures/question.wav` a 16 kHz synthesized question about Medicaid paying for in-home help.

The smoke test writes `data/voice-smoke.json` (transcripts, tool calls, reply length) and
`data/voice-smoke-reply.wav`.

## Language switching and clarification

A caller can ask to switch to another language using `set_conversation_language`. The app does not impose a fixed language allowlist; recognition and spoken output in any requested language depend on the capabilities of Nova 2 Sonic and must be verified live. Some languages may be experimental or unreliable.

Intake is intended to proceed one question at a time. The agent should answer a caller's clarification or eligibility question before returning to intake; the `intake_next_question` and `save_intake` tools validate important fields and callback confirmation. Prompting is not a guarantee of model behavior; confirm with live conversations.

## Model-free query expansion for voice lookups

Voice `lookup_program_info` now requests `search_mode=feedback` by default.
It searches the original question first, optionally makes up to two relevant
heading-derived extra queries, and merges results with RRF. No Nova Lite
permission is needed. To switch the **voice** back to single-query search,
set `CAREONEX_VOICE_SEARCH_MODE=baseline` before starting the process.
See [`../../docs/EVALUATION.md`](../../docs/EVALUATION.md) for how to evaluate retrieval modes.

## Where does call data go?

- A normal call does not automatically generate a permanent transcript or intake.
- When a caller explicitly requests a callback and confirms their ten-digit phone number, `save_intake` persists JSON locally under `services/voice/data/intakes/` (or `CAREONEX_INTAKE_DIR`). The console prints the exact absolute filename.
- On exit, the microphone client prints the count of confirmed saves and whether a save request was rejected. If it says `No save_intake tool call occurred`, the agent did not finish a confirmed callback intake.
- For synthetic debugging only, set `CAREONEX_EVAL_REPORT` to an absolute JSON filename before launching the voice client. This opt-in saves transcripts and tool results after the call; it can include names and phone numbers. Never commit it.
- Local records are *not* pushed to GitHub and there is no coordinator/database integration yet. A repository ZIP has no saved caller records, by design.
