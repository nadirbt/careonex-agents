# RAG handoff: `feat/sonic_with_rag` local fixes (2026-10-09, Windows test patch)

## What is in this ZIP

This is a **modified working copy of the feature branch** from the ZIP supplied by the user. It is not a GitHub commit and has not been deployed. The original ZIP and shared AWS resources remain unchanged.

Changes:

1. **DoAS Programs Side-by-Side 2026 PDF**: `services/extract/careonex_extract/doas_tables.py` uses PyMuPDF's ruled-table geometry to verify all six program headers on each of the four pages, then outputs separate Markdown sections per program with every financial/eligibility row labeled. It fails closed when the header structure is not reliable, rather than indexing a misleading table. Extractor version was incremented from 3 to 4 to force re-extraction.
2. **Defense against old ambiguous chunks**: newly verified comparison chunks carry `table_verified: true` from extract → chunk sidecar → Bedrock retrieval. Pre-fix chunks from the same file (which lack that marker) are excluded even if the existing KB still retrieves them. This does not delete any AWS objects.
3. **Voice tool safety**: source URLs are preserved in passage tool results for internal traceability; empty/malformed retrieval results cannot be treated as evidence. The model must admit that an answer couldn't be verified rather than guessing. Tool inputs of the wrong JSON shape are rejected.
4. **Intake**: only actual New Jersey counties count as known. `save_intake` rejects an invalid nonempty county, normalizes valid county names, and no longer promises a callback time just because JSON was written to local disk. Actual staff dispatch is not implemented.
5. **Filtering**: program aliases now match whole words/phrases (so e.g. `private pay` is not incorrectly filtered to VA benefits).
6. **Read-only evaluation**: `python -m careonex_retrieve.evaluate` runs repeat retrieval queries and reports evidence hit rate, source traceability, latency, and individual failures; it exits nonzero on any failure. These are retrieval checks, not a guarantee that spoken answers are correct.
7. **Docs**: team SSO guidance replaces outdated personal IAM key instructions.

## Windows extraction-test compatibility update (2026-10-09)

After the initial ZIP was shared, Windows PowerShell testing returned **2 extraction failures; retrieval (10) and voice (39) tests passed**. These were addressed in this revised ZIP:

- `services/extract/careonex_extract/convert.py`: The prior `NamedTemporaryFile` remained open when `pymupdf4llm` attempted to open it again. Windows blocks this; the converter now writes a PDF into `TemporaryDirectory`, closes its file handle, then invokes the Markdown converter. The temporary file is automatically removed afterward.
- `services/extract/careonex_extract/doas_tables.py`: On the user's PyMuPDF build, ruled-table extraction returned malformed cell strings such as `$2123 ,` and `20years`. The code now re-reads **the exact same verified cell rectangle** from the underlying PDF text; it uses the improved representation only when the content matches after ignoring whitespace and comma placement. It never accepts a reread that changes digits, decimals, operators, currency signs, or other punctuation.
- Added regression tests for these two issues. `EXTRACTOR_VERSION` increased from 4 to 5 to ensure future controlled runs do not reuse older extracted documents.

Test on Windows from the repository root with **explicit Python 3.12 selection**. Merely installing Python 3.12 does *not* change uv's automatic interpreter choice (the previous run used Python 3.14):

```powershell
uv python install 3.12
uv run --python 3.12 --directory services/extract pytest -q
uv run --python 3.12 --directory services/retrieve pytest -q
uv run --python 3.12 --directory services/voice pytest -q
```

This update has passed its targeted Linux extraction regressions (10, with the external-library integration test excluded) and the previous offline sanity suite (35). **The full Windows extraction suite needs to be rerun by a teammate.** The official government PDF and live AWS integrations are still unverified.

## Verified in this environment

