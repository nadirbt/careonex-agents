# CareOneX — Amazon Nova 2 Sonic

[https://github.com/aws-samples/amazon-nova-samples/tree/main/speech-to-speech/amazon-nova-2-sonic/sample-codes/console-python](https://github.com/aws-samples/amazon-nova-samples/tree/main/speech-to-speech/amazon-nova-2-sonic/sample-codes/console-python)

Real-time speech-to-speech over Bedrock `InvokeModelWithBidirectionalStream` (`amazon.nova-2-sonic-v1:0`).

The mic stays open while Sonic talks. Built-in server barge-in stops generation as soon as you speak; this client then drops queued playback so leftover audio does not keep talking over you.

## What you need

- Python 3.12 (managed by `uv`; the Bedrock SDK requires >= 3.12)
- Docker Desktop (every component runs as a container)
- AWS credentials with Amazon Bedrock access (SigV4 — **not** a Bedrock API key)
- An AWS account permitted to invoke **Nova 2 Sonic** in `us-east-1`
- Headset recommended: speaker echo can look like a barge-in to the model
- PortAudio (`brew install portaudio` on macOS)



## Layout

Every component is a container under `services/` with its own `Dockerfile` and uv `pyproject.toml`;
`docker-compose.yml` wires them and `make run` runs the data pipeline end to end:

| Service | Does |
| --- | --- |
| `data` | ensures the S3 bucket; fetches the source catalog into `raw/` with metadata sidecars |
| `extract` | raw PDF/HTML -> clean Markdown under `text/` |
| `chunk` | Markdown -> section-aware chunks under `chunks/`, one object per chunk |
| `kb-sync` | S3 Vectors index + Bedrock Knowledge Base over `chunks/`; runs ingestion |
| `retrieve` | HTTP API: question + filters -> cited passages (`:8080`) |
| `voice` | this Nova 2 Sonic client; the container runs a mic-free smoke test with tool use |

```bash
brew install portaudio awscli        # portaudio only for the laptop microphone client
aws sso login --profile careonex-team
export AWS_PROFILE=careonex-team AWS_DEFAULT_REGION=us-east-1
make build && make run               # pipeline
make serve                           # retrieval API
make smoke                           # voice round trip through the API
make test                            # offline tests for all services
```

## AWS credentials (local development)

1. In **IAM → Users**, create `careonex-local` without console access. Add an inline policy named `CareOneXNovaSonic`:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "bedrock:InvokeModel",
    "Resource": "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-sonic-v1:0"
  }]
}
```

1. Under that user's **Security credentials → Access keys**, create a key for **Command Line Interface (CLI)**. Use the IAM user's keys, not root keys.
2. Run `aws configure --profile careonex-personal`. Enter the new access key and secret key, region `us-east-1`, and output format `json`.

This saves a named profile in `~/.aws/credentials`, alongside any existing `default` profile. Keep keys out of the repository and chat.

Select the profile and verify it in the terminal you will use to run the app:

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN
export AWS_PROFILE=careonex-personal
export AWS_DEFAULT_REGION=us-east-1
aws sts get-caller-identity
```

Confirm the returned account ID is your intended account and the ARN ends in `:user/careonex-local`. `InvalidClientTokenId` means the credentials need fixing; the app's “Listening” message alone does not verify authentication.

Later, configure a separate `careonex-team` profile and switch `AWS_PROFILE`. Team SSO requires updating this app's credential loader; deployed services should use IAM roles rather than personal keys.

## Track test costs

Check **Billing → Credits** for available credits and expiry. After a short test, use **Cost Explorer**, filter to **Amazon Bedrock**, and view daily **Unblended cost**, excluding **Credit** and **Refund** charge types to see usage before offsets. Billing data is delayed; credits do not make usage inherently free.

## Run the voice client on a laptop

```bash
cd services/voice
uv sync --extra mic
export CAREONEX_RETRIEVE_URL=http://localhost:8080   # after `make serve`
uv run careonex-voice
```

Speak after the "Listening" line. Interrupt mid-reply to hear barge-in. Press Enter to close the session.
Ask about a program ("Does Medicaid pay for someone to come to the house?") and the model calls
`lookup_program_info`, which asks the retrieve service for cited passages.

Optional environment variables:

| Variable                 | Default                    | Purpose                                  |
| ------------------------ | -------------------------- | ---------------------------------------- |
| `NOVA_SONIC_MODEL_ID`    | `amazon.nova-2-sonic-v1:0` | Model ID                                 |
| `NOVA_SONIC_VOICE_ID`    | `matthew`                  | Output voice                             |
| `NOVA_SONIC_ENDPOINTING` | `MEDIUM`                   | `HIGH` / `MEDIUM` / `LOW` turn detection |
| `AWS_DEFAULT_REGION`     | `us-east-1`                | Bedrock region                           |
| `CAREONEX_RETRIEVE_URL`  | unset                      | retrieve service; unset = tool says "unavailable" |

## How barge-in works

1. Audio is streamed continuously as `audioInput` events (full duplex).
2. Sonic detects speech while it is generating and stops on the server.
3. It sends `{ "interrupted" : true }` on `textOutput`, and often `contentEnd.stopReason = INTERRUPTED`.
4. The client stops the speaker and drains the playback queue. Generation is faster than playback, so queued chunks would otherwise keep talking.

Sessions last up to about 8 minutes. Close with `contentEnd` → `promptEnd` → `sessionEnd`.

Official samples: [amazon-nova-samples / amazon-nova-2-sonic](https://github.com/aws-samples/amazon-nova-samples/tree/main/speech-to-speech/amazon-nova-2-sonic).