"""Mic-free end-to-end check: stream a WAV question to Nova 2 Sonic, save the spoken reply.

Default question (fixtures/question.wav): "My mother lives in New Jersey. Does Medicaid pay for
someone to come to the house to help her?" which should make the model call lookup_program_info.

    careonex-voice-smoke                      # fixture question
    careonex-voice-smoke --wav my.wav         # any 16 kHz mono 16-bit WAV
    careonex-voice-smoke --question "..."     # synthesize with macOS `say` (host only)
    careonex-voice-smoke --expect-tool lookup_program_info   # also require a working tool round trip
Exit 0 when reply audio came back (and, with --expect-tool, the tool checks passed); 1 otherwise.
Writes <data-dir>/voice-smoke.json with transcripts, tool calls and check results."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

from nova_sonic.config import CHUNK_FRAMES, INPUT_SAMPLE_RATE, MODEL_ID, OUTPUT_SAMPLE_RATE, SAMPLE_WIDTH_BYTES, AWS_REGION
from nova_sonic.session import NovaSonicSession

CHUNK_BYTES = CHUNK_FRAMES * SAMPLE_WIDTH_BYTES
CHUNK_SECONDS = CHUNK_FRAMES / INPUT_SAMPLE_RATE
FIXTURE = Path(__file__).with_name("fixtures") / "question.wav"


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        if (w.getnchannels(), w.getframerate(), w.getsampwidth()) != (1, INPUT_SAMPLE_RATE, SAMPLE_WIDTH_BYTES):
            raise SystemExit(f"{path} must be 16 kHz mono 16-bit PCM")
        return w.readframes(w.getnframes())


def write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_WIDTH_BYTES)
        w.setframerate(OUTPUT_SAMPLE_RATE)
        w.writeframes(pcm)


def synthesize(text: str) -> Path:
    if not shutil.which("say"):
        raise SystemExit("--question needs macOS `say`; inside the container use --wav or the fixture")
    out = Path(tempfile.gettempdir()) / "nova_smoke_question.wav"
    subprocess.run(["say", "-o", str(out), "--file-format=WAVE", f"--data-format=LEI16@{INPUT_SAMPLE_RATE}", text], check=True)
    return out


def check_expected_tool(tool_calls: list[dict], name: str, last_audio_at: float | None = None) -> dict:
    """Evidence that `name` made a full round trip: called, its handler succeeded, and its result was sent
    back on the stream. A fluent answer alone is not evidence. For lookup_program_info, success means the
    retrieve service returned at least one passage. `reply_after_result` is reported but not required."""
    calls = [c for c in tool_calls if c.get("name") == name]
    report: dict = {"tool": name, "called": bool(calls), "calls": len(calls), "succeeded": False, "result_sent": False, "reply_after_result": None, "complete_round_trip": False, "failures": []}
    if not calls:
        report["failures"].append(f"{name} was never called (tools called: {[c.get('name') for c in tool_calls] or 'none'})")
        return report
    complete = False
    for c in calls:
        label = f"{name} {c.get('toolUseId')}"
        ok = False
        if "output" not in c:
            report["failures"].append(f"{label}: no output recorded (handler did not finish)")
        else:
            try:
                out = json.loads(c["output"])
            except json.JSONDecodeError:
                out = None
                report["failures"].append(f"{label}: output is not JSON")
            if out is not None:
                if out.get("error"):
                    report["failures"].append(f"{label}: tool reported an error: {out['error']}")
                elif name == "lookup_program_info" and not out.get("passages"):
                    report["failures"].append(f"{label}: retrieval returned no passages")
                else:
                    ok = True
                    report["succeeded"] = True
        if c.get("result_sent"):
            report["result_sent"] = True
            if last_audio_at is not None and c.get("result_sent_at") is not None:
                report["reply_after_result"] = bool(report["reply_after_result"]) or last_audio_at > c["result_sent_at"]
        else:
            report["failures"].append(f"{label}: result was not sent back to the session" + (f" ({c['send_error']})" if c.get("send_error") else ""))
        complete = complete or (ok and bool(c.get("result_sent")))
    report["complete_round_trip"] = complete
    if complete:
        report["failures"] = []  # one call succeeded and was delivered; an earlier failed attempt does not fail the run
    elif not report["failures"]:
        report["failures"].append(f"no single {name} call both succeeded and was delivered")
    return report


async def round_trip(pcm: bytes, timeout: float, idle: float) -> tuple[bytes, NovaSonicSession]:
    session = NovaSonicSession()
    print(f"Opening bidirectional stream: {MODEL_ID} in {AWS_REGION}")
    await session.start()
    await session.start_audio()
    chunks = [pcm[i:i + CHUNK_BYTES] for i in range(0, len(pcm), CHUNK_BYTES)]
    silence = b"\x00" * CHUNK_BYTES
    reply = bytearray()
    started = time.monotonic()
    last_audio: float | None = None
    idx = 0
    print(f"Streaming {len(pcm) / INPUT_SAMPLE_RATE / SAMPLE_WIDTH_BYTES:.1f}s of speech, then silence while waiting...")
    try:
        while True:
            if session._response_task and session._response_task.done():
                break
            if time.monotonic() - started > timeout:
                print(f"Timed out after {timeout:.0f}s.")
                break
            await session.send_audio(chunks[idx] if idx < len(chunks) else silence)
            idx += 1
            while not session.audio_queue.empty():
                reply.extend(session.audio_queue.get_nowait())
                last_audio = time.monotonic()
            # A tool call means a second reply is coming; keep listening while tools are in flight.
            if last_audio is not None and time.monotonic() - last_audio > idle and not session._tool_tasks:
                break
            await asyncio.sleep(CHUNK_SECONDS)
    finally:
        await session.close()
    return bytes(reply), session


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--wav", type=Path, help="16 kHz mono WAV to send (default: bundled fixture)")
    p.add_argument("--question", help="text to synthesize with macOS `say` instead of a WAV")
    p.add_argument("--data-dir", type=Path, default=Path(os.environ.get("CAREONEX_DATA_DIR", "data")))
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--idle", type=float, default=2.5, help="seconds of silence after the last audio that ends the test")
    p.add_argument("--play", action="store_true", help="afplay the reply (host only)")
    p.add_argument("--expect-tool", metavar="NAME", help="fail unless NAME was called, succeeded, and its result was sent back (e.g. lookup_program_info)")
    args = p.parse_args(argv)

    src = synthesize(args.question) if args.question else (args.wav or FIXTURE)
    pcm = read_pcm(src)
    print(f"Question audio: {src} ({len(pcm) / INPUT_SAMPLE_RATE / SAMPLE_WIDTH_BYTES:.1f}s)")

    reply, session = asyncio.run(round_trip(pcm, args.timeout, args.idle))
    tool_check = check_expected_tool(session.tool_calls, args.expect_tool, session.last_audio_output_at) if args.expect_tool else None
    report = {
        "model": MODEL_ID,
        "question_wav": str(src),
        "reply_seconds": round(len(reply) / OUTPUT_SAMPLE_RATE / SAMPLE_WIDTH_BYTES, 2),
        "transcripts": session.transcripts,
        "tool_calls": session.tool_calls,
        "retrieve_url": os.environ.get("CAREONEX_RETRIEVE_URL", ""),
        "expect_tool": tool_check,
    }
    args.data_dir.mkdir(parents=True, exist_ok=True)
    (args.data_dir / "voice-smoke.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    if not reply:
        print("FAIL: no audio came back from Nova 2 Sonic.")
        sys.exit(1)
    out = args.data_dir / "voice-smoke-reply.wav"
    write_wav(out, reply)
    if tool_check is not None:
        print(f"Tool check {args.expect_tool}: called={tool_check['called']} succeeded={tool_check['succeeded']} "
              f"result_sent={tool_check['result_sent']} reply_after_result={tool_check['reply_after_result']}")
        if not tool_check["complete_round_trip"]:
            for reason in tool_check["failures"]:
                print(f"  - {reason}")
            print(f"FAIL: reply audio came back, but no complete {args.expect_tool} round trip; report {args.data_dir / 'voice-smoke.json'}")
            sys.exit(1)
    print(f"PASS: {report['reply_seconds']}s of reply audio -> {out}; {len(session.tool_calls)} tool call(s); report {args.data_dir / 'voice-smoke.json'}")
    if args.play and shutil.which("afplay"):
        subprocess.run(["afplay", str(out)], check=False)


if __name__ == "__main__":
    main()
