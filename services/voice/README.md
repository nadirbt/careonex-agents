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
# Speakers instead of headphones? The client gates mic audio during playback (NOVA_SONIC_ECHO_GATE,
# default RMS 1500; raise it if the assistant still interrupts itself, lower it if your barge-in is
# ignored). NOVA_SONIC_HALF_DUPLEX=1 mutes the mic while it speaks (no barge-in at all).
uv run careonex-voice-smoke --question "Does JACC have an income limit?" --play
```

Files: `session.py` stream lifecycle, credentials (env -> shared file -> SSO via botocore),
response loop, barge-in, tool dispatch; `events.py` JSON event builders including
`toolConfiguration` on promptStart and the contentStart/toolResult/contentEnd triplet;
`tools.py` the tool spec and handler; `audio.py` full-duplex PyAudio (optional); `smoke.py` the
test; `fixtures/question.wav` a 16 kHz synthesized question about Medicaid paying for in-home help.

The smoke test writes `data/voice-smoke.json` (transcripts, tool calls, reply length) and
`data/voice-smoke-reply.wav`.
