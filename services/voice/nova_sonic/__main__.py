import argparse
import asyncio
import os
import json
from pathlib import Path

from nova_sonic.audio import DuplexAudio
from nova_sonic.config import AWS_REGION, INTAKE_DIR, MODEL_ID
from nova_sonic.session import NovaSonicSession


def report_intake_status(session: NovaSonicSession) -> dict:
    """Operator-only summary; never misreport an attempted save as persistence."""
    saves = [call for call in session.tool_calls if call.get("name") == "save_intake"]
    successes = []
    failures = []
    for call in saves:
        if "output" not in call:
            failures.append("Save attempt did not finish before session ended")
            continue
        try:
            output = json.loads(call["output"])
        except (ValueError, TypeError):
            failures.append("Save tool returned an unreadable result")
            continue
        if output.get("saved") is True:
            if not output.get("already_saved"):
                successes.append(output.get("intake_id", "unknown"))
        else:
            failures.append(output.get("error") or output.get("missing_field") or
                            ("Phone confirmation required" if output.get("confirmation_required") else "Save was rejected"))
    print(f"\nIntake status: {len(successes)} saved record(s) this call.")
    print(f"Intake files directory: {Path(INTAKE_DIR).resolve()}")
    if not saves:
        print("No save_intake tool call occurred. Simply speaking or ending a call does not create an intake file; request a callback and confirm the callback phone number to save one.")
    elif not successes:
        print("No confirmed intake saved. The tool rejected or did not finish the save attempt.")
        if failures:
            print(f"Last save result: {failures[-1]}")
    return {"saved_count": len(successes), "attempts": len(saves), "failures": failures}


async def run(eval_report: Path | None = None) -> None:
    # Explicit opt-in for synthetic test calls. Live transcripts contain private
    # details and are not saved by default.
    if eval_report is None and os.environ.get("CAREONEX_EVAL_REPORT"):
        eval_report = Path(os.environ["CAREONEX_EVAL_REPORT"])
    session = NovaSonicSession()
    audio = DuplexAudio(session)
    print(f"Opening bidirectional stream: {MODEL_ID} in {AWS_REGION}")
    print(f"Intake records (only after confirmed callback): {Path(INTAKE_DIR).resolve()}")
    await session.start()
    try:
        await audio.start()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        await audio.stop()
        report_intake_status(session)
        # Explicit opt-in only. Transcripts can contain names and phone numbers;
        # never persist live-call text by default.
        if eval_report is not None:
            eval_report.parent.mkdir(parents=True, exist_ok=True)
            report = {"model": MODEL_ID, "transcripts": session.transcripts,
                      "tool_calls": session.tool_calls,
                      "retrieve_url": os.environ.get("CAREONEX_RETRIEVE_URL", "")}
            eval_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Evaluation transcript saved locally: {eval_report} (contains sensitive call data; use synthetic test calls only)")


def main() -> None:
    parser = argparse.ArgumentParser(description="CareOneX Nova 2 Sonic voice session")
    parser.add_argument("--eval-report", type=Path, help="Opt-in local transcript + tool log for synthetic test calls ONLY")
    args = parser.parse_args()
    has_env = os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")
    has_file = os.path.exists(os.path.expanduser("~/.aws/credentials"))
    if not has_env and not has_file:
        print(
            "Set AWS credentials first (this API does not accept Bedrock API keys):\n"
            "  export AWS_ACCESS_KEY_ID=...\n"
            "  export AWS_SECRET_ACCESS_KEY=...\n"
            "  export AWS_DEFAULT_REGION=us-east-1\n"
            "or configure ~/.aws/credentials and optionally AWS_PROFILE."
        )
        raise SystemExit(1)
    try:
        asyncio.run(run(args.eval_report))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
