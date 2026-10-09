"""Text-in, audio-out smoke test for Nova 2 Sonic -- no microphone needed.

Nova 2 Sonic's "cross-modal input" (send TEXT, still get an audio reply)
requires an active AUDIO content stream to be open at the same time --
see docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-cross-modal.html.
This script opens that stream and feeds it silence instead of a real mic,
so you can test the model/prompt without any audio hardware.

The reply comes back as spoken audio, not text, so this script saves it to
a .wav file you can actually play -- that's your "proof of life" for this
branch, the Nova-side equivalent of openai_realtime/smoke_test.py printing
text to the terminal.

Usage:
    export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_DEFAULT_REGION=us-east-1
    # (or have ~/.aws/credentials / AWS_PROFILE set up -- see TEAM_SETUP.md)
    python -m nova_sonic.smoke_test "Do I qualify for NJ MLTSS?"
"""

from __future__ import annotations

import asyncio
import sys
import uuid
import wave

from nova_sonic import events
from nova_sonic.config import CHANNELS, CHUNK_FRAMES, INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE, SAMPLE_WIDTH_BYTES
from nova_sonic.session import NovaSonicSession

SILENCE_CHUNK = b"\x00" * (CHUNK_FRAMES * CHANNELS * SAMPLE_WIDTH_BYTES)
OUTPUT_WAV_PATH = "nova_sonic_smoke_test_reply.wav"


async def ask(question: str, *, listen_seconds: float = 20.0) -> None:
    session = NovaSonicSession()
    await session.start()
    await session.start_audio()  # opens the AUDIO content stream cross-modal needs

    # Keep the stream "alive" with silence while we send the text turn and
    # wait for a reply -- a real mic would normally be doing this job.
    async def _feed_silence() -> None:
        while session.is_active:
            await session.send_audio(SILENCE_CHUNK)
            await asyncio.sleep(CHUNK_FRAMES / INPUT_SAMPLE_RATE)

    silence_task = asyncio.create_task(_feed_silence())

    text_content_name = str(uuid.uuid4())
    print(f"You: {question}")
    await session.send_event(
        events.text_content_start(session.prompt_name, text_content_name, "USER", interactive=True)
    )
    await session.send_event(events.text_input(session.prompt_name, text_content_name, question))
    await session.send_event(events.content_end(session.prompt_name, text_content_name))

    print("CareOneX (Nova 2 Sonic) is responding -- listen for printed text, "
          f"and check {OUTPUT_WAV_PATH} afterward for the spoken audio.")
    await asyncio.sleep(listen_seconds)

    silence_task.cancel()
    try:
        await silence_task
    except asyncio.CancelledError:
        pass

    audio_bytes = bytearray()
    while not session.audio_queue.empty():
        audio_bytes.extend(session.audio_queue.get_nowait())

    await session.close()

    if audio_bytes:
        with wave.open(OUTPUT_WAV_PATH, "wb") as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(OUTPUT_SAMPLE_RATE)
            wav_file.writeframes(bytes(audio_bytes))
        print(f"\nSaved spoken reply to {OUTPUT_WAV_PATH} ({len(audio_bytes)} bytes). "
              f"Open it with any audio player to hear the answer.")
    else:
        print(
            "\nNo audio came back in the listen window -- try increasing "
            "listen_seconds, or check the printed text above for errors."
        )


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python -m nova_sonic.smoke_test "your question"')
        raise SystemExit(1)
    asyncio.run(ask(" ".join(sys.argv[1:])))


if __name__ == "__main__":
    main()
