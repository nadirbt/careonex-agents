"""Smoke test for OpenAI's Realtime API -- no microphone needed either way.

Two modes:
- Default (text): fast, cheap (text tokens only), good for a quick sanity
  check or a demo where you don't want to rely on speakers working.
- --voice: asks for an audio reply instead, prints the spoken transcript
  as it streams, and saves the actual audio to a .wav file -- the
  OpenAI-side equivalent of nova_sonic/smoke_test.py's .wav output, so you
  can compare how each model *sounds* answering the same question.

Confirms the API key works, the WebSocket connection and session.update
succeed, and -- most importantly -- that
nova_sonic.config.DEFAULT_SYSTEM_PROMPT's program-card grounding actually
makes it into the model's answer (ask it something only a program card
would know, like "What is NJ MLTSS?", and check the answer names the
program and doesn't just guess).

Usage:
    export OPENAI_API_KEY=sk-...
    python -m openai_realtime.smoke_test "Do I qualify for NJ MLTSS?"
    python -m openai_realtime.smoke_test "Do I qualify for NJ MLTSS?" --voice
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import wave

import websockets

from openai_realtime import events
from openai_realtime.config import MODEL_ID, OUTPUT_SAMPLE_RATE, REALTIME_WS_URL, SYSTEM_PROMPT
from openai_realtime.session import OpenAIRealtimeSession  # noqa: F401 (see note below)

# This script intentionally does NOT reuse OpenAIRealtimeSession -- that
# class is built around a continuous mic/speaker loop for openai_realtime
# __main__.py. A one-shot text or audio round trip is simpler to express
# directly against the raw WebSocket. If you later want tool-calling or a
# multi-turn conversation in a smoke test, switching to
# OpenAIRealtimeSession is the right move.

OUTPUT_WAV_PATH = "openai_realtime_smoke_test_reply.wav"


async def ask(question: str, *, voice_output: bool = False, timeout_seconds: float = 20.0) -> None:
    import os

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Set OPENAI_API_KEY first (see openai_realtime/README.md).")
        raise SystemExit(1)

    url = f"{REALTIME_WS_URL}?model={MODEL_ID}"
    async with websockets.connect(
        url, additional_headers={"Authorization": f"Bearer {api_key}"}
    ) as ws:
        await ws.send(
            json.dumps(
                events.session_update(
                    model=MODEL_ID,
                    instructions=SYSTEM_PROMPT,
                    voice="marin",
                    input_rate=24000,
                    output_rate=OUTPUT_SAMPLE_RATE,
                    turn_detection_type=None,  # no audio input in this script
                )
            )
        )

        await ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": question}],
                    },
                }
            )
        )
        # session.update hardcodes output_modalities=["audio"] (see
        # events.py), so a response.create override is only needed for the
        # TEXT-only path below; the voice path just takes that default.
        if not voice_output:
            await ws.send(json.dumps(events.response_create(output_modalities=("text",))))

        print(f"You: {question}")
        label = "spoken reply" if voice_output else "gpt-realtime-2.1"
        print(f"CareOneX ({label}): ", end="", flush=True)

        audio_bytes = bytearray()

        async def _listen() -> None:
            async for raw in ws:
                payload = json.loads(raw)
                event_type = payload.get("type", "")
                if event_type == "response.output_text.delta":
                    print(payload.get("delta", ""), end="", flush=True)
                elif event_type == "response.output_audio_transcript.delta":
                    # The words the model is speaking, streamed as text --
                    # this is what you'd show as captions during a demo.
                    print(payload.get("delta", ""), end="", flush=True)
                elif event_type == "response.output_audio.delta":
                    audio_bytes.extend(base64.b64decode(payload.get("delta", "")))
                elif event_type == "response.done":
                    print()
                    return
                elif event_type == "error":
                    print(f"\n[error] {payload.get('error')}")
                    return

        try:
            await asyncio.wait_for(_listen(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            print(f"\n[no response within {timeout_seconds}s]")

        if voice_output and audio_bytes:
            with wave.open(OUTPUT_WAV_PATH, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)  # 16-bit PCM
                wav_file.setframerate(OUTPUT_SAMPLE_RATE)
                wav_file.writeframes(bytes(audio_bytes))
            print(f"Saved spoken reply to {OUTPUT_WAV_PATH} ({len(audio_bytes)} bytes).")
        elif voice_output:
            print("No audio came back -- see any [error] above.")


def main() -> None:
    args = sys.argv[1:]
    voice_output = "--voice" in args
    args = [a for a in args if a != "--voice"]
    if not args:
        print('Usage: python -m openai_realtime.smoke_test "your question" [--voice]')
        raise SystemExit(1)
    asyncio.run(ask(" ".join(args), voice_output=voice_output))


if __name__ == "__main__":
    main()

