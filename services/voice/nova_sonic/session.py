"""Bedrock bidirectional session for Amazon Nova 2 Sonic."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
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
from nova_sonic.tools import TOOLS, explicit_language_request, handle_tool, set_conversation_language_sync
from nova_sonic.intake_confirmation import CallbackConfirmationGate
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


def _relationship_stated_by_caller(value: str, transcripts: list[tuple[str, str]]) -> bool:
    """Do not store a role inferred from 'my mother' or 'my father'.

    A relationship is acceptable only when the caller identifies themselves
    explicitly, or answers a direct relationship question. The model's tool
    arguments alone are not evidence. Unknown/unrecognized answers are omitted.
    """
    relationship = str(value or "").strip().casefold()
    if not relationship or len(relationship) > 70:
        return False
    escaped = re.escape(relationship)
    # Do not allow a negation ('I'm not her daughter') to count as identification.
    self_identification = re.compile(
        rf"\b(?:i(?: am|'m|’m)|i'm|my relationship (?:is|to .* is))\s+"
        rf"(?!(?:not|never)\b)(?:(?:his|her|their|the|a|an)\s+)?{escaped}\b",
        re.I,
    )
    for i, (role, message) in enumerate(transcripts):
        if role != "USER":
            continue
        spoken = str(message).strip()
        if self_identification.search(spoken):
            return True
        # Single-word answers like 'daughter' are valid only after a direct
        # question. Do not accept a standalone mention in an unrelated turn.
        prior_assistant = ""
        for prior_role, prior_text in reversed(transcripts[:i]):
            if prior_role == "ASSISTANT":
                prior_assistant = str(prior_text)
                break
        if re.search(r"\b(?:your relationship|related to|who are you to|how are you related)\b", prior_assistant, re.I):
            if re.fullmatch(rf"\s*(?:(?:i(?: am|'m|’m)|as)\s+)?(?:(?:his|her|their|the|a|an)\s+)?{escaped}[.!, ]*", spoken, re.I):
                return True
            # A real caller answered 'son and mother' to the relationship
            # question. This describes the caller's role, not a model guess.
            if relationship in {"son", "daughter"} and re.fullmatch(
                rf"\s*(?:{escaped}\s+and\s+(?:my\s+)?(?:mom|mother)|"
                rf"(?:my\s+)?(?:mom|mother)\s+and\s+{escaped})[.!, ]*",
                spoken, re.I,
            ):
                return True
    return False


def _caller_name_stated_by_caller(value: str, transcripts: list[tuple[str, str]]) -> bool:
    """Require a caller-provided name, not a model-guessed tool argument."""
    name = " ".join(str(value or "").strip().split())
    if not name or len(name) > 100:
        return False
    escaped = re.escape(name).replace(r"\ ", r"\s+")
    introduction = re.compile(
        rf"\b(?:my name is|you can call me|please call me|call me|this is|"
        rf"i(?: am|'m|’m))\s+{escaped}(?![\w])", re.I,
    )
    name_question = re.compile(
        r"\b(?:your name|what name|may i (?:have|ask) your name|"
        r"who (?:am i|should i) (?:speaking|ask for)|name should)\b", re.I,
    )
    short_reply = re.compile(
        rf"^\s*(?:(?:my name is|it's|it is|call me|i(?: am|'m|’m))\s+)?"
        rf"{escaped}[.!, ]*$", re.I,
    )
    for i, (role, text) in enumerate(transcripts):
        if role != "USER":
            continue
        spoken = str(text).strip()
        if introduction.search(spoken):
            return True
        # A bare name is proof ONLY when it answers a name question.
        prior_assistant = next(
            (str(past_text) for past_role, past_text in reversed(transcripts[:i])
             if past_role == "ASSISTANT"), "",
        )
        if name_question.search(prior_assistant) and short_reply.fullmatch(spoken):
            return True
    return False


# A caller may choose not to provide these details, but Nova should first ask
# the question instead of silently omitting it from the intake.
_INTAKE_PROMPTS = (
    ("county", "Which New Jersey county does your family member need care in?"),
    ("relationship", "What is your relationship to the person who needs care?"),
    ("timeline", "When would you like care to start?"),
    ("caller_name", "What name should the coordinator ask for?"),
)
_NOT_SURE = re.compile(
    r"^(?:i(?:'m| am)? (?:not sure|don't know|do not know)|not sure|"
    r"(?:i'd|i would) rather not say|prefer not to say|unknown|skip(?: it)?|"
    r"(?:i )?can't say|(?:i )?cannot say)[.!\s]*$", re.I
)


def _caller_skipped_intake_field(field: str, transcripts: list[tuple[str, str]]) -> bool:
    """Accept a voluntary 'not sure' only as an answer to this field's question."""
    patterns = {
        "county": r"\bcounty\b|\bwhich town\b",
        "relationship": r"\b(?:relationship|related to|who are you to)\b",
        "timeline": r"\b(?:when.+(?:start|begin)|(?:care|service).+(?:start|begin)|start date)\b",
        "caller_name": r"\b(?:your name|what name|name should|ask for)\b",
    }
    for index, (role, spoken) in enumerate(transcripts):
        if role != "USER" or not _NOT_SURE.fullmatch(str(spoken).strip()):
            continue
        prior_assistant = ""
        for past_role, past_text in reversed(transcripts[:index]):
            if past_role == "ASSISTANT":
                prior_assistant = str(past_text)
                break
        if re.search(patterns[field], prior_assistant, re.I):
            return True
    return False


