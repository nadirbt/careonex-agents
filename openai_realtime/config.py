"""Config for the OpenAI Realtime API branch -- the direct architectural
analog of nova_sonic/config.py.

Deliberately reuses nova_sonic.config.DEFAULT_SYSTEM_PROMPT unmodified. The
prompt-design work (persona, hard rules, program-card grounding) has zero
AWS dependency and zero Nova-specific assumptions, so the same prompt
string should work against either model. If a prompt change only "works"
on one of the two branches, that is itself a useful finding -- it means
the prompt depends on a model-specific quirk (and we may need a per-model
instructions variant) rather than just encoding our actual policy.
"""

from __future__ import annotations

import os

from nova_sonic.config import DEFAULT_SYSTEM_PROMPT  # noqa: F401 (re-exported)

# gpt-realtime-2.1 is OpenAI's current speech-to-speech Realtime model as of
# Oct 2026 (released as "gpt-realtime" in Aug 2025; -2 and -2.1 followed).
# Not to be confused with "gpt-live-1" (the newer, per-second-billed
# delegation API at /v1/live/sessions) -- see README.md "Which OpenAI API"
# for why this branch targets Realtime, not Live.
MODEL_ID = os.environ.get("OPENAI_REALTIME_MODEL_ID", "gpt-realtime-2.1")
REALTIME_WS_URL = os.environ.get(
    "OPENAI_REALTIME_WS_URL", "wss://api.openai.com/v1/realtime"
)

# Built-in voices as of the Realtime conversations guide (Oct 2026):
# alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin, cedar.
# OpenAI recommends marin or cedar for best quality. Voice is locked once
# the model has emitted audio in a session -- it cannot be changed mid-call.
VOICE_ID = os.environ.get("OPENAI_REALTIME_VOICE_ID", "marin")

# Realtime audio format: mono, 16-bit PCM. Unlike Nova 2 Sonic (16kHz in /
# 24kHz out), OpenAI's docs default both directions to 24kHz -- there's no
# requirement to match Nova's rates, so we use OpenAI's default rather than
# forcing symmetry with the Nova branch.
INPUT_SAMPLE_RATE = int(os.environ.get("OPENAI_REALTIME_INPUT_RATE", "24000"))
OUTPUT_SAMPLE_RATE = int(os.environ.get("OPENAI_REALTIME_OUTPUT_RATE", "24000"))
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
CHUNK_FRAMES = 512

# "semantic_vad" (model judges end-of-turn by meaning, not just silence) vs
# "server_vad" (classic silence-threshold VAD). semantic_vad is the closer
# analog to Nova's ENDPOINTING_SENSITIVITY=MEDIUM/HIGH/LOW behavior.
TURN_DETECTION_TYPE = os.environ.get("OPENAI_REALTIME_TURN_DETECTION", "semantic_vad")

# A Realtime session is capped at 60 minutes by OpenAI, regardless of
# activity -- there is no equivalent documented cap on the Nova 2 Sonic
# bidirectional stream. Long intake calls may need a reconnect/resume
# strategy on this branch that Nova doesn't require. Flagging here so it
# isn't rediscovered the hard way mid-call.
MAX_SESSION_SECONDS = 60 * 60

SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT
