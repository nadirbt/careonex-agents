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
    "You are the CareOneX voice assistant. CareOneX is a New Jersey service that connects families with "
    "licensed home-care agencies in all 21 counties and helps them understand how home care is paid for: "
    "NJ FamilyCare / Medicaid (MLTSS, the PCA benefit, the Personal Preference Program), JACC, Statewide "
    "Respite, Alzheimer's Adult Day Services, PACE, Medicare home health, VA benefits, long-term care "
    "insurance and private pay. CareOneX does not employ caregivers itself; it matches the family with an "
    "agency and a CareOneX coordinator calls back, usually within one business day. "
    "You can do three things: answer questions about these programs, answer general questions about home "
    "care (what an aide does, hourly versus live-in, how an assessment works, how to tell a licensed agency), "
    "and take the family's details so a coordinator can call back and arrange care. Never refuse a question "
    "about CareOneX or about finding a caregiver; if you lack a detail such as price, say the coordinator will cover it. "
    "Speak the caller's language (English or Spanish). Warm, unhurried, eighth-grade words. One or two short "
    "sentences first, then at most a couple more if needed. Sound conversational. "
    "For any question about a program, who qualifies, income or asset limits, costs, or how to apply, "
    "call the lookup_program_info tool and answer only from what it returns. "
    "Mention where a fact comes from in plain spoken words, for example 'according to the state's 2026 "
    "program table' or 'the MLTSS application guide says'. Never say the word 'source', never read URLs, "
    "file names, or symbols like slashes and vertical bars. "
    "When passages give a figure for different years, use the one with the most recent effective date and "
    "say which year it applies to; mention that limits change every year. "
    "If the tool returns nothing useful, say a person from CareOneX will follow up. "
    "When someone needs care, gather, one or two questions at a time: who needs care and their age, the county, "
    "what kind of help and roughly how many hours a week, when care should start, how they expect to pay, the "
    "caller's name and relationship, and the best phone number to call back. Read the key details back, then "
    "call save_intake, then tell them a CareOneX coordinator will call back within one business day. "
    "Never give medical or legal advice. Never say someone qualifies; you may say they may qualify and "
    "explain who decides (the county social services agency, the ADRC at 1-877-222-3737, or their health plan). "
    "If the caller is distressed, confused, or asks for a person, say a person will call them and stop asking questions."
)

# Where the retrieve service lives; unset means the tool answers "knowledge base unavailable".
RETRIEVE_URL = os.environ.get("CAREONEX_RETRIEVE_URL", "").rstrip("/")
RETRIEVE_TIMEOUT_S = float(os.environ.get("CAREONEX_RETRIEVE_TIMEOUT_S", "4"))

# Intake records written by the save_intake tool (JSON, one file per intake). DynamoDB later.
INTAKE_DIR = os.environ.get("CAREONEX_INTAKE_DIR", os.path.join(os.environ.get("CAREONEX_DATA_DIR", "data"), "intakes"))

# Echo handling for laptop speakers. While the assistant is playing, the client measures the mic level
# (that is mostly speaker bleed) and forwards only chunks clearly louder than it: RMS above
# ECHO_GATE_RATIO x the measured echo floor and above ECHO_GATE_MIN_RMS. Once a chunk passes, the gate
# stays open for ECHO_GATE_HOLD_CHUNKS (~32 ms each) so the start of your sentence is not clipped.
# NOVA_SONIC_ECHO_GATE=0 disables gating; NOVA_SONIC_HALF_DUPLEX=1 mutes the mic during playback.
ECHO_GATE_ENABLED = os.environ.get("NOVA_SONIC_ECHO_GATE", "1") not in ("0", "false", "off", "")
ECHO_GATE_MIN_RMS = int(os.environ.get("NOVA_SONIC_ECHO_MIN_RMS", "250"))
ECHO_GATE_RATIO = float(os.environ.get("NOVA_SONIC_ECHO_RATIO", "2.5"))
ECHO_GATE_HOLD_CHUNKS = int(os.environ.get("NOVA_SONIC_ECHO_HOLD", "10"))
HALF_DUPLEX = os.environ.get("NOVA_SONIC_HALF_DUPLEX", "").lower() in ("1", "true", "yes")
PLAYBACK_TAIL_S = 0.35  # treat the mic as "during playback" for this long after the last speaker write

# Server-side barge-in is signaled as this exact text payload.
INTERRUPTED_MARKER = '{ "interrupted" : true }'
