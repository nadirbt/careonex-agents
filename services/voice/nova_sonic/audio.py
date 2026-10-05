"""Microphone + speaker I/O. Mic stays open so Sonic can barge-in natively."""

from __future__ import annotations

import array
import asyncio
import math
import time
from collections import deque
from typing import Any, Optional

from nova_sonic.config import (
    CHANNELS,
    CHUNK_FRAMES,
    ECHO_GATE_ENABLED,
    ECHO_GATE_HOLD_CHUNKS,
    ECHO_GATE_MIN_RMS,
    ECHO_GATE_RATIO,
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


class EchoGate:
    """Output-correlated echo gate for microphone audio while the assistant is speaking.

    The client knows what it is playing. Expected echo at the mic is `k * output_rms`, where
    output_rms is the loudest speaker chunk in the last `window_s` (covers device latency and
    room reverb) and k is the echo coupling learned online from chunks judged to be echo. A mic
    chunk is forwarded when its RMS exceeds `max(min_rms, ratio * k * output_rms)`; it then holds
    the gate open for `hold_chunks` so speech onsets survive. In the assistant's pauses output_rms
    falls to ~0 and the bar drops to `min_rms`, so a caller can always get a word in.
    When the assistant is silent everything is forwarded."""

    # Coupling is tracked as a slowly decaying PEAK, not an average. Averaging over chunks judged to be
    # echo is biased low (the quietest echo chunks, between the assistant's words, dominate), and a low k
    # lets real echo through: a live run drifted to k=0.03 and the assistant started hearing itself again.
    K_INIT, K_MIN, K_MAX, K_DECAY = 0.25, 0.08, 1.0, 0.999  # decay per echo chunk (~32 ms): halves in ~22 s

    def __init__(self, enabled: bool = ECHO_GATE_ENABLED, half_duplex: bool = HALF_DUPLEX, min_rms: int = ECHO_GATE_MIN_RMS,
                 ratio: float = ECHO_GATE_RATIO, hold_chunks: int = ECHO_GATE_HOLD_CHUNKS, window_s: float = 0.3,
                 k_init: float | None = None) -> None:
        self.enabled, self.half_duplex = enabled, half_duplex
        self.min_rms, self.ratio, self.hold_chunks, self.window_s = min_rms, ratio, hold_chunks, window_s
        self.k = k_init if k_init is not None else self.K_INIT  # echo coupling: mic RMS per unit of output RMS
        self._out: deque[tuple[float, float]] = deque(maxlen=64)
        self._hold = 0
        self.forwarded_during_playback = 0
        self.dropped = 0
        self.clock = time.monotonic

    def note_output(self, pcm: bytes) -> None:
        self._out.append((self.clock(), rms_int16(pcm)))

    def recent_output_rms(self) -> float:
        now = self.clock()
        levels = [r for (ts, r) in self._out if now - ts <= self.window_s]
        return max(levels) if levels else 0.0

    def threshold(self, output_rms: float) -> float:
        return max(float(self.min_rms), self.ratio * self.k * output_rms)

    def should_forward(self, data: bytes, playing: bool) -> bool:
        if not playing:
            self._hold = 0
            return True
        if self.half_duplex:
            self.dropped += 1
            return False
        if not self.enabled:
            return True
        if self._hold > 0:
            self._hold -= 1
            self.forwarded_during_playback += 1
            return True
        level = rms_int16(data)
        out = self.recent_output_rms()
        if level > self.threshold(out):
            self._hold = self.hold_chunks
            self.forwarded_during_playback += 1
            return True
        # Judged echo: track the coupling as a decaying peak (only when there is real output to compare to).
        if out > 100:
            observed = level / out
            self.k = min(self.K_MAX, max(self.K_MIN, observed, self.k * self.K_DECAY))
        self.dropped += 1
        return False


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
        self.gate = EchoGate()

    @property
    def playing(self) -> bool:
        return (time.monotonic() - self._last_write) < PLAYBACK_TAIL_S

    def _on_mic(self, in_data, frame_count, time_info, status):
        if self.running and in_data:
            if self.gate.should_forward(in_data, self.playing):
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
                self.gate.note_output(piece)
                self._last_write = time.monotonic()
                await asyncio.get_event_loop().run_in_executor(None, self._output.write, piece)
                self._last_write = time.monotonic()
                await asyncio.sleep(0)

    async def start(self) -> None:
        print("Listening. Speak anytime — you can interrupt the assistant.")
        if HALF_DUPLEX:
            print("Half-duplex: the mic is muted while the assistant speaks (NOVA_SONIC_HALF_DUPLEX=1).")
        elif ECHO_GATE_ENABLED:
            print(f"Echo gate: mic audio counts as an interruption only when {ECHO_GATE_RATIO:g}x louder than the echo expected from what is "
                  f"playing right now (min RMS {ECHO_GATE_MIN_RMS}). NOVA_SONIC_ECHO_RATIO adjusts; NOVA_SONIC_HALF_DUPLEX=1 mutes the mic "
                  f"during replies; headphones avoid echo entirely.")
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
        if ECHO_GATE_ENABLED and not HALF_DUPLEX:
            print(f"Echo gate stats: learned coupling k={self.gate.k:.3f} (mic RMS per output RMS), "
                  f"forwarded during playback {self.gate.forwarded_during_playback}, dropped {self.gate.dropped}.")
        await self.session.close()
