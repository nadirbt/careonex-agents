"""Offline checks for reliable intake persistence diagnostics (no real caller data)."""
import asyncio
import json
from pathlib import Path

from nova_sonic import tools
from nova_sonic.__main__ import report_intake_status
from nova_sonic.config import INTAKE_DIR, VOICE_ROOT
from nova_sonic.session import NovaSonicSession


def test_default_intake_folder_in_voice_project():
    # Unless configured via CAREONEX_INTAKE_DIR before import, the default
    # must be under the voice project, independent of shell working directory.
    assert Path(INTAKE_DIR).is_absolute()
    if not __import__("os").environ.get("CAREONEX_INTAKE_DIR") and not __import__("os").environ.get("CAREONEX_DATA_DIR"):
        assert Path(INTAKE_DIR) == VOICE_ROOT / "data" / "intakes"


def test_no_save_attempt_is_reported_as_zero(capsys):
    session = NovaSonicSession()
    summary = report_intake_status(session)
    assert summary["saved_count"] == 0
    assert "No save_intake tool call occurred" in capsys.readouterr().out


def test_save_attempt_does_not_count_as_success(capsys):
    session = NovaSonicSession()
    session.tool_calls.append({"name": "save_intake", "output": json.dumps({"saved": False, "confirmation_required": True})})
    summary = report_intake_status(session)
    assert summary["saved_count"] == 0
    assert "Phone confirmation required" in capsys.readouterr().out


def test_success_count_is_from_tool_output_not_intent(capsys):
    session = NovaSonicSession()
    session.tool_calls.extend([
        {"name": "save_intake", "output": json.dumps({"saved": False, "missing_field": "timeline"})},
        {"name": "save_intake", "output": json.dumps({"saved": True, "intake_id": "synthetic-1"})},
        {"name": "save_intake", "output": json.dumps({"saved": True, "already_saved": True})},
    ])
    summary = report_intake_status(session)
    assert summary["saved_count"] == 1
    assert "1 saved record(s)" in capsys.readouterr().out


def test_intake_write_uses_configured_directory_and_is_real_file(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "INTAKE_DIR", str(tmp_path / "intakes"))
    result = tools.save_intake_sync({"caller_name": "Synthetic Test", "callback_phone": "1234567890"})
    assert result["saved"] is True
    files = list((tmp_path / "intakes").glob("*.json"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["callback_phone"] == "1234567890"


def test_local_write_failure_reports_saved_false(monkeypatch, tmp_path):
    # This path is a file, not a writable directory.
    bad = tmp_path / "not-a-directory"
    bad.write_text("not a directory")
    monkeypatch.setattr(tools, "INTAKE_DIR", str(bad))
    result = tools.save_intake_sync({"callback_phone": "1234567890"})
    assert result["saved"] is False
    assert result["error"] == "local intake file write failed"


class _FakeInput:
    async def send(self, chunk):
        pass

    async def close(self):
        pass


class _FakeStream:
    input_stream = _FakeInput()


def test_session_close_waits_for_started_save_tool():
    async def scenario():
        session = NovaSonicSession()
        session.stream = _FakeStream()
        session.is_active = True
        observed = []

        async def pending_save():
            await asyncio.sleep(0.01)
            observed.append("finished")

        task = asyncio.create_task(pending_save())
        session._tool_tasks.add(task)
        task.add_done_callback(session._tool_tasks.discard)
        await session.close()
        return observed, session

    observed, session = asyncio.run(scenario())
    assert observed == ["finished"]
    assert session.stream is None