def _timeline_stated_by_caller(transcripts: list[tuple[str, str]]) -> bool:
    """Only treat a care start time as collected when the caller supplied it.

    The model can put a guessed ``timeline`` in tool arguments even if it never
    asked when care should begin. A real answer to the start-time question, or
    an explicit volunteered time preference, is required to advance intake.
    """
    time_words = re.compile(
        r"\b(?:asap|immediately|right away|as soon as possible|soon|today|tomorrow|"
        r"tonight|now|next (?:week|month|year)|this (?:week|month)|"
        r"within (?:a|an|the next|\d+|one|two|three|few) (?:days?|weeks?|months?)|"
        r"in (?:a|an|\d+|one|two|three|few) (?:days?|weeks?|months?)|"
        r"planning ahead|no rush|not urgent|later|whenever (?:you can|possible)|flexible|"
        r"after (?:discharge|the holidays)|next (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
        r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?: \d{1,2})?|"
        r"on the \d{1,2}(?:st|nd|rd|th)?|"
        r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b",
        re.I,
    )
    timing_question = re.compile(
        r"\b(?:when.+(?:start|begin)|(?:care|service).+(?:start|begin)|"
        r"start date|how soon.+(?:care|help)|when do you need (?:care|help))\b",
        re.I,
    )
    for index, (role, utterance) in enumerate(transcripts):
        if role != "USER":
            continue
        speech = str(utterance).strip()
        if not time_words.search(speech) or _NOT_SURE.fullmatch(speech):
            continue
        prior_assistant = ""
        for prior_role, prior_text in reversed(transcripts[:index]):
            if prior_role == "ASSISTANT":
                prior_assistant = str(prior_text)
                break
        # A standalone 'now' or 'tomorrow' is meaningful as an answer to the
        # timing question; unsolicited timing needs to concern the care request.
        if timing_question.search(prior_assistant) or re.search(
            r"\b(?:care|service|caregiver|help|start|begin|need|come|visit)\b", speech, re.I
        ) or re.search(r"\b(?:within|next|planning ahead|no rush|not urgent|asap)\b", speech, re.I):
            return True
    return False


