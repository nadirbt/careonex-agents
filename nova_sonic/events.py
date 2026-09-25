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


def text_content_start(prompt_name: str, content_name: str, role: str) -> str:
    return json.dumps(
        {
            "event": {
                "contentStart": {
                    "promptName": prompt_name,
                    "contentName": content_name,
                    "type": "TEXT",
                    "interactive": False,
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
