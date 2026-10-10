# Push CareOneX to GitHub (Windows)

This repository preserves the live Nova 2 Sonic voice client, language switching, intake confirmation and validation, Docker RAG, the six services, and all existing evaluation tools/fixtures. It contains no saved caller intake JSON files and no Python virtual environments.

## Before pushing

1. Use **a fresh clone** of the target GitHub repository so the existing `.git` history is preserved. Do not commit a ZIP as a single file or copy `.git` from another project.
2. Create or switch to a feature branch (example: `feat/sonic_with_rag_updated`).
3. Copy the **contents** of this extracted `careonex-agents-rag` folder into the checked-out repository, keeping its `services/` and `evaluation/` structure. Do **not** copy or commit private `services/voice/data/intakes/` records.
4. Review what will be committed (`git status`, `git diff --stat`, and `git diff --cached --stat`). A `.gitignore` rule will not remove secrets or caller records previously tracked in Git history.
5. Commit and push only after reviewing the changes with your team.

Example using PowerShell **inside the cloned checkout** after copying the project files:

```powershell
git switch -c feat/sonic_with_rag_updated
git status
git add -A
git diff --cached --stat
git status
git commit -m "Prepare CareOneX voice, RAG, and evaluation for GitHub"
git push -u origin feat/sonic_with_rag_updated
```

If the branch already exists, use `git switch feat/sonic_with_rag_updated` rather than `git switch -c ...`.

## Local development (not GitHub-hosted)

- Sign in with AWS SSO locally (`aws sso login --profile careonex-team`). Never push `~/.aws`, `.env`, private keys, or personal data.
- Start the existing Knowledge Base retrieval service with `docker compose up --build retrieve` after configuring `HOME`, `AWS_PROFILE`, and `CAREONEX_KB_ID` as documented in `README.md`.
- Run live microphone mode using `uv run --python 3.12 --extra mic --directory services/voice python -c "import asyncio; from nova_sonic.__main__ import run; asyncio.run(run())"`.
- Saved intake JSON files remain local and git-ignored; no coordinator delivery/database integration is included.
- `make run` is **not** required for voice testing. It can ingest and change AWS Knowledge Base content, so don't run it against shared AWS resources without approval.

## Tests and evaluations

- `uv run --python 3.12 --directory services/retrieve pytest -q`
- `uv run --python 3.12 --directory services/voice pytest -q` (requires voice dependencies)
- `make test` (requires GNU Make and service development dependencies)
- See `docs/EVALUATION.md` for the full evaluation code, reference data and benchmark commands.

There is no automatic GitHub deployment: pushing code does not transfer AWS resources or saved caller records.
