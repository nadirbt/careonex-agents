"""Microphone + speaker I/O. Mic stays open so Sonic can barge-in natively."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from nova_sonic.config import (
    CHANNELS,
    CHUNK_FRAMES,
    INPUT_SAMPLE_RATE,
    OUTPUT_SAMPLE_RATE,
)
from nova_sonic.session import NovaSonicSession


class DuplexAudio:
    def __init__(self, session: NovaSonicSession) -> None:
        try:
            import pyaudio  # noqa: PLC0415 - optional dependency, only the interactive mic client needs it
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "PyAudio is not installed. For the microphone client run `brew install portaudio` then "
                "`uv sync --extra mic` in services/voice. The container only runs the mic-free smoke test."
            ) from exc
        self._pyaudio_mod: Any = pyaudio
        self.session = session
        self.running = False
        self._loop = asyncio.get_running_loop()
        self._pyaudio: Optional[Any] = pyaudio.PyAudio()
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
        return (None, self._pyaudio_mod.paContinue)

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
        print("Listening. Speak anytime — you can interrupt the assistant.")
        print("Press Enter to end the session.")
        await self.session.start_audio()
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
