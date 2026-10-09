"""Text-in, text-out smoke test for OpenAI's Realtime API -- no microphone
or speaker needed. Good for a demo: fast, cheap (text tokens, not audio
tokens), and nothing can go wrong with mic permissions mid-presentation.

Confirms three things at once: the API key works, the WebSocket connection
and session.update succeed, and -- most importantly -- that
nova_sonic.config.DEFAULT_SYSTEM_PROMPT's program-card grounding actually
makes it into the model's answer (ask it something only a program card
would know, like "What is NJ MLTSS?", and check the answer names the
program and doesn't just guess).

Usage:
    export OPENAI_API_KEY=sk-...
    python -m openai_realtime.smoke_test "Do I qualify for NJ MLTSS?"
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

from openai_realtime import events
from openai_realtime.config import MODEL_ID, REALTIME_WS_URL, SYSTEM_PROMPT
from openai_realtime.session import OpenAIRealtimeSession  # noqa: F401 (see note below)

# This script intentionally does NOT reuse OpenAIRealtimeSession -- that
# class is built around audio.output_audio.delta events for the voice loop.
# A text-only round trip is simpler to express directly against the raw
# WebSocket. If you later want tool-calling or audio in this smoke test,
# switching to OpenAIRealtimeSession is the right move.


async def ask(question: str, *, timeout_seconds: float = 20.0) -> None:
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
                    output_rate=24000,
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
        await ws.send(json.dumps(events.response_create(output_modalities=("text",))))

        print(f"You: {question}")
        print("CareOneX (gpt-realtime-2.1): ", end="", flush=True)

        async def _listen() -> None:
            async for raw in ws:
                payload = json.loads(raw)
                event_type = payload.get("type", "")
                if event_type == "response.output_text.delta":
                    print(payload.get("delta", ""), end="", flush=True)
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


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python -m openai_realtime.smoke_test "your question"')
        raise SystemExit(1)
    asyncio.run(ask(" ".join(sys.argv[1:])))


if __name__ == "__main__":
    main()
