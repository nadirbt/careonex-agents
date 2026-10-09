"""OpenAI Realtime API (WebSocket transport) session.

Mirrors nova_sonic/session.py's shape on purpose (start/send_audio/close,
an audio_queue for playback, a barge_in flag) so the eventual A/B
comparison between branches is apples-to-apples at the call site, and so
nova_sonic/audio.py's DuplexAudio could plausibly be reused later with
minimal changes.

Transport choice: WebSocket, not WebRTC. WebRTC is what OpenAI recommends
for *browser* clients (it owns mic capture/playback for you), but this
mirrors nova_sonic/audio.py, which drives PyAudio directly from a desktop
process -- the "server-to-server" case the docs point at WebSocket for.
See README.md, "WebSocket vs. WebRTC" for the tradeoff this gives up
(we now do our own interruption truncation; see conversation_item.truncate
in events.py).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from typing import Any

import websockets

from openai_realtime import events
from openai_realtime.config import (
    INPUT_SAMPLE_RATE,
    MODEL_ID,
    OUTPUT_SAMPLE_RATE,
    REALTIME_WS_URL,
    SYSTEM_PROMPT,
    TURN_DETECTION_TYPE,
    VOICE_ID,
)

# config.MAX_SESSION_SECONDS (60 min, an OpenAI-imposed cap with no Nova
# equivalent) isn't enforced here yet -- no reconnect/resume logic exists.
# Fine for now since this branch is for getting a conversation going and
# comparing against Nova, not for intake calls long enough to hit it.


class OpenAIRealtimeSession:
    """Keeps a Realtime WebSocket open and queues playback audio."""

    def __init__(
        self,
        model_id: str = MODEL_ID,
        api_key: str | None = None,
        system_prompt: str = SYSTEM_PROMPT,
        voice: str = VOICE_ID,
    ) -> None:
        self.model_id = model_id
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.system_prompt = system_prompt
        self.voice = voice

        self.ws: Any = None
        self.is_active = False
        self.barge_in = False
        self.audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._receive_task: asyncio.Task[None] | None = None

        # Tracked for conversation.item.truncate on barge-in -- see
        # events.conversation_item_truncate and README.md "Interruption
        # handling is manual here."
        self._current_response_item_id: str | None = None
        self._current_response_content_index = 0
        self._response_audio_ms_elapsed = 0

    async def start(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. export OPENAI_API_KEY=... first "
                "(see openai_realtime/README.md)."
            )

        url = f"{REALTIME_WS_URL}?model={self.model_id}"
        self.ws = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {self.api_key}"},
        )
        self.is_active = True

        await self._send(
            events.session_update(
                model=self.model_id,
                instructions=self.system_prompt,
                voice=self.voice,
                input_rate=INPUT_SAMPLE_RATE,
                output_rate=OUTPUT_SAMPLE_RATE,
                turn_detection_type=TURN_DETECTION_TYPE,
            )
        )
        self._receive_task = asyncio.create_task(self._process_responses())

    async def _send(self, event: dict[str, Any]) -> None:
        if not self.ws:
            return
        await self.ws.send(json.dumps(event))

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if not self.is_active or not pcm_bytes:
            return
        encoded = base64.b64encode(pcm_bytes).decode("utf-8")
        await self._send(events.input_audio_buffer_append(encoded))

    def handle_barge_in(self) -> None:
        """Unlike Nova 2 Sonic, the server does NOT truncate playback for
        us on a WebSocket connection -- see README.md "Interruption
        handling is manual here." This flag stops local playback;
        _truncate_current_response() (called from _process_responses on
        speech_started) tells the server what was actually heard."""
        self.barge_in = True

    def drain_audio_queue(self) -> None:
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _truncate_current_response(self) -> None:
        if self._current_response_item_id is None:
            return
        await self._send(
            events.conversation_item_truncate(
                self._current_response_item_id,
                content_index=self._current_response_content_index,
                audio_end_ms=self._response_audio_ms_elapsed,
            )
        )
        self._current_response_item_id = None
        self._response_audio_ms_elapsed = 0

    async def _process_responses(self) -> None:
        try:
            async for raw in self.ws:
                payload = json.loads(raw)
                event_type = payload.get("type", "")

                if event_type == "input_audio_buffer.speech_started":
                    # Server already cancelled its in-progress response
                    # (response.cancelled follows); our job is to stop
                    # local playback and truncate what was actually heard.
                    print("Barge-in: user started speaking.")
                    self.handle_barge_in()
                    await self._truncate_current_response()

                elif event_type == "response.output_item.added":
                    item = payload.get("item") or {}
                    if item.get("type") == "message":
                        self._current_response_item_id = item.get("id")
                        self._current_response_content_index = 0
                        self._response_audio_ms_elapsed = 0

                elif event_type == "response.output_audio.delta":
                    audio_b64 = payload.get("delta", "")
                    if audio_b64:
                        await self.audio_queue.put(base64.b64decode(audio_b64))
                        # Rough running estimate of played-out audio, used
                        # if a barge-in happens before response.done.
                        # 16-bit mono PCM: bytes / 2 / sample_rate * 1000.
                        decoded_len = len(base64.b64decode(audio_b64))
                        self._response_audio_ms_elapsed += int(
                            decoded_len / 2 / OUTPUT_SAMPLE_RATE * 1000
                        )

                elif event_type == "response.output_text.delta":
                    print(payload.get("delta", ""), end="", flush=True)

                elif event_type == "response.done":
                    self._current_response_item_id = None
                    self._response_audio_ms_elapsed = 0

                elif event_type == "error":
                    print(f"Realtime API error: {payload.get('error')}")

        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosed as exc:
            print(f"Realtime WebSocket closed: {exc}")
        except Exception as exc:
            print(f"Error reading Realtime stream: {exc}")

    async def close(self) -> None:
        if not self.ws:
            return
        self.is_active = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        try:
            await self.ws.close()
        except Exception:
            pass
        finally:
            self.ws = None
