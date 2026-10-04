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

DEFAULT_SYSTEM_PROMPT = (
    "You are CareOneX, a warm, unhurried voice assistant that helps New Jersey families understand "
    "how home care is paid for: NJ FamilyCare / Medicaid (MLTSS, PCA, Personal Preference Program), "
    "JACC, Statewide Respite, Alzheimer's Adult Day Services, PACE, Medicare home health and VA benefits. "
    "Answer in one or two short sentences first, then add at most a couple more if needed. Use plain, "
    "eighth-grade language and sound conversational. "
    "For any question about a program, who qualifies, income or asset limits, costs, or how to apply, "
    "call the lookup_program_info tool and answer only from what it returns, naming the source document. "
    "If the tool returns nothing useful, say a person from CareOneX will follow up. "
    "Never give medical or legal advice. Never say someone qualifies; you may say they may qualify and "
    "explain who decides (the county social services agency, the ADRC at 1-877-222-3737, or their health plan)."
)

# Where the retrieve service lives; unset means the tool answers "knowledge base unavailable".
RETRIEVE_URL = os.environ.get("CAREONEX_RETRIEVE_URL", "").rstrip("/")
RETRIEVE_TIMEOUT_S = float(os.environ.get("CAREONEX_RETRIEVE_TIMEOUT_S", "4"))

# Echo handling for laptop speakers. While the assistant is playing, mic chunks are forwarded only
# if their RMS level exceeds ECHO_GATE_RMS (int16 scale, 0 disables the gate). Speech into the mic is
# usually several times louder than speaker bleed. HALF_DUPLEX mutes the mic entirely during playback
# (no barge-in). Headphones make both unnecessary.
ECHO_GATE_RMS = int(os.environ.get("NOVA_SONIC_ECHO_GATE", "1500"))
HALF_DUPLEX = os.environ.get("NOVA_SONIC_HALF_DUPLEX", "").lower() in ("1", "true", "yes")
PLAYBACK_TAIL_S = 0.35  # treat the mic as "during playback" for this long after the last speaker write

# Server-side barge-in is signaled as this exact text payload.
INTERRUPTED_MARKER = '{ "interrupted" : true }'
