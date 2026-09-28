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
    "You are CareOneX, a warm and concise voice assistant. "
    "Answer in 1-2 short sentences first, then add at most a couple more if needed. "
    "Sound natural and conversational. Do not lecture."
)

# Server-side barge-in is signaled as this exact text payload.
INTERRUPTED_MARKER = '{ "interrupted" : true }'
