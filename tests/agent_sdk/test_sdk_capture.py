"""What an SDK-owned session records (`lib/agent_events`).

The tier used to capture only what the agent *did* — tool calls — so a session
whose answer was a sentence left no trace of that sentence, and its token spend
never reached `turn_usage`. These tests pin the other half: text, reasoning,
session boundaries, and the usage row the context meter reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from lib.agent_events import (
    AssistantText,
    AssistantThinking,
    SessionEnded,
    SessionStarted,
    TurnCompleted,
    to_span,
    turn_usage_row,
)
from lib.agent_events.from_sdk import from_sdk_message
from lib.agent_events.usage import context_tokens
from lib.settings import settings


# The SDK's own classes are matched by name, so local stand-ins exercise the
# normalizer without a live agent or the SDK installed.
@dataclass
class TextBlock:
    text: str


@dataclass
class ThinkingBlock:
    thinking: str = ''
    signature: str = ''


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict = field(default_factory=dict)


@dataclass
class AssistantMessage:
    content: list
    model: str | None = None
    parent_tool_use_id: str | None = None
    usage: dict | None = None


@dataclass
class ResultMessage:
    is_error: bool = False
    result: str | None = None
    duration_ms: int = 0
    usage: dict | None = None


def test_every_block_becomes_an_event_in_emission_order():
    message = AssistantMessage(
        content=[
            ThinkingBlock(thinking="weighing it up"),
            TextBlock(text="PICKED=MySQL"),
            ToolUseBlock(id="toolu_1", name="Bash"),
        ],
        model="claude-opus-5",
    )

    events = from_sdk_message("t1", message)

    assert [type(e).__name__ for e in events] == [
        "AssistantThinking", "AssistantText", "ToolCall"]
    assert events[1].text == "PICKED=MySQL"
    assert events[1].model == "claude-opus-5"


def test_subagent_text_carries_the_agent_id():
    message = AssistantMessage(content=[TextBlock(text="done")],
                               parent_tool_use_id="toolu_parent")

    assert from_sdk_message("t1", message)[0].agent_id == "toolu_parent"


def test_whitespace_only_text_is_not_an_event():
    assert from_sdk_message("t1", AssistantMessage(content=[TextBlock("  \n")])) == []


def test_encrypted_thinking_is_still_captured_by_its_signature():
    message = AssistantMessage(content=[ThinkingBlock(signature="abcd")])

    event = from_sdk_message("t1", message)[0]

    assert event.text == ""
    assert event.signature_bytes == 4


def test_assistant_text_projects_onto_the_hook_tier_span_name():
    span = to_span(AssistantText(trace_id="t1", text="hello",
                                 model="claude-opus-5"))

    assert span["name"] == "assistant_response"
    assert span["attributes"]["text"] == "hello"
    assert span["attributes"]["response_chars"] == 5
    assert span["attributes"]["model"] == "claude-opus-5"
    assert span["status_code"] == "OK"


def test_thinking_projects_onto_its_own_span_name():
    span = to_span(AssistantThinking(trace_id="t1", text="hmm",
                                     signature_bytes=9))

    assert span["name"] == "assistant.thinking"
    assert span["attributes"]["thinking_text"] == "hmm"
    assert span["attributes"]["thinking_blocks"] == 1
    assert span["attributes"]["thinking_signature_bytes"] == 9


def test_long_response_is_truncated_at_the_configured_byte_cap(monkeypatch):
    monkeypatch.setattr(settings, "assistant_response_max_bytes", 32)

    span = to_span(AssistantText(trace_id="t1", text="x" * 500))

    assert span["attributes"]["truncated"] is True
    assert len(span["attributes"]["text"].encode("utf-8")) < 100
    # The uncapped length is still reported, so the UI can say what was cut.
    assert span["attributes"]["response_chars"] == 500


def test_capture_gate_suppresses_both_assistant_spans(monkeypatch):
    monkeypatch.setattr(settings, "capture_assistant_response", False)

    assert to_span(AssistantText(trace_id="t1", text="hi")) is None
    assert to_span(AssistantThinking(trace_id="t1", text="hm")) is None


def test_session_boundaries_project_to_lifecycle_spans():
    start = to_span(SessionStarted(trace_id="t1", source="sdk",
                                   model="claude-opus-5", cwd="/repo"))
    end = to_span(SessionEnded(trace_id="t1", reason="stopped"))

    assert start["name"] == "session.start"
    assert start["attributes"] == {"source": "sdk", "model": "claude-opus-5",
                                   "cwd": "/repo"}
    assert end["name"] == "session.end"
    assert end["attributes"]["reason"] == "stopped"


def test_result_usage_is_normalized_off_the_provider_key_names():
    message = ResultMessage(usage={
        "input_tokens": 10,
        "output_tokens": 3,
        "cache_read_input_tokens": 400,
        "cache_creation_input_tokens": 50,
    })

    event = from_sdk_message("t1", message)[0]

    assert event.usage == {
        "input_tokens": 10, "output_tokens": 3,
        "cache_read_tokens": 400, "cache_creation_tokens": 50,
    }


def test_an_interrupted_turn_keeps_its_token_spend():
    """The tokens were spent whether or not the turn produced an answer, and
    an interrupt is the likeliest way for a turn to end."""
    message = ResultMessage(is_error=True, result="Interrupted by user",
                            usage={"input_tokens": 4, "output_tokens": 512})

    event = from_sdk_message("t1", message)[0]

    assert type(event).__name__ == "TurnFailed"
    assert event.usage["output_tokens"] == 512


def test_a_call_with_no_renderable_blocks_still_reports_its_context():
    """A server-tool-only or empty message moved the context; skipping it
    would leave a stale earlier call standing in as the turn's size."""
    message = AssistantMessage(
        content=[], usage={"input_tokens": 1, "cache_read_input_tokens": 90000})

    events = from_sdk_message("t1", message)

    assert [type(e).__name__ for e in events] == ["UsageUpdated"]
    assert context_tokens(events[0].usage) == 90001


