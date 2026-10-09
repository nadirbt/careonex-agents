"""OpenAI Realtime API client -- the GPT-Realtime analog of nova_sonic/."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai_realtime.session import OpenAIRealtimeSession

__all__ = ["OpenAIRealtimeSession"]


def __getattr__(name: str):
    # Lazy for the same reason as nova_sonic/__init__.py: don't force the
    # `websockets` dependency on anyone who only wants openai_realtime.config
    # (e.g. to confirm SYSTEM_PROMPT matches the Nova branch).
    if name == "OpenAIRealtimeSession":
        from openai_realtime.session import OpenAIRealtimeSession

        return OpenAIRealtimeSession
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
