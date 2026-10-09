"""Amazon Nova 2 Sonic bidirectional streaming client."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nova_sonic.session import NovaSonicSession

__all__ = ["NovaSonicSession"]


def __getattr__(name: str):
    # Lazy: nova_sonic.session imports the Bedrock bidirectional-streaming
    # SDK, which submodules like nova_sonic.knowledge and nova_sonic.config
    # do not need. Importing it eagerly here used to force every test and
    # script that only touches knowledge.py/config.py to also have
    # aws_sdk_bedrock_runtime installed. Resolve it on first access instead,
    # so `import nova_sonic.knowledge` works with zero AWS dependencies.
    if name == "NovaSonicSession":
        from nova_sonic.session import NovaSonicSession

        return NovaSonicSession
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
