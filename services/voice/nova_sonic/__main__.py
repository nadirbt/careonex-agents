import argparse
import asyncio
import os

from nova_sonic.audio import DuplexAudio
from nova_sonic.config import AWS_REGION, MODEL_ID
from nova_sonic.session import NovaSonicSession


async def run() -> None:
    session = NovaSonicSession()
    audio = DuplexAudio(session)
    print(f"Opening bidirectional stream: {MODEL_ID} in {AWS_REGION}")
    await session.start()
    try:
        await audio.start()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        await audio.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="CareOneX Nova 2 Sonic voice session")
    parser.parse_args()
    has_env = os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")
    has_file = os.path.exists(os.path.expanduser("~/.aws/credentials"))
    if not has_env and not has_file:
        print(
            "Set AWS credentials first (this API does not accept Bedrock API keys):\n"
            "  export AWS_ACCESS_KEY_ID=...\n"
            "  export AWS_SECRET_ACCESS_KEY=...\n"
            "  export AWS_DEFAULT_REGION=us-east-1\n"
            "or configure ~/.aws/credentials and optionally AWS_PROFILE."
        )
        raise SystemExit(1)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
