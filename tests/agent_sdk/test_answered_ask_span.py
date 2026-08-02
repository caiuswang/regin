"""An answered `AskUserQuestion` has to stay readable in the trace.

The questions live on the PENDING row, which the serve-time merge retires as
soon as the tool's resolved row lands — so whatever the resolved row does not
carry is simply gone. Measured against the hook tier, which records
`questions` + `answers` on its PostToolUse span, the SDK tier's resolved row
carried only `{tool_name, tool_use_id}`: the answered question rendered as a
bare tool line with neither what was asked nor what was picked.
"""

from __future__ import annotations

import asyncio

import pytest

from lib.agent_events import ToolResult
from lib.agent_sdk import registry, runner as runner_mod
from lib.settings import settings


class _Ctx:
    def __init__(self, tool_use_id="tu-ask"):
        self.tool_use_id = tool_use_id


QUESTION_INPUT = {
    "questions": [{
        "question": "Ship it?",
        "header": "Ship",
        "options": [{"label": "yes"}, {"label": "no"}],
    }],
}


@pytest.fixture
def posted(monkeypatch):
    spans: list[dict] = []

    async def _post(self, span):
        if span:
            spans.append(span)

    monkeypatch.setattr(runner_mod.AgentRunner, "_post", _post)
    return spans


async def _answer(run, answers):
    """Park a question, answer it the way `/live` does, and emit the tool
    result the SDK sends once the tool has run."""
    run.loop = asyncio.get_running_loop()
    task = asyncio.create_task(
        run._park("question", "AskUserQuestion", QUESTION_INPUT, _Ctx()))
    for _ in range(200):
        if registry.pending_asks(run.trace_id):
            break
        await asyncio.sleep(0)
    assert registry.resolve_ask(run.trace_id, answers)[0]
    await task
    await run._emit(ToolResult(trace_id=run.trace_id,
                               tool_name="AskUserQuestion",
                               tool_use_id="tu-ask"))


def _resolved(spans):
    return next(s for s in spans if s["name"] == "tool.AskUserQuestion"
                and s["status_code"] == "OK")


def test_the_resolved_span_carries_the_question_and_the_answer(posted):
    pytest.importorskip("claude_agent_sdk")

    asyncio.run(_answer(runner_mod.AgentRunner("sdk-ask-1"),
                        [{"option_index": 0}]))

    attrs = _resolved(posted)["attributes"]
    # The pair the conversation card renders — it falls back to a bare tool row
    # without `questions`, and marks no option chosen without `answers`.
    assert attrs["questions"][0]["question"] == "Ship it?"
    assert attrs["answers"] == {"Ship it?": "yes"}


def test_a_typed_answer_is_recorded_as_the_agent_received_it(posted):
    pytest.importorskip("claude_agent_sdk")

    asyncio.run(_answer(runner_mod.AgentRunner("sdk-ask-2"),
                        [{"option_index": 0, "text": "ship on friday"}]))

    assert _resolved(posted)["attributes"]["answers"] == {
        "Ship it?": "ship on friday"}


def test_a_dismissed_question_stamps_nothing(posted):
    """A dismissal denies the call, so there is no answer to record — and the
    held Q&A must not leak onto the next tool result."""
    pytest.importorskip("claude_agent_sdk")

    asyncio.run(_answer(runner_mod.AgentRunner("sdk-ask-3"), None))

    assert "answers" not in _resolved(posted)["attributes"]


def test_the_answer_is_stamped_once_not_on_every_later_result(posted):
    pytest.importorskip("claude_agent_sdk")

    async def _twice():
        run = runner_mod.AgentRunner("sdk-ask-4")
        await _answer(run, [{"option_index": 1}])
        await run._emit(ToolResult(trace_id=run.trace_id, tool_name="Bash",
                                   tool_use_id="tu-ask"))

    asyncio.run(_twice())

    bash = next(s for s in posted if s["name"] == "tool.Bash")
    assert "answers" not in bash["attributes"]
