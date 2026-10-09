# OpenAI Realtime branch

This branch answers the "what's a valid voice agent, compared to what?"
question by building the same voice-agent shape against OpenAI's Realtime
API, reusing `nova_sonic.config.DEFAULT_SYSTEM_PROMPT` unmodified. If the
prompt (persona + hard rules + program-card grounding) only works well on
one model, that's a real finding -- see `openai_realtime/config.py`.

**Status: wiring only.** This gets a conversation flowing end-to-end
(mic in, Realtime API, speaker out, barge-in). It does not yet have an
eval harness pointed at it -- that comes once the golden question set
from real call transcripts exists (see the knowledge-base work in
`knowledge/README.md` for the parallel situation on the Nova side).

## Which OpenAI API: Realtime, not Live

OpenAI has two speech-to-speech-shaped APIs as of Oct 2026. This branch
uses the **Realtime API** (`gpt-realtime-2.1`, `/v1/realtime`), not the
newer **Live API** (`gpt-live-1`, `/v1/live/sessions`), because:

- Realtime is one model handling the whole turn, closer in shape to Nova
  2 Sonic -- a fair architectural comparison.
- Live is explicitly a *delegation* architecture: a small, fast front-end
  model (`gpt-live-1`) handles the live voice turn and can delegate to a
  separate, more capable backend model (e.g. `gpt-5.6-luna`) for anything
  needing real reasoning or tools, then speaks the backend's answer. It's
  billed differently too (flat $0.05/min for the voice session, backend
  work billed separately) -- a genuinely different cost/latency tradeoff,
  not just a renamed Realtime.
- Live is worth evaluating *later*, specifically for the RAG-less
  constraint we're under: it could mean a cheap, always-on front-end voice
  model plus a slower backend model doing the heavier grounding/safety
  reasoning against the program cards. That's a second axis of comparison
  beyond "Nova vs. OpenAI," and deserves its own pass once this simpler
  Realtime branch has a working baseline.

## WebSocket vs. WebRTC

OpenAI recommends WebRTC for browser/mobile clients (it owns mic capture
and audio playback for you via native browser APIs) and WebSocket for
server-to-server use -- telephony bridges, IVR, or (our case) a desktop
process driving PyAudio directly, which is exactly what
`nova_sonic/audio.py` already does. This branch uses WebSocket to mirror
that.

The real cost of that choice: **on WebSocket, barge-in truncation is the
client's job.** With WebRTC or SIP, the server tracks how much audio it
has streamed and auto-truncates on interruption. On WebSocket, our client
has to track playback progress itself and send `conversation.item.truncate`
with an `audio_end_ms` -- see `openai_realtime/session.py`,
`_truncate_current_response()`. Nova 2 Sonic's bidirectional stream does
this server-side regardless of transport, so this is a genuine extra piece
of state this branch has that the Nova branch doesn't. The ms estimate
`_process_responses()` computes from audio-byte counts is a reasonable
approximation, not an exact clock -- worth validating by ear during
testing, not just trusting the math.

## Setup

```bash
pip install -r requirements.txt   # adds `websockets` for this branch
export OPENAI_API_KEY=sk-...      # https://platform.openai.com/api-keys
python -m openai_realtime
```

No AWS credentials needed for this branch. `openai_realtime/config.py` and
`openai_realtime/events.py` have no AWS or OpenAI SDK dependency and can be
imported standalone (same lazy-import pattern as `nova_sonic/__init__.py`).

## Model and session notes (as of Oct 2026)

- **Model:** `gpt-realtime-2.1` (current GA Realtime model; released as
  `gpt-realtime` Aug 2025, followed by `-2` and `-2.1`).
