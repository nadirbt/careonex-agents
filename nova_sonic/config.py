import os

MODEL_ID = os.environ.get("NOVA_SONIC_MODEL_ID", "amazon.nova-2-sonic-v1:0")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
VOICE_ID = os.environ.get("NOVA_SONIC_VOICE_ID", "matthew")

# Nova 2 Sonic input is 16 kHz LPCM; output is 24 kHz LPCM.
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
CHUNK_FRAMES = 512

# HIGH / MEDIUM / LOW — how quickly the model treats a pause as end-of-turn.
ENDPOINTING_SENSITIVITY = os.environ.get("NOVA_SONIC_ENDPOINTING", "MEDIUM")

_BASE_PERSONA_PROMPT = (
    "You are CareOneX, a warm and concise voice assistant helping a family "
    "figure out home care for a loved one in New Jersey. "
    "Answer in 1-2 short sentences first, then add at most a couple more if needed. "
    "Sound natural and conversational. Do not lecture. "
    "Speak in plain, eighth-grade language, not policy jargon."
)

# Hard rules per the Milestone 1 SOW (Section 3, stage 3 "Dialog agent"):
# these are non-negotiable regardless of what the knowledge base says.
_HARD_RULES = (
    "Hard rules, never break these: "
    "1) Never diagnose a medical condition and never give medical or legal advice. "
    "2) Never promise or guarantee that someone is eligible for a program -- "
    "only a caseworker can confirm eligibility; say what the rules generally "
    "require and offer to connect them to start an application. "
    "3) Only state facts that are grounded in the program information below; "
    "if a question goes beyond it, say you will have someone follow up rather "
    "than guessing. Name the source (the program name) when you answer. "
    "4) If the caller sounds confused, distressed, or in crisis, offer to "
    "connect them with a person right away."
)


def _build_default_system_prompt() -> str:
    """Compose the persona, hard rules, and RAG grounding block.

    Grounding uses Markdown "program cards" (knowledge/program_cards/),
    not a vector store -- the corpus is small enough (8-10 programs, ~7k
    tokens rendered) to hand to the model directly. See knowledge/README.md
    for why. Falls back to the base persona if cards can't be loaded (e.g.
    running outside the repo checkout), so a missing knowledge/ directory
    never breaks the whole session.
    """
    try:
        from nova_sonic.knowledge import render_grounding_block

        grounding = render_grounding_block()
    except Exception:
        grounding = ""

    parts = [_BASE_PERSONA_PROMPT, _HARD_RULES]
    if grounding:
        parts.append(
            "Program information you can rely on when a family asks how care "
            "is paid for, who qualifies, or how to apply. Every entry below "
            "carries its own source; cite the program name, not the URL, when "
            "you speak:\n\n" + grounding
        )
    return "\n\n".join(parts)


DEFAULT_SYSTEM_PROMPT = os.environ.get(
    "NOVA_SONIC_SYSTEM_PROMPT", _build_default_system_prompt()
)

# Server-side barge-in is signaled as this exact text payload.
INTERRUPTED_MARKER = '{ "interrupted" : true }'