- Python compilation succeeded for all source files.
- **35 offline checks passed** using the Python packages present in the environment; these included all new DoAS table, intake, retrieval-failure, provenance, alias, and evaluator tests as well as several existing voice tests.
- Extractor geometry tests used **generated four-page PDFs**. The actual official DoAS PDF is **not included in the uploaded ZIP**; this implementation has **not** been run against the actual PDF bytes, so its geometry still requires verification before publishing. The team-curated summary file remains available as a separately labeled source.
- The full service-specific test suites could not be run here, because dependencies including `moto`, `pymupdf4llm` and the experimental Nova Sonic SDK are absent and the environment has no package network access.
- No AWS SDK calls to the shared team account, no ingestion job, no real voice session, and no real RAG answer-quality evaluation were performed.

## Steps for a teammate who has the AWS SSO profile

Run these **from the repository root** in a terminal with Python 3.12, `uv`, and the pinned project dependencies. Commands below use Bash/WSL/Git Bash. In PowerShell, replace `export` with `$env:NAME="value"`.

```bash
aws sso login --profile careonex-team
export AWS_PROFILE=careonex-team
export AWS_DEFAULT_REGION=us-east-1
aws sts get-caller-identity

# All services' tests, using their pinned environments:
make test

# Before writing to AWS, inspect the live catalog PDF conversion locally:
#   download the official nj_doas_programs_side_by_side_2026.pdf to this machine
uv run --directory services/extract careonex-extract convert /path/to/nj_doas_programs_side_by_side_2026.pdf > /tmp/doas-verified.md
# Check that pages 1-2 have MLTSS/PACE, JACC, SRCP, AADSP, CHSP, OAA sections
# and pages 3-4 have PAAD, Senior Gold, MSPs, Lifeline, HAAAD/NJHAP, USF/LIHEAP.

# Preview extraction from the team's existing raw S3 object without writing:
uv run --directory services/extract careonex-extract run --only nj_doas_programs_side_by_side_2026.pdf --dry-run
```

**Stop if any table fails verification**, or if the dry run reports failure. Review the resulting Markdown against the actual PDF page images. Discuss any source-content ambiguity with the team; do not change program limits to make tests pass.

Only after **explicit team approval for AWS writes** and verification of required permissions:

```bash
# This modifies the shared team's S3 text and chunk data, and the Bedrock KB:
uv run --directory services/extract careonex-extract run --only nj_doas_programs_side_by_side_2026.pdf
uv run --directory services/chunk careonex-chunk run --only nj_doas_programs_side_by_side_2026.pdf
uv run --directory services/kb-sync careonex-kb sync
```

Then start the existing retrieve server and run repeat retrieval checks:

```bash
# Terminal 1 (keep running):
uv run --directory services/retrieve careonex-retrieve serve

# Terminal 2:
uv run --directory services/retrieve python -m careonex_retrieve.evaluate --repeats 3 --output data/rag-eval.json

# Optional end-to-end Nova Sonic voice smoke (costs money):
export CAREONEX_RETRIEVE_URL=http://127.0.0.1:8080
uv run --directory services/voice careonex-voice-smoke --expect-tool lookup_program_info --data-dir data/voice-smoke
```

Record the evaluation JSON and review at least three actual audio answers per question (Medicaid coverage, JACC income, PCA vs MLTSS, disallowed age, retrieval outage). Check **every** eligibility condition, age, program name, year, cited source, answer length, and callback claim. The prompt cannot guarantee no hallucinations. The spoken transcript and playback should be checked for stray formatting marks, too.

## Remaining prerequisites to claim a completed *live* RAG deployment

- Successful actual-PDF verification, controlled re-extraction/re-chunking/ingestion and index-health checks with the team-owned AWS account.
- Repeated live retrieval and speech-to-speech evaluations; answer faithfulness and safety need human review, not just a tool-success flag.
- Confirm the team account's S3 write and `bedrock:StartIngestionJob` permissions and budget. The Oct 7 notes verified read operations only.
- Connect local intake files to an actual operator workflow before promising any callback, and implement real consent/opt-out protections before outbound calling families.

Nothing in this ZIP authorizes making live calls to real customers or giving binding coverage/eligibility determinations.
