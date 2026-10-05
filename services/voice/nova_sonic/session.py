"""Bedrock bidirectional session for Amazon Nova 2 Sonic."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from typing import Any

from aws_sdk_bedrock_runtime.client import (
    BedrockRuntimeClient,
    InvokeModelWithBidirectionalStreamOperationInput,
)
from aws_sdk_bedrock_runtime.config import Config
from aws_sdk_bedrock_runtime.models import (
    BidirectionalInputPayloadPart,
    InvokeModelWithBidirectionalStreamInputChunk,
)

from nova_sonic import events
from nova_sonic.tools import TOOLS, handle_tool
from nova_sonic.config import (
    AWS_REGION,
    DEFAULT_SYSTEM_PROMPT,
    INTERRUPTED_MARKER,
    MODEL_ID,
)


def _load_shared_credentials() -> dict[str, str] | None:
    """Read SigV4 keys from ~/.aws/credentials for the active profile.

    The experimental Bedrock streaming SDK only reads credentials from
    environment variables, so we hydrate them from the shared file. This
    supports plain IAM-user profiles and any profile whose keys were written
    by tooling (including a session token for temporary/SSO credentials)."""
    import configparser
    from pathlib import Path

    path = Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE", Path.home() / ".aws" / "credentials"))
    if not path.is_file():
        return None
    profile = os.environ.get("AWS_PROFILE", "default")
    parser = configparser.ConfigParser()
    parser.read(path)
    if not parser.has_section(profile):
        return None
    access = parser.get(profile, "aws_access_key_id", fallback=None)
    secret = parser.get(profile, "aws_secret_access_key", fallback=None)
    if not access or not secret:
        return None
    creds = {"access_key_id": access, "secret_access_key": secret}
    token = parser.get(profile, "aws_session_token", fallback=None)
    if token:
        creds["session_token"] = token
    return creds


def _load_sso_credentials() -> dict[str, str] | None:
    """Resolve temporary credentials for an SSO / assumed-role profile.

    Requires botocore (bundled with the awscli / boto3). Returns None if
    botocore is unavailable or the profile cannot be resolved, so callers
    can fall back to other methods."""
    profile = os.environ.get("AWS_PROFILE")
    if not profile:
        return None
    try:
        from botocore.session import Session
    except ImportError:
        return None
    try:
        frozen = Session(profile=profile).get_credentials()
    except Exception:
        return None
    if not frozen:
        return None
    try:
        frozen = frozen.get_frozen_credentials()
    except Exception as exc:  # botocore TokenRetrievalError etc.: SSO session expired
        raise SystemExit(
            f"AWS session for profile '{profile}' is not valid ({type(exc).__name__}). "
            f"Run:  aws sso login --profile {profile}   and start again."
        ) from exc
    creds = {"access_key_id": frozen.access_key, "secret_access_key": frozen.secret_key}
    if frozen.token:
        creds["session_token"] = frozen.token
    return creds


def _credentials_resolver():
    from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver

    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        # Prefer static keys in the shared file; otherwise resolve SSO/role
        # profiles (which mint temporary credentials with a session token).
        shared = _load_shared_credentials() or _load_sso_credentials()
        if shared:
            os.environ["AWS_ACCESS_KEY_ID"] = shared["access_key_id"]
            os.environ["AWS_SECRET_ACCESS_KEY"] = shared["secret_access_key"]
            if shared.get("session_token"):
                os.environ["AWS_SESSION_TOKEN"] = shared["session_token"]
    return EnvironmentCredentialsResolver()


class NovaSonicSession:
    """Keeps a full-duplex Bedrock stream open and queues playback audio."""

    def __init__(
        self,
        model_id: str = MODEL_ID,
        region: str = AWS_REGION,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self.model_id = model_id
        self.region = region
        self.system_prompt = system_prompt
        self.prompt_name = str(uuid.uuid4())
        self.system_content_name = str(uuid.uuid4())
        self.audio_content_name = str(uuid.uuid4())

        self.client: BedrockRuntimeClient | None = None
        self.stream: Any = None
        self.is_active = False
        self.barge_in = False
        self.audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._response_task: asyncio.Task[None] | None = None
        self._role = ""
        self._show_assistant_text = False
        self.tools = TOOLS
        self.tool_calls: list[dict[str, Any]] = []
        self.transcripts: list[tuple[str, str]] = []
        self._tool_tasks: set[asyncio.Task[None]] = set()

    def _init_client(self) -> None:
        config = Config(
            endpoint_uri=f"https://bedrock-runtime.{self.region}.amazonaws.com",
            region=self.region,
            aws_credentials_identity_resolver=_credentials_resolver(),
        )
        self.client = BedrockRuntimeClient(config=config)

    async def send_event(self, payload: str) -> None:
        if not self.stream:
            return
        chunk = InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=payload.encode("utf-8"))
        )
        await self.stream.input_stream.send(chunk)

    async def start(self) -> None:
        if not self.client:
            self._init_client()
        assert self.client is not None

        self.stream = await self.client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=self.model_id)
        )
        self.is_active = True

        await self.send_event(events.session_start())
        await self.send_event(events.prompt_start(self.prompt_name, self.tools))
        await self.send_event(
            events.text_content_start(self.prompt_name, self.system_content_name, "SYSTEM")
        )
        await self.send_event(
            events.text_input(self.prompt_name, self.system_content_name, self.system_prompt)
        )
        await self.send_event(events.content_end(self.prompt_name, self.system_content_name))
        self._response_task = asyncio.create_task(self._process_responses())

    async def start_audio(self) -> None:
        await self.send_event(events.audio_content_start(self.prompt_name, self.audio_content_name))

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if not self.is_active or not pcm_bytes:
            return
        encoded = base64.b64encode(pcm_bytes).decode("utf-8")
        await self.send_event(events.audio_input(self.prompt_name, self.audio_content_name, encoded))

    def handle_barge_in(self) -> None:
        """Stop generation playback immediately. Nova already stopped on the server."""
        self.barge_in = True

    def drain_audio_queue(self) -> None:
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _process_responses(self) -> None:
        try:
            while self.is_active:
                output = await self.stream.await_output()
                result = await output[1].receive()
                if not (result.value and result.value.bytes_):
                    continue

                payload = json.loads(result.value.bytes_.decode("utf-8"))
                event = payload.get("event") or {}

                if "contentStart" in event:
                    start = event["contentStart"]
                    self._role = start.get("role", "")
                    extra = start.get("additionalModelFields")
                    self._show_assistant_text = False
                    if extra:
                        fields = json.loads(extra) if isinstance(extra, str) else extra
                        self._show_assistant_text = fields.get("generationStage") == "SPECULATIVE"

                elif "textOutput" in event:
                    text = event["textOutput"].get("content", "")
                    if INTERRUPTED_MARKER in text:
                        print("Barge-in: Sonic stopped speaking.")
                        self.handle_barge_in()
                        continue
                    if self._role == "ASSISTANT" and self._show_assistant_text:
                        print(f"Assistant: {text}")
                        self.transcripts.append(("ASSISTANT", text))
                    elif self._role == "USER":
                        print(f"User: {text}")
                        self.transcripts.append(("USER", text))

                elif "toolUse" in event:
                    use = event["toolUse"]
                    name, tool_use_id, args_json = use.get("toolName", ""), use.get("toolUseId", ""), use.get("content", "{}")
                    print(f"Tool: {name}({args_json})")
                    self.tool_calls.append({"name": name, "toolUseId": tool_use_id, "input": args_json})
                    task = asyncio.create_task(self._run_tool(name, tool_use_id, args_json))
                    self._tool_tasks.add(task)
                    task.add_done_callback(self._tool_tasks.discard)

                elif "contentEnd" in event:
                    if event["contentEnd"].get("stopReason") == "INTERRUPTED":
                        print("Barge-in: content interrupted.")
                        self.handle_barge_in()

                elif "audioOutput" in event:
                    audio_b64 = event["audioOutput"]["content"]
                    await self.audio_queue.put(base64.b64decode(audio_b64))

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Error reading Sonic stream: {exc}")

    async def _run_tool(self, name: str, tool_use_id: str, args_json: str) -> None:
        result_json = await handle_tool(name, args_json)
        self.tool_calls[-1]["output"] = result_json
        content_name = str(uuid.uuid4())
        for payload in events.tool_result_events(self.prompt_name, content_name, tool_use_id, result_json):
            await self.send_event(payload)

    async def close(self) -> None:
        if not self.stream:
            return
        self.is_active = False
        if self._response_task and not self._response_task.done():
            self._response_task.cancel()
            try:
                await self._response_task
            except asyncio.CancelledError:
                pass
        try:
            await self.send_event(events.content_end(self.prompt_name, self.audio_content_name))
            await self.send_event(events.prompt_end(self.prompt_name))
            await self.send_event(events.session_end())
            await self.stream.input_stream.close()
        except Exception:
            pass
        finally:
            self.is_active = False
            self.stream = None
