"""Microphone + speaker I/O. Mic stays open so Sonic can barge-in natively."""

from __future__ import annotations

import array
import asyncio
import math
import time
from typing import Any, Optional

from nova_sonic.config import (
    CHANNELS,
    CHUNK_FRAMES,
    ECHO_GATE_RMS,
    HALF_DUPLEX,
    INPUT_SAMPLE_RATE,
    OUTPUT_SAMPLE_RATE,
    PLAYBACK_TAIL_S,
)


def rms_int16(data: bytes) -> float:
    """Root-mean-square level of 16-bit little-endian PCM, 0..32767."""
    if len(data) < 2:
        return 0.0
    samples = array.array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def should_forward(data: bytes, playing: bool, gate_rms: int = ECHO_GATE_RMS, half_duplex: bool = HALF_DUPLEX) -> bool:
    """Decide whether a mic chunk goes to Nova. Everything passes when the assistant is silent;
    during playback, half-duplex drops all audio and the gate drops anything quieter than gate_rms."""
    if not playing:
        return True
    if half_duplex:
        return False
    if gate_rms <= 0:
        return True
    return rms_int16(data) >= gate_rms
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
        self._last_write = 0.0  # monotonic time of the last speaker write
        self.gated_chunks = 0

    @property
    def playing(self) -> bool:
        return (time.monotonic() - self._last_write) < PLAYBACK_TAIL_S

    def _on_mic(self, in_data, frame_count, time_info, status):
        if self.running and in_data:
            if should_forward(in_data, self.playing):
                asyncio.run_coroutine_threadsafe(self.session.send_audio(in_data), self._loop)
            else:
                self.gated_chunks += 1
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
                self._last_write = time.monotonic()
                await asyncio.get_event_loop().run_in_executor(None, self._output.write, piece)
                self._last_write = time.monotonic()
                await asyncio.sleep(0)

    async def start(self) -> None:
        print("Listening. Speak anytime — you can interrupt the assistant.")
        if HALF_DUPLEX:
            print("Half-duplex: the mic is muted while the assistant speaks (NOVA_SONIC_HALF_DUPLEX=1).")
        elif ECHO_GATE_RMS > 0:
            print(f"Echo gate: while the assistant speaks, only mic audio louder than RMS {ECHO_GATE_RMS} is sent (NOVA_SONIC_ECHO_GATE). Headphones avoid echo entirely.")
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
