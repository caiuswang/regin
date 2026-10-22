"""The `regin` MCP server: send_to_user plus the pipeline, as one server."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from lib.agent_messages import mcp_server as send_to_user_server
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.orm.models.trace import SessionSpan
from lib.regin_mcp import server
from lib.trace import span_gates

PIPELINE_TOOL_NAMES = {
    "pipeline_overview", "feature_list", "feature_get", "feature_create",
    "feature_set_spec", "task_list", "task_get", "task_create",
    "defect_open", "defect_list", "defect_close"}


def _tool_names(mcp) -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


def _call(mcp, name: str, **args):
    """Through FastMCP's own dispatch, so argument validation and the error
    path are the ones a client gets."""
    out = asyncio.run(mcp.call_tool(name, args))
    if isinstance(out, tuple):
        return out[1]["result"]
    return json.loads(out[0].text)


@pytest.fixture
def mcp():
    return server.build_server()


@pytest.fixture
def project():
    with SessionLocal() as s:
        s.add(Repo(name="core", path="/tmp/core"))
        s.commit()
    return "core"


def test_one_server_carries_send_to_user_and_the_pipeline(mcp):
    assert mcp.name == "regin"
    assert _tool_names(mcp) == PIPELINE_TOOL_NAMES | {"send_to_user"}


def test_send_to_user_schema_is_the_standalone_servers(mcp):
    ours = {t.name: t for t in asyncio.run(mcp.list_tools())}["send_to_user"]
    theirs, = asyncio.run(send_to_user_server.mcp.list_tools())
    assert ours.inputSchema == theirs.inputSchema
    assert ours.description == theirs.description
    assert _call(mcp, "send_to_user", message="hi") == "delivered"


def test_without_send_to_user_lists_only_the_pipeline():
    mcp = server.build_server(with_send_to_user=False)
    assert _tool_names(mcp) == PIPELINE_TOOL_NAMES


def test_the_script_lists_its_tools_over_stdio():
    """The server is launched as a script from an arbitrary cwd, so a
    module-scope regin import or a sys.path slip only shows up this way."""
    script = Path(server.__file__)
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "0"}}}
    ready = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    stdin = "".join(json.dumps(m) + "\n" for m in (init, ready, listing))
    proc = subprocess.run([sys.executable, str(script), server.NO_SEND_TO_USER],
                          input=stdin, capture_output=True, text=True,
                          cwd="/", timeout=60)
    replies = [json.loads(line) for line in proc.stdout.splitlines() if line]
    tools = next(r for r in replies if r.get("id") == 2)["result"]["tools"]
    assert {t["name"] for t in tools} == PIPELINE_TOOL_NAMES


@pytest.fixture
def feature(mcp, project):
    return _call(mcp, "feature_create", title="Unified MCP", spec="one server",
                 project=project, session_id="trace-1")


def test_feature_create_and_set_spec(mcp, project, feature):
    assert feature["stage"] == "spec"
    _call(mcp, "feature_set_spec", feature_id=feature["id"], spec="v2 spec")
    shown = _call(mcp, "feature_get", feature_id=feature["id"])
    assert (shown["spec"], shown["project"]) == ("v2 spec", project)
    listed = _call(mcp, "feature_list", project=project)
    assert [f["id"] for f in listed["features"]] == [feature["id"]]


def test_task_create_under_a_feature(mcp, feature):
    task = _call(mcp, "task_create", title="build it",
                 feature_id=feature["id"], priority="high")
    assert (task["feature_id"], task["source"]) == (feature["id"], "mcp")
    assert _call(mcp, "task_get", task_id=task["id"])["title"] == "build it"
    shown = _call(mcp, "feature_get", feature_id=feature["id"])
    assert [t["id"] for t in shown["tasks"]] == [task["id"]]


def test_defect_open_dedups_and_closes(mcp, feature):
    defect = _call(mcp, "defect_open", symptom="tool list empty",
                   feature_id=feature["id"], session_id="trace-1",
                   paths=["lib/regin_mcp/server.py"])
    again = _call(mcp, "defect_open", symptom="tool list empty",
                  feature_id=feature["id"], session_id="trace-1")
    assert (defect["deduped"], again["deduped"]) == (False, True)
    assert again["id"] == defect["id"]
    shown = _call(mcp, "feature_get", feature_id=feature["id"])
    assert [d["id"] for d in shown["defects"]] == [defect["id"]]

    closed = _call(mcp, "defect_close", defect_id=defect["id"],
                   check="a stdio tools/list test")
    assert (closed["status"], closed["missing_check"]) == (
        "closed", "a stdio tools/list test")


def test_an_empty_list_still_reaches_a_content_only_client(mcp):
    content = asyncio.run(mcp.call_tool("defect_list", {}))
    blocks = content[0] if isinstance(content, tuple) else content
    assert json.loads(blocks[0].text) == {"defects": []}


def test_empty_states(mcp):
    assert _call(mcp, "feature_list")["features"] == []
    assert _call(mcp, "task_list") == {"tasks": []}
    assert _call(mcp, "defect_list") == {"defects": []}
    assert _call(mcp, "defect_list", whole_ledger=True) == {"defects": []}
    overview = _call(mcp, "pipeline_overview")
    assert overview["scope"] == "all"


def test_unattributed_defect_lists_until_attributed(mcp):
    opened = _call(mcp, "defect_open", symptom="orphan bug")
    assert opened["feature_id"] is None
    listed = _call(mcp, "defect_list")["defects"]
    assert [d["id"] for d in listed] == [opened["id"]]


@pytest.mark.parametrize("name,args,needle", [
    ("feature_get", {"feature_id": 999}, "no feature 999"),
    ("task_get", {"task_id": 999}, "no task #999"),
    ("feature_list", {"project": "nope"}, "no project named 'nope'"),
    ("feature_list", {"stage": "bogus"}, "unknown stage"),
    ("feature_set_spec", {"feature_id": 999, "spec": "x"}, "no feature 999"),
    ("task_create", {"title": "t", "priority": "asap"}, "unknown priority"),
    ("task_create", {"title": "  "}, "needs a title"),
    ("task_create", {"title": "t", "feature_id": 999}, "no feature 999"),
    ("defect_open", {"symptom": "  "}, "symptom"),
])
def test_bad_input_is_a_tool_error_naming_the_problem(mcp, name, args, needle):
    with pytest.raises(ToolError, match=needle):
        _call(mcp, name, **args)


def test_empty_spec_is_refused_not_written(mcp):
    feature = _call(mcp, "feature_create", title="keep my spec", spec="original")
    with pytest.raises(ToolError, match="empty"):
        _call(mcp, "feature_set_spec", feature_id=feature["id"], spec="  ")
    assert _call(mcp, "feature_get", feature_id=feature["id"])["spec"] == "original"


@pytest.mark.parametrize("span_name", [
    "tool.mcp__regin__defect_open",
    "tool.mcp__plugin_regin-agents_regin__feature_get",
])
def test_pipeline_gate_counts_the_servers_calls(span_name):
    with SessionLocal() as s:
        s.add(SessionSpan(trace_id="t-gate", span_id="s1", parent_id=None,
                          name=span_name, kind="internal",
                          start_time="2026-10-22 10:00:00"))
        s.commit()
    assert span_gates.span_count("t-gate", span_gates.GATES["pipeline-ran"]) == 1


def test_pipeline_gate_ignores_the_servers_read_only_listings():
    with SessionLocal() as s:
        s.add(SessionSpan(trace_id="t-list", span_id="s1", parent_id=None,
                          name="tool.mcp__regin__task_list", kind="internal",
                          start_time="2026-10-22 10:00:00"))
        s.commit()
    assert span_gates.span_count("t-list", span_gates.GATES["pipeline-ran"]) == 0
