import argparse
import asyncio
import os

from openai_realtime.audio import DuplexAudio
from openai_realtime.config import MODEL_ID
from openai_realtime.session import OpenAIRealtimeSession


async def run() -> None:
    session = OpenAIRealtimeSession()
    audio = DuplexAudio(session)
    print(f"Opening Realtime WebSocket: {MODEL_ID}")
    await session.start()
    try:
        await audio.start()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        await audio.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="CareOneX OpenAI Realtime voice session")
    parser.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "Set an OpenAI API key first:\n"
            "  export OPENAI_API_KEY=sk-...\n"
            "Get one from https://platform.openai.com/api-keys"
        )
        raise SystemExit(1)
    asyncio.run(run())


if __name__ == "__main__":
    main()
