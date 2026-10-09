"""JSON event builders for InvokeModelWithBidirectionalStream."""

from __future__ import annotations

import json

from nova_sonic.config import (
    ENDPOINTING_SENSITIVITY,
    OUTPUT_SAMPLE_RATE,
    VOICE_ID,
)


def session_start() -> str:
    return json.dumps(
        {
            "event": {
                "sessionStart": {
                    "inferenceConfiguration": {
                        "maxTokens": 1024,
                        "topP": 0.9,
                        "temperature": 0.7,
                    },
                    "turnDetectionConfiguration": {
                        "endpointingSensitivity": ENDPOINTING_SENSITIVITY,
                    },
                }
            }
        }
    )


def prompt_start(prompt_name: str) -> str:
    return json.dumps(
        {
            "event": {
                "promptStart": {
                    "promptName": prompt_name,
                    "textOutputConfiguration": {"mediaType": "text/plain"},
                    "audioOutputConfiguration": {
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": OUTPUT_SAMPLE_RATE,
                        "sampleSizeBits": 16,
                        "channelCount": 1,
                        "voiceId": VOICE_ID,
                        "encoding": "base64",
                        "audioType": "SPEECH",
                    },
                }
            }
        }
    )


def text_content_start(
    prompt_name: str, content_name: str, role: str, *, interactive: bool = False
) -> str:
    """interactive=False is right for the SYSTEM prompt and conversation
    history (the original use of this function). interactive=True is what
    AWS calls "cross-modal input" -- a TEXT turn sent mid-session while an
    AUDIO content stream is open, e.g. nova_sonic/smoke_test.py's text-only
    USER turns. See docs.aws.amazon.com/nova/latest/nova2-userguide/
    sonic-cross-modal.html."""
    return json.dumps(
        {
            "event": {
                "contentStart": {
                    "promptName": prompt_name,
                    "contentName": content_name,
                    "type": "TEXT",
                    "interactive": interactive,
                    "role": role,
                    "textInputConfiguration": {"mediaType": "text/plain"},
                }
            }
        }
    )


def text_input(prompt_name: str, content_name: str, content: str) -> str:
    return json.dumps(
        {
            "event": {
                "textInput": {
                    "promptName": prompt_name,
                    "contentName": content_name,
                    "content": content,
                }
            }
        }
    )


def audio_content_start(prompt_name: str, content_name: str) -> str:
    return json.dumps(
        {
            "event": {
                "contentStart": {
                    "promptName": prompt_name,
                    "contentName": content_name,
                    "type": "AUDIO",
                    "interactive": True,
                    "role": "USER",
                    "audioInputConfiguration": {
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": 16000,
                        "sampleSizeBits": 16,
                        "channelCount": 1,
                        "audioType": "SPEECH",
                        "encoding": "base64",
                    },
                }
            }
        }
    )


def audio_input(prompt_name: str, content_name: str, b64_audio: str) -> str:
    return json.dumps(
        {
            "event": {
                "audioInput": {
                    "promptName": prompt_name,
                    "contentName": content_name,
                    "content": b64_audio,
                }
            }
        }
    )


def content_end(prompt_name: str, content_name: str) -> str:
    return json.dumps(
        {
            "event": {
                "contentEnd": {
                    "promptName": prompt_name,
                    "contentName": content_name,
                }
            }
        }
    )


def prompt_end(prompt_name: str) -> str:
    return json.dumps({"event": {"promptEnd": {"promptName": prompt_name}}})


def session_end() -> str:
    return json.dumps({"event": {"sessionEnd": {}}})
