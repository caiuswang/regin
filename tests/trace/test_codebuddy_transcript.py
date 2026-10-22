"""CodeBuddy-family transcript import: parser → posters → session_spans / turn_usage."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lib.orm.engine import get_connection

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "codebuddy"
DESKTOP_SID = "5f0c1a2e-7d3b-4c8e-9a61-2b4d8e0f1c37"
LEGACY_SID = "d140c917-8d5a-4fc2-8f13-e722c0975e08"
UNPAIRED_SID = "0cc2531d-ac9a-4c81-869b-4528d42b1f86"
_FILES = {DESKTOP_SID: "workbuddy_desktop.jsonl",
          LEGACY_SID: "legacy_headless.jsonl",
          UNPAIRED_SID: "unpaired_call.jsonl"}


@pytest.fixture
def workbuddy_home(tmp_path, monkeypatch):
    from lib.settings import settings
    root = tmp_path / ".workbuddy"
    monkeypatch.setattr(settings, "workbuddy_config_dir", root)
    monkeypatch.setenv("REGIN_TURN_TRACE_STATE_DIR", str(tmp_path / "seen"))
    project = root / "projects" / "Users-dev-WorkBuddy-demo"
    project.mkdir(parents=True)
    (project / DESKTOP_SID / "tool-results").mkdir(parents=True)
    (project / DESKTOP_SID / "tool-results" / "call_00-overflow.txt").write_text("never read")
    for sid, name in _FILES.items():
        shutil.copyfile(FIXTURES / name, project / f"{sid}.jsonl")
    return project


def _import(session: str) -> None:
    from cli.app import app
    result = CliRunner().invoke(
        app, ["trace", "import-transcripts", "--provider", "workbuddy", "--session", session])
    assert result.exit_code == 0, result.output


def _spans(trace_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT span_id, name, status_code, attributes FROM session_spans "
            "WHERE trace_id = ? ORDER BY start_time", (trace_id,)).fetchall()
    finally:
        conn.close()
    return [{**dict(r), "attributes": json.loads(r["attributes"] or "{}")} for r in rows]


def _one(sql: str, *args):
    conn = get_connection()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def _counts(trace_id: str) -> tuple[int, int]:
    return (_one("SELECT COUNT(*) FROM session_spans WHERE trace_id = ?", trace_id)[0],
            _one("SELECT COUNT(*) FROM turn_usage WHERE trace_id = ?", trace_id)[0])


def _tool_spans(trace_id: str) -> dict[str, dict]:
    return {s["name"]: s for s in _spans(trace_id) if s["name"].startswith("tool.")}


def _usage_totals(trace_id: str):
    return _one("SELECT COUNT(*) n, SUM(input_tokens) i, SUM(cache_read_tokens) c, "
                "SUM(output_tokens) o, SUM(reasoning_tokens) r FROM turn_usage "
                "WHERE trace_id = ?", trace_id)


def test_workbuddy_transcript_import_builds_tool_spans(workbuddy_home):
    _import(DESKTOP_SID)

    session = _one("SELECT agent_type, title FROM sessions WHERE trace_id = ?", DESKTOP_SID)
    assert (session["agent_type"], session["title"]) == ("workbuddy", "修复登录流程")

    tools = _tool_spans(DESKTOP_SID)
    assert sorted(tools) == ["tool.Bash", "tool.Read"]
    assert {s["status_code"] for s in tools.values()} == {"OK"}
    bash = tools["tool.Bash"]["attributes"]
    assert (bash["tool_use_id"], bash["command"], bash["stdout"]) == (
        "call_01_9QwErTyUiOpAsDfGhJkL0001", "pytest -q tests/test_login.py",
        "1 failed, 3 passed")
    names = [s["name"] for s in _spans(DESKTOP_SID)]
    assert (names.count("assistant.thinking"), names.count("assistant_response")) == (1, 1)

    # Two inferences (two messageIds); the parallel Bash call shares the
    # first one's usage, which is counted once.
    usage = _usage_totals(DESKTOP_SID)
    assert (usage["n"], usage["i"] + usage["c"], usage["c"], usage["o"], usage["r"]) == (
        2, 35064 + 36000, 8192 + 35000, 130 + 200, 51)


def test_legacy_transcript_without_startsnewuserrequest_still_splits_turns(workbuddy_home):
    _import(LEGACY_SID)

    prompts = [s for s in _spans(LEGACY_SID) if s["name"] == "prompt"]
    assert [p["attributes"]["text"] for p in prompts] == ["总结今天的 AI 新闻", "再详细说说第一条"]
    # No conversationRequestId: the turn is keyed on the opening user message id.
    assert [p["attributes"]["prompt_id"] for p in prompts] == [
        "996ba833-50eb-4b32-99ae-f3d21487e725", "7c2b1d0e-4f5a-4b6c-8d7e-9f0a1b2c3d4e"]
    search = next(s for s in _spans(LEGACY_SID) if s["name"] == "tool.WebSearch")
    assert search["attributes"]["source_prompt_id"] == "996ba833-50eb-4b32-99ae-f3d21487e725"


def test_desktop_prompt_is_unwrapped(workbuddy_home):
    _import(DESKTOP_SID)

    (prompt,) = [s for s in _spans(DESKTOP_SID) if s["name"] == "prompt"]
    assert prompt["attributes"]["text"] == "修一下登录"
    assert prompt["attributes"]["prompt_id"] == "01a162354b9170d4b2dccf43edeed23e"


def test_transcript_import_is_idempotent(workbuddy_home, tmp_path):
    _import(DESKTOP_SID)
    first_ids = {s["span_id"] for s in _spans(DESKTOP_SID)}
    first = _counts(DESKTOP_SID)
    # Drop the client-side seen cache so the second import really re-posts
    # every row and the store's own id dedup is what is under test.
    shutil.rmtree(tmp_path / "seen")

    _import(DESKTOP_SID)

    assert _counts(DESKTOP_SID) == first
    assert {s["span_id"] for s in _spans(DESKTOP_SID)} == first_ids


def test_span_ids_depend_only_on_session_and_entry_id(tmp_path):
    from lib.trace.codebuddy_transcript import entry_key, read_usage_codebuddy

    usage = read_usage_codebuddy(str(FIXTURES / "workbuddy_desktop.jsonl"))
    read_call = next(s for s in usage.tool_spans if s["name"] == "tool.Read")
    assert read_call["span_id"] == "trtool-" + entry_key(
        DESKTOP_SID, "01a16235-7104-7d14-b6c3-3f37059517c7")
    # Same-millisecond UUIDv7 ids share their first 13 chars; the keys must not.
    assert len({s["span_id"] for s in usage.tool_spans}) == 2


def _append_bash_failure(project: Path) -> None:
    with (project / f"{UNPAIRED_SID}.jsonl").open("a") as fh:
        fh.write(json.dumps({
            "id": "a0000000-0000-7000-8000-000000000003", "timestamp": 1792649030000,
            "type": "function_call_result", "name": "Bash",
            "callId": "call_00_inflightBuild0001", "status": "error",
            "output": {"type": "text", "text": "build failed"},
            "sessionId": UNPAIRED_SID}) + "\n")


def _raw_rows(trace_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM session_spans WHERE trace_id = ?", (trace_id,))]
    finally:
        conn.close()
    return [{**r, "attributes": json.loads(r["attributes"] or "{}")} for r in rows]


def test_unpaired_function_call_stays_pending(workbuddy_home):
    from lib.trace.merge import merge_spans

    _import(UNPAIRED_SID)

    build = _tool_spans(UNPAIRED_SID)["tool.Bash"]
    assert build["status_code"] == "PENDING"
    assert build["span_id"].startswith("pending-")
    counted = _one("SELECT span_count FROM sessions WHERE trace_id = ?", UNPAIRED_SID)[0]
    assert counted == len(_spans(UNPAIRED_SID)) - 1

    _append_bash_failure(workbuddy_home)
    _import(UNPAIRED_SID)

    raw = _raw_rows(UNPAIRED_SID)
    assert [r["status_code"] for r in raw if r["name"] == "tool.Bash"].count("PENDING") == 1
    merged = [s["status_code"] for s in merge_spans(raw) if s["name"] == "tool.Bash"]
    assert merged == ["ERROR"]


def test_unknown_record_type_is_skipped(workbuddy_home):
    from lib.trace.codebuddy_transcript import read_usage_codebuddy

    usage = read_usage_codebuddy(str(workbuddy_home / f"{DESKTOP_SID}.jsonl"))
    assert usage is not None
    assert len(usage.turns) == 2 and len(usage.tool_spans) == 2

    _import(DESKTOP_SID)
    names = {s["name"] for s in _spans(DESKTOP_SID)}
    assert not any("future" in n for n in names)
    assert {"prompt", "tool.Read", "tool.Bash"} <= names


def test_live_rescan_finds_codebuddy_transcript_through_provider(workbuddy_home):
    from lib.trace import live_rescan

    _import(DESKTOP_SID)
    live_rescan._provider_cache.clear()

    provider = live_rescan._session_provider(DESKTOP_SID)
    assert provider.provider_id == "workbuddy"
    assert live_rescan._find_main_transcript(DESKTOP_SID) == str(
        workbuddy_home / f"{DESKTOP_SID}.jsonl")
    usage = provider.parse_transcript(live_rescan._find_main_transcript(DESKTOP_SID))
    assert usage.prompt_texts and usage.tool_spans


def test_import_rejects_a_provider_without_codebuddy_transcripts():
    from cli.app import app
    result = CliRunner().invoke(app, ["trace", "import-transcripts", "--provider", "kimi"])
    assert result.exit_code == 2


def test_automation_prompt_takes_the_last_user_query():
    from lib.trace.codebuddy_transcript import prompt_text

    text = ('<system-reminder data-role="user-context">answer the <user_query> below.'
            '\n</system-reminder>\n<user_query>关注当天 AI 动态</user_query>')
    assert prompt_text([{"type": "input_text", "text": text}]) == "关注当天 AI 动态"
    assert prompt_text([{"type": "input_text", "text": " plain headless "}]) == "plain headless"


def _tool_row(span_id: str, status: str, **attrs) -> dict:
    return {"trace_id": DESKTOP_SID, "span_id": span_id, "parent_id": None,
            "name": "tool.Bash", "status_code": status,
            "start_time": "2026-10-22T10:00:00", "end_time": "2026-10-22T10:00:01",
            "attributes": {"tool_name": "Bash", **attrs}}


def _post_hook_bash_span() -> None:
    from lib import hook_plugin
    from lib.trace.in_process_ingest import in_process_ingest
    hook_plugin.set_active_agent_type("workbuddy")
    try:
        with in_process_ingest() as errors:
            hook_plugin.post_span(
                trace_id=DESKTOP_SID, name="tool.Bash", span_id="abcd1234abcd1234",
                start_time="2026-10-22T10:00:00", end_time="2026-10-22T10:00:01",
                attributes={"tool_name": "Bash", "exitCode": 1,
                            "tool_use_id": "call_01_9QwErTyUiOpAsDfGhJkL0001"})
    finally:
        hook_plugin.set_active_agent_type(None)
    assert errors == []


def test_import_into_hooked_session_counts_each_call_once(workbuddy_home):
    _post_hook_bash_span()

    _import(DESKTOP_SID)

    bash = [s for s in _spans(DESKTOP_SID) if s["name"] == "tool.Bash"]
    assert [s["span_id"] for s in bash] == ["abcd1234abcd1234"]
    counted = _one("SELECT span_count FROM sessions WHERE trace_id = ?", DESKTOP_SID)[0]
    assert counted == len(_spans(DESKTOP_SID))


def test_hook_path_leaves_tool_spans_to_the_hooks(workbuddy_home):
    from hook_manager.handlers.turn_trace.entry import _ingest_transcript_usage
    from lib.providers.registry import build_provider
    from lib.trace.in_process_ingest import in_process_ingest

    with in_process_ingest():
        _ingest_transcript_usage(DESKTOP_SID, str(workbuddy_home / f"{DESKTOP_SID}.jsonl"),
                                 None, build_provider("workbuddy"))

    names = [s["name"] for s in _spans(DESKTOP_SID)]
    assert "prompt" in names and "assistant.thinking" in names
    assert not [n for n in names if n.startswith("tool.")]


def test_transcript_pending_drops_once_a_hook_resolves_the_call():
    from lib.trace.merge import merge_spans

    def row(span_id, status, **attrs):
        return {"trace_id": UNPAIRED_SID, "span_id": span_id, "parent_id": None,
                "name": "tool.Bash", "status_code": status,
                "start_time": "2026-10-22T10:00:00", "end_time": None,
                "attributes": {"tool_name": "Bash", **attrs}}

    merged = merge_spans([
        row("pending-0123456789abc", "PENDING", tool_use_id="call_00_y",
            transcript_source=True),
        row("hook2", "OK", tool_use_id="call_00_y"),
    ])

    assert [s["span_id"] for s in merged] == ["hook2"]


def test_import_reports_rejected_posts(workbuddy_home, monkeypatch):
    from cli.app import app
    from lib.trace import trace_service

    def refuse(_rows):
        raise RuntimeError("disk full")
    monkeypatch.setattr(trace_service, "ingest_turn_usage", refuse)

    result = CliRunner().invoke(
        app, ["trace", "import-transcripts", "--provider", "workbuddy",
              "--session", DESKTOP_SID])

    assert result.exit_code == 1
    assert "failed=1" in result.output


def test_imported_history_keeps_last_seen_at_transcript_time(workbuddy_home):
    _import(DESKTOP_SID)

    from datetime import datetime
    row = _one("SELECT status, last_seen FROM sessions WHERE trace_id = ?", DESKTOP_SID)
    newest = datetime.fromtimestamp(1792649026100 / 1000).isoformat()
    assert row["status"] is None
    assert row["last_seen"] <= newest
    title = _one("SELECT start_time FROM session_spans WHERE trace_id = ? "
                 "AND name = 'session.title'", DESKTOP_SID)
    assert title["start_time"] == datetime.fromtimestamp(1792649022300 / 1000).isoformat()
