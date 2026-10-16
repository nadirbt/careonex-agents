# GitHub preparation report

Source: user-uploaded `careonex-agents-rag(8).zip` (October 10, 2026).

## Preservation

- **211 original project files retained** (including all voice, RAG, chunking, KB, evaluation, reference, Docker, and test files).
- Original executable Python application/test code, Dockerfiles, Makefile, Compose file, source catalog, and dependency lockfiles were preserved in the GitHub preparation version. This subsequent diagnostic patch changes **only voice-service files** for reliable local intake persistence and better save reporting, adds one voice regression test file, and updates documentation. The six-service RAG and evaluation implementation remains unchanged.
- All original evaluation questions, graded benchmarks, gold answers, fixtures, and text-to-text evaluation source code retained.
- `evaluation/retrieval-example.json` re-encoded losslessly from Windows CP1252 to UTF-8 to ensure standard JSON tooling and GitHub previews can read it. JSON fields and values are unchanged.
- `README.md` updated with a link to the GitHub setup guide; this report refreshed. Added `docs/GITHUB_PUSH.md`.

## Excluded from GitHub archive

- **3126** bundled `.venv` files (recreated from `pyproject.toml` and `uv.lock`).
- **8** compiled cache/test files.
- **1** saved caller intake record (private; leave on user's computer, not in a public repository).

No stored AWS credentials or private key material were found by the local pattern scan of the final included project. This scan is not a substitute for reviewing the staged Git diff before pushing.

## Running and validation

- Python syntax, project TOML, and JSON fixture validation performed.
- 90 retrieval pytest cases passed using available installed dependencies.
- 21 voice-tool/text-evaluation pytest cases passed. The full Nova Sonic suite needs the Amazon streaming SDK and other dependencies in its normal Python 3.12 environment; it was not run in full here.
- No AWS requests, Knowledge Base changes, or GitHub pushes were made as part of preparing this ZIP.

## Git safety note

If you have already committed private intake records or secrets into the **existing** Git history, `.gitignore` cannot erase that history. Review the repository and coordinate proper remediation before pushing.

## Follow-up: after-call data diagnostics

When the microphone client ends, it now shows the absolute local intake directory, count of confirmed successful saves, and an explanation when none occurred. Already-running tools have a short grace period before shutdown. A diagnostic JSON with synthetic call transcripts can be opted into through `CAREONEX_EVAL_REPORT`. The `services/voice/data/` directory continues to be ignored by Git.

For a complete troubleshooting procedure see `CALL_DATA_TROUBLESHOOTING.md`. The new tests passed with local SDK import stubs in an offline environment; no live Bedrock/microphone integration tests were performed during this patch.
