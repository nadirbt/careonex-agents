"""Amazon Nova 2 Sonic bidirectional streaming client.

Tool-only validation should not require the optional streaming SDK.
"""

__all__ = ["NovaSonicSession"]


def __getattr__(name: str):
    if name == "NovaSonicSession":
        from nova_sonic.session import NovaSonicSession
        return NovaSonicSession
    raise AttributeError(name)