def _intake_missing_question(record: dict, transcripts: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Do not let an early save_intake call silently skip intake questions."""
    for field, question in _INTAKE_PROMPTS:
        value = str(record.get(field) or "").strip().casefold()
        if field == "timeline" and value and not _timeline_stated_by_caller(transcripts):
            value = ""  # Model-supplied text is not proof the caller gave a start time.
        if field == "caller_name" and value and not _caller_name_stated_by_caller(value, transcripts):
            value = ""  # Never save a model-invented caller name.
        supplied = bool(value and value not in {
            "unknown", "not sure", "skip", "n/a", "not provided", "none",
        })
        if not supplied and not _caller_skipped_intake_field(field, transcripts):
            return field, question
    return None


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
        self.preferred_language = "English"  # per-call state, not a global shared across sessions
        self.callback_confirmation = CallbackConfirmationGate()
        # Carry already collected details across tool calls. Sonic may omit an
        # earlier answer when retrying a save after a missing-field prompt.
        self._intake_details: dict[str, str] = {}
        self._save_lock = asyncio.Lock()
        # One record per toolUse, in arrival order. Tool tasks run concurrently, so a record is found by
        # its toolUseId (never "the last one") when the output comes back.
        self.tool_calls: list[dict[str, Any]] = []
        self._tool_calls_by_id: dict[str, dict[str, Any]] = {}
        self.transcripts: list[tuple[str, str]] = []
        self._tool_tasks: set[asyncio.Task[None]] = set()
        self.last_audio_output_at: float | None = None  # time.monotonic() of the latest audioOutput event

    def _init_client(self) -> None:
        config = Config(
            endpoint_uri=f"https://bedrock-runtime.{self.region}.amazonaws.com",
            region=self.region,
            aws_credentials_identity_resolver=_credentials_resolver(),
        )
        self.client = BedrockRuntimeClient(config=config)

    async def send_event(self, payload: str) -> bool:
        """Send one event; False when the stream is already closed and nothing was sent."""
        if not self.stream:
            return False
        chunk = InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=payload.encode("utf-8"))
        )
        await self.stream.input_stream.send(chunk)
        return True

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
                    self._start_tool(event["toolUse"])

                elif "contentEnd" in event:
                    if event["contentEnd"].get("stopReason") == "INTERRUPTED":
                        print("Barge-in: content interrupted.")
                        self.handle_barge_in()

                elif "audioOutput" in event:
                    audio_b64 = event["audioOutput"]["content"]
                    self.last_audio_output_at = time.monotonic()
                    await self.audio_queue.put(base64.b64decode(audio_b64))

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Error reading Sonic stream: {exc}")

    def _start_tool(self, use: dict) -> asyncio.Task[None]:
        name, tool_use_id, args_json = use.get("toolName", ""), use.get("toolUseId", ""), use.get("content", "{}")
        print(f"Tool: {name}({args_json})")
        record = {"name": name, "toolUseId": tool_use_id, "input": args_json, "requested_at": time.monotonic(), "result_sent": False}
        self.tool_calls.append(record)
        self._tool_calls_by_id[tool_use_id] = record
        task = asyncio.create_task(self._run_tool(name, tool_use_id, args_json))
        self._tool_tasks.add(task)
        task.add_done_callback(self._tool_tasks.discard)
        return task

    def _latest_user_utterance(self) -> str:
        """Collect the user's latest transcript chunks, excluding earlier turns."""
        chunks = []
        for role, text in reversed(self.transcripts):
            if role == "ASSISTANT":
                break
            if role == "USER":
                chunks.append(text)
            if len(chunks) == 4:
                break
        return " ".join(reversed(chunks))

    def _latest_assistant_utterance(self) -> str:
        """Last assistant turn, used only to recognize short language-choice replies."""
        chunks = []
        for role, text in reversed(self.transcripts):
            if role == "USER" and chunks:
                break
            if role == "ASSISTANT":
                chunks.append(text)
            if len(chunks) >= 4:
                break
        return " ".join(reversed(chunks))

    def _remember_intake_details(self, args: dict[str, Any]) -> dict[str, str]:
        """Merge intake details across calls without accepting guessed facts."""
        from nova_sonic.tools import INTAKE_FIELDS

        for key in INTAKE_FIELDS:
            value = args.get(key)
            if value is None or not str(value).strip():
                continue
            value = str(value).strip()
            if value.casefold() in {
                "not sure", "unknown", "skip", "don't know", "i don't know",
                "prefer not to say", "not provided", "none", "n/a",
            }:
                self._intake_details.pop(key, None)
                continue
            if key == "relationship" and not _relationship_stated_by_caller(value, self.transcripts):
                continue
            if key == "timeline" and not _timeline_stated_by_caller(self.transcripts):
                continue
            if key == "caller_name" and not _caller_name_stated_by_caller(value, self.transcripts):
                continue
            self._intake_details[key] = value
        # Previously validated fields should survive later partial tool calls.
        return dict(self._intake_details)

    async def _run_tool(self, name: str, tool_use_id: str, args_json: str) -> None:
        record = self._tool_calls_by_id[tool_use_id]
        if name == "set_conversation_language":
            # Keep preference per call; do not use a module-global or reset the entire audio stream.
            try:
                args = json.loads(args_json)
            except (ValueError, TypeError):
                args = {}
            requested = args.get("language", "") if isinstance(args, dict) else ""
            user_speech = self._latest_user_utterance()
            if not explicit_language_request(user_speech, str(requested), self._latest_assistant_utterance()):
                result = {
                    "changed": False,
                    "error": "no explicit request to switch to that language in the recognized speech",
                    "guidance": (
                        "Do not switch languages based on a greeting or an uncertain transcript. "
                        "Continue in the current language, answer the caller's actual request briefly, "
                        "and ask which language they prefer only if necessary."
                    ),
                }
            else:
                result = set_conversation_language_sync(args)
            if result.get("changed"):
                self.preferred_language = result["language"]
                print(f"Conversation language: {self.preferred_language}")
            result_json = json.dumps(result, ensure_ascii=False)
        elif name == "save_intake":
            # The model may call this before the caller finishes dictating or before
            # hearing "yes" to a full phone readback. The session, not the model,
            # enforces readback + explicit affirmation and deduplicates saves.
            async with self._save_lock:
                try:
                    args = json.loads(args_json)
                except (TypeError, ValueError):
                    args = None
                if isinstance(args, dict):
                    args = self._remember_intake_details(args)
                missing = _intake_missing_question(args, self.transcripts) if isinstance(args, dict) else None
                if missing:
                    field, question = missing
                    print(f"Intake NOT saved: ask {field} before saving.")
                    result_json = json.dumps({
                        "saved": False, "missing_field": field,
                        "guidance": f"Before saving the intake, ask ONLY: '{question}' and wait for the caller's reply. "
                                    "If they do not know or prefer to skip, you may leave this field blank. "
                                    "Do not say the intake was saved.",
                    }, ensure_ascii=False)
                else:
                    approved, response = self.callback_confirmation.check(args, self.transcripts)
                    if response is not None:
                        if not response.get("already_saved"):
                            print("Intake NOT saved: phone readback/confirmation still required or invalid.")
                        result_json = json.dumps(response, ensure_ascii=False)
                    else:
                        result_json = await handle_tool(name, json.dumps(approved, ensure_ascii=False))
                        try:
                            save_result = json.loads(result_json)
                        except (TypeError, ValueError):
                            save_result = {}
                        if save_result.get("saved"):
                            self.callback_confirmation.mark_saved(approved, save_result)
        elif name == "intake_next_question":
            try:
                args = json.loads(args_json)
            except (TypeError, ValueError):
                args = None
            if isinstance(args, dict):
                # Prevent an unasked, model-invented timeline from causing the
                # guided intake tool to skip 'When would you like care to start?'
                args = {**args, **self._remember_intake_details(args)}
                if not _timeline_stated_by_caller(self.transcripts):
                    args.pop("timeline", None)
                if args.get("relationship") and not _relationship_stated_by_caller(args["relationship"], self.transcripts):
                    args.pop("relationship", None)
                if args.get("caller_name") and not _caller_name_stated_by_caller(args["caller_name"], self.transcripts):
                    args.pop("caller_name", None)
            result_json = await handle_tool(name, json.dumps(args, ensure_ascii=False)) if isinstance(args, dict) else await handle_tool(name, args_json)
        else:
            result_json = await handle_tool(name, args_json)
        if name != "set_conversation_language":
            # Reinforce the requested language on every later tool result, including RAG.
            # Knowledge passages can be in English; that does not change the spoken-language choice.
            if self.preferred_language != "English":
                try:
                    tool_data = json.loads(result_json)
                    if isinstance(tool_data, dict):
                        tool_data["conversation_language"] = self.preferred_language
                        tool_data["language_guidance"] = (
                            f"Reply to the caller ONLY in {self.preferred_language}, even if "
                            "the retrieved documents or intake question are written in English. "
                            "Keep official program names and factual requirements accurate."
                        )
                        result_json = json.dumps(tool_data, ensure_ascii=False)
                except (ValueError, TypeError):
                    pass
        record["output"] = result_json
        content_name = str(uuid.uuid4())
        try:
            sent = [await self.send_event(p) for p in events.tool_result_events(self.prompt_name, content_name, tool_use_id, result_json)]
        except Exception as exc:  # noqa: BLE001 - recorded for the report; the response loop carries on
            record["send_error"] = str(exc)
            print(f"Tool result for {name} not delivered: {exc}")
            return
        # Delivered only if all three events (contentStart, toolResult, contentEnd) went out on an open stream.
        record["result_sent"] = all(sent)
        if record["result_sent"]:
            record["result_sent_at"] = time.monotonic()

    async def close(self) -> None:
        if not self.stream:
            return
        # A caller may press Enter immediately after confirming their number.
        # Finish already-started tool calls before closing the stream, otherwise
        # a legitimate save can be cancelled without writing the intake JSON.
        if self._tool_tasks:
            try:
                _, pending = await asyncio.wait(self._tool_tasks.copy(), timeout=4.0)
                if pending:
                    print(f"Warning: {len(pending)} tool call(s) still pending at shutdown; allow the agent to finish before ending next time.")
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
            except Exception as exc:
                print(f"Warning: could not finish outstanding tool calls: {exc}")
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
