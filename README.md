# CareOneX — Voice Agent + RAG

Amazon Nova 2 Sonic voice assistant with a Bedrock Knowledge Base retrieval service, offline tests, and evaluation tools.

**GitHub-ready distribution.** All application and evaluation features from the uploaded project are retained. Only local environments, caches, and private intake records are omitted. This update also fixes local voice intake storage diagnostics and shutdown handling; RAG code is unchanged. See [GitHub push guide](docs/GITHUB_PUSH.md) and [preparation report](CLEANUP_REPORT.md).

## Repository map

| Path | Purpose |
| --- | --- |
| `services/voice/` | Live Nova 2 Sonic microphone client, conversation and intake tools, transcript/text evaluation |
| `services/retrieve/` | RAG FastAPI server, model-free feedback expansion, RRF, parent-context utilities |
| `services/data/` | Document source catalog and ingestion |
| `services/extract/` | PDF/HTML text extraction |
| `services/chunk/` | Chunking methods and experiments |
| `services/kb-sync/` | Bedrock Knowledge Base sync/ingestion |
| `evaluation/` | Benchmarks, reference answers, graded examples, test questions, and fixtures |
| `docker-compose.yml`, `Makefile` | Container-based services and task commands |
| `docs/EVALUATION.md` | How to run evaluation without changing RAG |
| `TEAM_SETUP.md` | AWS SSO setup guidance (no stored credentials) |
| `RAG_HANDOFF.md` | RAG operational notes; read before changing ingestion |

## Prerequisites

- Python **3.12** and [`uv`](https://docs.astral.sh/uv/)
- AWS CLI with the team's AWS SSO profile and required Bedrock permissions
- Docker Desktop for running the retrieval backend in containers
- A working microphone and speakers/headphones for live voice

Do not commit `.aws/`, environment secrets, saved caller information, or generated audio/transcripts. These are excluded from this ZIP and ignored by `.gitignore`.

## Run on Windows (PowerShell)

Open PowerShell in this repository's root folder. Log in to AWS:

```powershell
$env:AWS_PROFILE = "careonex-team"
$env:AWS_DEFAULT_REGION = "us-east-1"
$env:HOME = $HOME
aws sso login --profile careonex-team
```

**Window 1 — retrieval in Docker** (uses an existing Knowledge Base; does not reindex):

```powershell
$env:CAREONEX_KB_ID = "UYC7EK0ZDV"  # example staging KB; replace with your actual KB ID
$env:HOME = $HOME
docker compose up --build retrieve
```

**Window 2 — live interactive voice on Windows:**

```powershell
$env:AWS_PROFILE = "careonex-team"
$env:AWS_DEFAULT_REGION = "us-east-1"
$env:CAREONEX_RETRIEVE_URL = "http://127.0.0.1:8080"
$env:CAREONEX_VOICE_SEARCH_MODE = "feedback"
uv run --python 3.12 --extra mic --directory services/voice python -c "import asyncio; from nova_sonic.__main__ import run; asyncio.run(run())"
```

Speak normally after `Listening...`; press **Enter** to stop. Try `$env:CAREONEX_VOICE_SEARCH_MODE = "baseline"` to disable query expansion without code changes. Language switching can be attempted on request; actual coverage depends on Nova Sonic.

The intake tool saves JSON files **locally** under `services/voice/data/intakes/` (or an absolute path chosen with `CAREONEX_INTAKE_DIR`). The path and the number of successful records are printed after every call. An ordinary voice conversation does **not** create an intake automatically: the caller must agree to a callback, complete the brief intake, and explicitly confirm their ten-digit callback number. Look for `Intake saved: <absolute path>` in PowerShell. A `Tool: save_intake(...)` message alone does not mean saving succeeded.

Check saved records from the project root in PowerShell:

```powershell
Get-ChildItem .\services\voice\data\intakes -Filter *.json
```

**To record a synthetic test call's transcript and tool results for debugging (opt-in only):** before launching voice, set `$env:CAREONEX_EVAL_REPORT = (Join-Path (Get-Location) 'services/voice/data/synthetic-test-call.json')`. After the call ends, inspect that JSON's `tool_calls` for `save_intake`, its `output` (`saved: true` or `saved: false`), and `result_sent`. Remove/unset the variable afterward with `Remove-Item Env:CAREONEX_EVAL_REPORT`. Do not enable this for real callers without appropriate privacy/consent procedures. These records are ignored by Git. **Saving an intake is not a coordinator notification or a database integration.**

## Testing and evaluation

- Local voice tests: `uv run --python 3.12 --directory services/voice pytest -q`
- Local retrieval tests: `uv run --python 3.12 --directory services/retrieve pytest -q`
- All local tests: `make test` (requires GNU Make and project development dependencies)
- Retrieval, chunking, and transcript/text evaluation: see [`docs/EVALUATION.md`](docs/EVALUATION.md).

Some live AWS tests incur charges; ask the team before performing Knowledge Base ingestion or running `make run`.

## Docker / Make

| Command | Action |
| --- | --- |
| `docker compose up --build retrieve` | Run only the retrieval API on `localhost:8080` |
| `make serve` | Build and serve the retrieval API |
| `make smoke` | Run prerecorded Nova Sonic smoke test |
| `make test` | Run local offline tests |
| `make run` | **Rebuild/sync the data pipeline — do NOT use for routine voice testing** |

Note: the Docker voice service is intended for a prerecorded smoke test. On Windows, run the interactive microphone client directly with `uv` as shown above.

## Known limitations

- `amazon.nova-lite-v1:0` direct text-model calls require IAM permission your team profile may not have. Model-free `feedback` retrieval does not invoke it.
- Experiment results are not a substitute for human factual evaluation; grading files include provisional labels.
- Knowledge Base resources and IAM permissions live in AWS; cloning this repository does not recreate them.
- The baseline RAG implementation and the optional expansion/parent-context code are preserved exactly from the uploaded project.

Licensed under the existing [`LICENSE`](LICENSE).