- **Voices:** `alloy`, `ash`, `ballad`, `coral`, `echo`, `sage`, `shimmer`,
  `verse`, `marin`, `cedar`. OpenAI recommends `marin` or `cedar` for best
  quality -- defaulted to `marin` here. Voice locks after the first audio
  response in a session; it cannot change mid-call (same constraint as
  Nova's `VOICE_ID`).
- **Audio format:** mono 16-bit PCM, 24kHz both directions by default
  (vs. Nova's 16kHz in / 24kHz out). No requirement to match Nova's rates
  since each branch's audio.py is self-contained.
- **Turn detection:** defaults to `semantic_vad` here (model judges
  end-of-turn by meaning, closer to Nova's sensitivity-tiered endpointing
  than classic silence-threshold `server_vad`). Can disable VAD entirely
  for push-to-talk -- see OpenAI's guide for the manual
  commit/response.create sequence that requires.
- **Session length cap: 60 minutes, hard limit.** Nova 2 Sonic's
  bidirectional stream has no documented equivalent cap. An intake call
  that runs long on this branch needs a reconnect/resume strategy that
  doesn't exist yet (`config.MAX_SESSION_SECONDS` is defined but not
  enforced). Flagging now so it's a known gap, not a surprise mid-call.
- **Function/tool calling** exists (`session.tools`, same request/response
  dance as Nova's tool pattern) but isn't wired up yet -- no equivalent of
  `get_program_card(id)` or `get_county_office(county)` on this branch.

## Pricing comparison (as of Oct 2026 -- re-verify before relying on this for budgeting)

| | Nova 2 Sonic (Bedrock) | GPT-Realtime-2.1 | GPT-Realtime-2.1-mini |
|---|---|---|---|
| Audio input | $3.00 / 1M tokens | $32.00 / 1M tokens | $10.00 / 1M tokens |
| Audio output | $12.00 / 1M tokens | $64.00 / 1M tokens | $20.00 / 1M tokens |
| Text input | $0.33 / 1M tokens | $4.00 / 1M tokens | $0.60 / 1M tokens |
| Text output | $2.75 / 1M tokens | $24.00 / 1M tokens | $2.40 / 1M tokens |
| Est. cost / minute of conversation | ~$0.015 | meaningfully higher -- audio tokens are ~10x Nova's rate on both legs | lower than full Realtime, still above Nova |

Nova 2 Sonic is markedly cheaper per audio token than full `gpt-realtime-2.1`
-- industry estimates put Nova around 80% cheaper per minute of
conversation. `gpt-realtime-2.1-mini` closes some of that gap. This is one
input into the eventual model choice, not the deciding factor by itself --
answer quality, latency, tool-calling reliability, and how well each model
follows the hard-rules prompt all matter too, which is exactly what the
eval harness (once the golden question set exists) should measure instead
of guessing from pricing pages alone.

For context, OpenAI's separate **Live API** (`gpt-live-1`) bills the
front-end voice leg flat at $0.05/minute regardless of audio token volume,
with backend model/tool calls billed at their own separate rates -- a
genuinely different cost shape worth comparing once that architecture is
evaluated (see "Which OpenAI API" above).

Sources: `developers.openai.com/api/docs/pricing`,
`developers.openai.com/api/docs/models/gpt-realtime-2.1`,
`developers.openai.com/api/docs/models/gpt-live-1`, AWS Bedrock pricing
pages. OpenAI's Realtime/Live pricing has changed multiple times since
launch (Aug 2025) -- treat these numbers as a snapshot, not a contract.

## What's deliberately NOT done yet

- No eval harness pointed at this branch (needs the golden question set
  first -- see the top-level TODO in chat/README, pending real call
  transcripts).
- No tool-calling / program-card grounding verification test analogous to
  `tests/test_knowledge.py`'s prompt-parity checks (grounding reuses the
  same string, so those tests already partially apply -- but nothing yet
  confirms the model actually *uses* the grounding well).
- No automatic reconnect at the 60-minute session cap.
- No Docker packaging (tracked separately, lower priority per current
  team discussion -- infra concern, not a quality concern).