def test_an_assistant_message_reports_its_own_call_usage():
    """The per-call prompt size, which the turn totals cannot reconstruct."""
    message = AssistantMessage(
        content=[TextBlock(text="hi")],
        usage={"input_tokens": 2, "output_tokens": 1,
               "cache_read_input_tokens": 30663,
               "cache_creation_input_tokens": 367},
    )

    events = from_sdk_message("t1", message)

    assert type(events[-1]).__name__ == "UsageUpdated"
    assert context_tokens(events[-1].usage) == 31032


def test_a_subagent_message_reports_no_usage():
    """A Task's spend is not the parent turn's context."""
    message = AssistantMessage(
        content=[TextBlock(text="sub")], parent_tool_use_id="toolu_task",
        usage={"input_tokens": 5},
    )

    assert not [e for e in from_sdk_message("t1", message)
                if type(e).__name__ == "UsageUpdated"]


def test_context_is_the_last_calls_prompt_not_the_turns_traffic():
    """A tool-loop turn sums cache_creation across its calls, so the totals
    describe traffic, not the size of any prompt that was actually sent."""
    event = TurnCompleted(trace_id="t1", usage={
        "input_tokens": 4, "output_tokens": 288,
        "cache_read_tokens": 30663, "cache_creation_tokens": 31030,
    })

    row = turn_usage_row(event, 0, context_used=31032)

    assert row["context_used_tokens"] == 31032
    # Billing totals are the turn's, and stay untouched.
    assert row["output_tokens"] == 288
    assert row["cache_creation_tokens"] == 31030


def test_turn_usage_row_sums_context_from_input_plus_both_caches():
    event = TurnCompleted(trace_id="t1", usage={
        "input_tokens": 10, "output_tokens": 3,
        "cache_read_tokens": 400, "cache_creation_tokens": 50,
    })

    row = turn_usage_row(event, 0, model="claude-opus-5")

    assert row["context_used_tokens"] == 460
    assert row["model"] == "claude-opus-5"
    assert row["turn_index"] == 0
    assert row["timestamp"]


def test_each_turn_gets_its_own_usage_row_identity():
    event = TurnCompleted(trace_id="t1")

    first = turn_usage_row(event, 0)["turn_uuid"]
    second = turn_usage_row(event, 1)["turn_uuid"]

    # (trace_id, turn_uuid) is the ingest's PK — a shared uuid would make the
    # second turn silently overwrite the first.
    assert first != second


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": "nope"}])
def test_malformed_usage_degrades_to_zeros_rather_than_raising(usage):
    event = from_sdk_message("t1", ResultMessage(usage=usage))[0]

    assert event.usage["input_tokens"] == 0
