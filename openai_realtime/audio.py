"""Microphone + speaker I/O for the OpenAI Realtime branch.

Deliberately parallel to nova_sonic/audio.py, parameterized on
openai_realtime.config's sample rates instead of nova_sonic's. The two
aren't merged into one shared implementation yet because nova_sonic.audio
hardcodes its config import -- see nova_sonic/audio.py line 10. Worth
collapsing into one generic DuplexAudio(session, sample_rates) once both
branches are stable; not done here to keep this branch's diff isolated.

With VAD enabled (the default -- see config.TURN_DETECTION_TYPE), there is
no Nova-style "start_audio" content-start event to send first: the server
starts listening as soon as audio arrives. Mic frames are sent to
session.send_audio() directly from the callback.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import pyaudio

from openai_realtime.config import (
    CHANNELS,
    CHUNK_FRAMES,
    INPUT_SAMPLE_RATE,
    OUTPUT_SAMPLE_RATE,
)
from openai_realtime.session import OpenAIRealtimeSession


class DuplexAudio:
    def __init__(self, session: OpenAIRealtimeSession) -> None:
        self.session = session
        self.running = False
        self._loop = asyncio.get_running_loop()
        self._pyaudio: Optional[pyaudio.PyAudio] = pyaudio.PyAudio()
        self._input = self._pyaudio.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=INPUT_SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_FRAMES,
            stream_callback=self._on_mic,
        )
        self._output = self._pyaudio.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=OUTPUT_SAMPLE_RATE,
            output=True,
            frames_per_buffer=CHUNK_FRAMES,
        )
        self._playback_task: asyncio.Task[None] | None = None

    def _on_mic(self, in_data, frame_count, time_info, status):
        if self.running and in_data:
            asyncio.run_coroutine_threadsafe(self.session.send_audio(in_data), self._loop)
        return (None, pyaudio.paContinue)

    async def _play(self) -> None:
        write_chunk = CHUNK_FRAMES * CHANNELS * 2
        while self.running:
            if self.session.barge_in:
                self.session.drain_audio_queue()
                self.session.barge_in = False
                await asyncio.sleep(0.03)
                continue

            try:
                audio = await asyncio.wait_for(self.session.audio_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            for offset in range(0, len(audio), write_chunk):
                if not self.running or self.session.barge_in:
                    break
                piece = audio[offset : offset + write_chunk]
                await asyncio.get_event_loop().run_in_executor(None, self._output.write, piece)
                await asyncio.sleep(0)

    async def start(self) -> None:
        print("Listening. Speak anytime -- you can interrupt the assistant.")
        print("Press Enter to end the session.")
        self.running = True
        if not self._input.is_active():
            self._input.start_stream()
        self._playback_task = asyncio.create_task(self._play())
        await asyncio.get_event_loop().run_in_executor(None, input)
        await self.stop()

    async def stop(self) -> None:
        if not self._pyaudio:
            return
        self.running = False
        if self._playback_task and not self._playback_task.done():
            self._playback_task.cancel()
            try:
                await self._playback_task
            except asyncio.CancelledError:
                pass
        try:
            if self._input.is_active():
                self._input.stop_stream()
            self._input.close()
            if self._output.is_active():
                self._output.stop_stream()
            self._output.close()
            self._pyaudio.terminate()
        finally:
            self._pyaudio = None
        await self.session.close()
