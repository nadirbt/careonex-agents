"""JSON event builders for the OpenAI Realtime API (WebSocket transport).

Unlike nova_sonic/events.py (which returns ready-to-send JSON strings),
these return plain dicts -- session.py does the json.dumps(). That's a
deliberate, cosmetic difference: nothing here depends on the model.

Event names and shapes per the Realtime conversations guide (Oct 2026):
https://developers.openai.com/api/docs/guides/realtime-conversations
"""

from __future__ import annotations

from typing import Any


def session_update(
    *,
    model: str,
    instructions: str,
    voice: str,
    input_rate: int,
    output_rate: int,
    turn_detection_type: str | None,
) -> dict[str, Any]:
    """Configure the session: model, voice, audio formats, turn detection,
    and the system prompt (as `instructions`, Realtime's equivalent of
    Nova's SYSTEM content-start block)."""
    turn_detection: dict[str, Any] | None
    if turn_detection_type is None:
        # Disables VAD entirely -- caller must drive turns manually
        # (push-to-talk). See conversation_item.truncate / response_create.
        turn_detection = None
    else:
        turn_detection = {"type": turn_detection_type}

    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": model,
            "output_modalities": ["audio"],
            "instructions": instructions,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": input_rate},
                    "turn_detection": turn_detection,
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": output_rate},
                    "voice": voice,
                },
            },
        },
    }


def input_audio_buffer_append(b64_audio: str) -> dict[str, Any]:
    """Stream one chunk of base64 PCM16 mic audio to the model."""
    return {"type": "input_audio_buffer.append", "audio": b64_audio}


def response_create(output_modalities: tuple[str, ...] = ("audio",)) -> dict[str, Any]:
    """Ask the model to generate a response now. Only needed when VAD is
    disabled or auto-response is turned off; with semantic_vad/server_vad
    enabled (the default here), the server creates responses on its own."""
    return {
        "type": "response.create",
        "response": {"output_modalities": list(output_modalities)},
    }


def response_cancel() -> dict[str, Any]:
    """Cancel an in-progress model response -- used on barge-in before
    truncating, mirroring what Nova 2 Sonic's server does automatically."""
    return {"type": "response.cancel"}


def conversation_item_truncate(item_id: str, *, content_index: int, audio_end_ms: int) -> dict[str, Any]:
    """Tell the server to cut the model's last audio item at audio_end_ms
    and drop the unplayed transcript tail, after a barge-in. The WebSocket
    transport makes the client responsible for this bookkeeping -- WebRTC/
    SIP and Nova's bidirectional stream handle it server-side. See
    openai_realtime/README.md, "Interruption handling is manual here."""
    return {
        "type": "conversation.item.truncate",
        "item_id": item_id,
        "content_index": content_index,
        "audio_end_ms": audio_end_ms,
    }
