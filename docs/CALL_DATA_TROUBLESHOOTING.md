# Why is nothing saved after a voice call?

## What is supposed to be saved?

A general question-answer voice call does not produce a saved intake or transcript. Only a completed, explicitly confirmed *callback intake* writes a file. The agent must call `save_intake`, and that tool must return `saved=true`. Files remain on **your Windows computer**, never GitHub or the retrieval Docker container.

## Diagnose a real run

1. Start the existing voice client from the project root, using the `README.md` commands. It now prints the **absolute local intake directory** before the call.
2. For a synthetic test: say "I would like a coordinator to call me back." Give your details one question at a time, including care needs, NJ county, relationship, timing, your name, and a **fictional ten-digit test phone number**. Confirm the whole read-back number with a clear "Yes, that's correct."
3. Wait for `Intake saved: ...json` in the PowerShell window before pressing Enter. A message beginning `Tool: save_intake` is only an attempted save, not success.
4. End the session. Read `Intake status:`. `0 saved` means no confirmed successful save; a rejection reason is printed if available.
5. Run `Get-ChildItem .\services\voice\data\intakes -Filter *.json` from the project root. This directory is deliberately `.gitignore`d, and it may not exist until the first successful intake.

## Debug tool calls (synthetic data only)

In the **same PowerShell window** before running voice:

```powershell
$env:CAREONEX_EVAL_REPORT = (Join-Path (Get-Location) 'services/voice/data/synthetic-test-call.json')
```

Run the normal voice command; after stopping, open the JSON specified above. Under `tool_calls`, see whether `save_intake` was called and whether its `output` has `saved=true`. `saved=false` commonly means a missing intake question, incomplete number, incorrect readback, or no explicit yes. Unset with `Remove-Item Env:CAREONEX_EVAL_REPORT` when finished. **Do not keep transcripts of real callers without appropriate consent and data-handling controls.**

## Operational notes

- Default directory is anchored to `services/voice/data/intakes` instead of depending on the terminal's current working directory. Override with an **absolute** `CAREONEX_INTAKE_DIR` if needed.
- When the app stops, it waits briefly for tool calls already in progress; however, if you stop before the model calls `save_intake`, nothing will be persisted.
- Only JSON intake files are created; no automatic database writes, AWS S3 uploads, coordinator notifications, or GitHub uploads are implemented.
- The retrieval/RAG service is separate: `CAREONEX_RETRIEVE_URL` affects program lookups but does not itself save caller information.
