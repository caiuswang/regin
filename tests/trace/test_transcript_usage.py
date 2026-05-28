"""Unit tests for lib.trace.transcript_usage.

Covers parser-only behaviour — the integration with turn_trace /
trace_service is exercised via test_trace_ingest.py.
"""

from __future__ import annotations

import json

from lib.trace.transcript_usage import read_usage


def _write(tmp_path, *entries) -> str:
    p = tmp_path / "t.jsonl"
    with open(p, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return str(p)


def _assistant(input_tokens: int, cache_read: int = 0, cache_creation: int = 0,
               output: int = 0, model: str = "claude-opus-4-7",
               uuid: str | None = None, timestamp: str | None = None,
               request_id: str | None = None) -> dict:
    entry = {
        "type": "assistant",
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
            },
        },
    }
    if uuid is not None:
        entry["uuid"] = uuid
    if timestamp is not None:
        entry["timestamp"] = timestamp
    if request_id is not None:
        entry["requestId"] = request_id
    return entry


def test_missing_file_returns_none(tmp_path):
    assert read_usage(str(tmp_path / "nope.jsonl")) is None


def test_empty_file_returns_none(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    assert read_usage(str(p)) is None


def test_no_assistant_turns_returns_none(tmp_path):
    path = _write(tmp_path, {"type": "user", "message": {"content": "hi"}})
    assert read_usage(path) is None


def test_single_turn_peak_equals_sum(tmp_path):
    path = _write(tmp_path, _assistant(input_tokens=100, cache_read=5000,
                                       cache_creation=200, output=50))
    u = read_usage(path)
    assert u is not None
    assert u.peak_context_tokens == 100 + 5000 + 200  # 5300
    assert u.input_tokens == 100
    assert u.cache_read_tokens == 5000
    assert u.output_tokens == 50
    assert u.model == "claude-opus-4-7"


def test_peak_is_max_across_turns_not_last(tmp_path):
    path = _write(
        tmp_path,
        _assistant(input_tokens=1000, cache_read=50_000),    # ctx 51k
        _assistant(input_tokens=2000, cache_read=180_000),   # ctx 182k ← peak
        _assistant(input_tokens=500,  cache_read=20_000),    # ctx 20.5k
    )
    u = read_usage(path)
    assert u is not None
    assert u.peak_context_tokens == 182_000
    # Sums cover every turn, not just the peak.
    assert u.input_tokens == 3500
    assert u.cache_read_tokens == 250_000


def test_latest_model_wins(tmp_path):
    path = _write(
        tmp_path,
        _assistant(input_tokens=100, model="claude-opus-4-7"),
        _assistant(input_tokens=100, model="claude-opus-4-7[1m]"),
    )
    u = read_usage(path)
    assert u is not None
    assert u.model == "claude-opus-4-7[1m]"


def test_per_turn_provenance_fields_are_captured(tmp_path):
    """turn_trace handler needs uuid + timestamp + requestId to make
    stable, idempotent span ids for per-turn spans."""
    path = _write(
        tmp_path,
        _assistant(input_tokens=100,
                   uuid="a" * 36,
                   timestamp="2026-04-24T10:00:00.000Z",
                   request_id="req_abc123"),
    )
    u = read_usage(path)
    assert u is not None
    t = u.turns[0]
    assert t.uuid == "a" * 36
    assert t.timestamp == "2026-04-24T10:00:00.000Z"
    assert t.request_id == "req_abc123"


def test_missing_provenance_fields_are_none(tmp_path):
    """Older transcripts (or malformed ones) may lack uuid/timestamp —
    the fields must be None rather than crash the parser."""
    path = _write(tmp_path, _assistant(input_tokens=50))
    u = read_usage(path)
    assert u is not None
    assert u.turns[0].uuid is None
    assert u.turns[0].timestamp is None
    assert u.turns[0].request_id is None


def test_duplicate_message_id_dedups(tmp_path):
    """A single API response is split into multiple `assistant` entries
    in the transcript (text block + tool_use block → two entries with
    the same message.id). Each entry repeats the identical usage
    block, so counting both double-counts the turn. The parser must
    dedup by message.id."""
    shared_id = "msg_01DuplicateExample"
    path = _write(
        tmp_path,
        # First block of turn 1 — text.
        {**_assistant(input_tokens=10, cache_read=1_000, output=50,
                      uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
         "message": {**_assistant(input_tokens=10, cache_read=1_000, output=50)["message"],
                     "id": shared_id}},
        # Second block of turn 1 — tool_use, same message.id.
        {**_assistant(input_tokens=10, cache_read=1_000, output=50,
                      uuid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
         "message": {**_assistant(input_tokens=10, cache_read=1_000, output=50)["message"],
                     "id": shared_id}},
        # A separate turn — different message.id.
        {**_assistant(input_tokens=20, cache_read=2_000, output=80,
                      uuid="cccccccc-cccc-cccc-cccc-cccccccccccc"),
         "message": {**_assistant(input_tokens=20, cache_read=2_000, output=80)["message"],
                     "id": "msg_01OtherTurn"}},
    )
    u = read_usage(path)
    assert u is not None
    assert len(u.turns) == 2  # not 3
    # First turn keeps the first-block uuid; dedup silently drops block 2.
    assert u.turns[0].uuid.startswith("aaaaaaaa")
    assert u.turns[1].uuid.startswith("cccccccc")
    # Sums don't double-count the shared-id pair.
    assert u.input_tokens == 30  # 10 + 20
    assert u.output_tokens == 130  # 50 + 80


def test_dedup_falls_back_to_request_id_when_message_id_missing(tmp_path):
    """Older transcripts may lack `message.id` — requestId at the
    top level is an acceptable second-choice key."""
    path = _write(
        tmp_path,
        {**_assistant(input_tokens=10, request_id="req_shared"),
         "uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
        {**_assistant(input_tokens=10, request_id="req_shared"),
         "uuid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
    )
    u = read_usage(path)
    assert u is not None
    assert len(u.turns) == 1


def test_malformed_lines_are_skipped(tmp_path):
    p = tmp_path / "t.jsonl"
    with open(p, "w") as f:
        f.write("not json at all\n")
        f.write(json.dumps(_assistant(input_tokens=42)) + "\n")
        f.write("{\"partial\":\n")  # truncated
    u = read_usage(str(p))
    assert u is not None
    assert u.peak_context_tokens == 42


def test_codex_style_camel_case_transcript_parses(tmp_path):
    path = _write(
        tmp_path,
        {
            "type": "assistant",
            "uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "requestId": "req_1",
            "timestamp": "2026-05-01T09:00:00Z",
            "message": {
                "id": "msg_1",
                "model": "gpt-5.3-codex",
                "content": "done",
                "usage": {
                    "promptTokens": 123,
                    "completionTokens": 45,
                    "cacheReadInputTokens": 7,
                    "cacheCreationInputTokens": 8,
                },
            },
        },
    )
    u = read_usage(path)
    assert u is not None
    assert len(u.turns) == 1
    t = u.turns[0]
    assert t.model == "gpt-5.3-codex"
    assert t.input_tokens == 123
    assert t.output_tokens == 45
    assert t.cache_read_tokens == 7
    assert t.cache_creation_tokens == 8
    assert t.text == "done"
    assert u.peak_context_tokens == 138


def test_role_based_assistant_entry_parses(tmp_path):
    path = _write(
        tmp_path,
        {
            "role": "assistant",
            "id": "msg_2",
            "timestamp": "2026-05-01T09:01:00Z",
            "model": "gpt-5.3-codex",
            "content": "answer",
            "usage": {
                "input_tokens": 50,
                "output_tokens": 10,
            },
        },
    )
    u = read_usage(path)
    assert u is not None
    assert len(u.turns) == 1
    assert u.turns[0].text == "answer"
    assert u.turns[0].input_tokens == 50
    assert u.turns[0].output_tokens == 10


# ── Response text extraction ────────────────────────────────────────

def _user(uuid: str, parent: str | None = None,
          *, timestamp: str | None = None) -> dict:
    e: dict = {
        "type": "user",
        "uuid": uuid,
        "parentUuid": parent,
        "message": {"content": "go"},
    }
    if timestamp is not None:
        e["timestamp"] = timestamp
    return e


def _asst_with_content(*, msg_id: str, content: list, uuid: str,
                        parent: str | None = None,
                        with_usage: bool = True,
                        timestamp: str = "2026-04-27T12:00:00Z") -> dict:
    e: dict = {
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": timestamp,
        "message": {
            "id": msg_id,
            "model": "claude-opus-4-7",
            "content": content,
        },
    }
    if with_usage:
        e["message"]["usage"] = {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
    return e


def test_extracts_response_text_from_text_blocks(tmp_path):
    """A turn's `text` is the concatenation of every `type: text` block
    in `message.content[]` only. `type: thinking` blocks land in a
    separate `thinking_text` field; tool_use blocks are skipped. The
    split keeps the response span as "what the user saw" while still
    making the reasoning available to consumers that ask for it."""
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
            {"type": "thinking", "thinking": "secret"},
            {"type": "text", "text": "world"},
        ]),
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 1
    t = u.turns[0]
    assert t.text == "hello\n\nworld"
    assert t.text_truncated is False
    assert t.thinking_text == "secret"
    assert t.thinking_blocks == 1


def test_concatenates_text_across_split_messages(tmp_path):
    """One API response can be split into adjacent assistant entries
    sharing message.id (text-block then tool_use-block in different
    entries). The text from BOTH entries should land on the canonical
    turn."""
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="same", uuid="asst-a", parent="user-1", content=[
            {"type": "text", "text": "first"},
        ]),
        _asst_with_content(msg_id="same", uuid="asst-b", parent="asst-a",
                            content=[
                                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                                {"type": "text", "text": "second"},
                            ],
                            with_usage=False),
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 1
    assert u.turns[0].text == "first\n\nsecond"


def test_resolves_prompt_uuid_via_parent_chain(tmp_path):
    """The owning user-prompt uuid must be resolved by walking up the
    parentUuid chain, even when intermediate `user` entries (tool
    results) intervene."""
    path = _write(
        tmp_path,
        _user("user-real", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-real",
                            content=[{"type": "text", "text": "ok"}]),
        # tool_result is a `user` entry in between — chain still
        # resolves to user-real because that's the topmost user.
        {"type": "user", "uuid": "tool-r1", "parentUuid": "asst-1",
         "message": {"content": [{"type": "tool_result", "content": "out"}]}},
        _asst_with_content(msg_id="m2", uuid="asst-2", parent="tool-r1",
                            content=[{"type": "text", "text": "done"}]),
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 2
    # Both turns should resolve to the original user prompt.
    assert u.turns[0].prompt_uuid == "user-real"
    assert u.turns[1].prompt_uuid == "user-real"


def test_truncates_text_at_byte_cap(tmp_path):
    """Per-turn text caps at max_text_bytes (UTF-8 bytes), with a
    truncation marker appended."""
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "text", "text": "x" * 1000},
        ]),
    )
    u = read_usage(path, max_text_bytes=50)
    assert u is not None and len(u.turns) == 1
    t = u.turns[0]
    assert t.text_truncated is True
    assert t.text is not None and t.text.endswith("…[truncated]")
    body = t.text.rsplit("\n\n", 1)[0]
    assert len(body.encode("utf-8")) <= 50


def test_no_truncation_when_cap_unset(tmp_path):
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "text", "text": "x" * 1000},
        ]),
    )
    u = read_usage(path)  # no cap argument
    assert u is not None and u.turns[0].text_truncated is False
    assert u.turns[0].text == "x" * 1000


def test_text_field_is_none_when_no_text_blocks(tmp_path):
    """A tool-only turn (no text content) leaves text=None so callers
    can decide whether to emit an assistant_response span."""
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
        ]),
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 1
    assert u.turns[0].text is None


def test_extracts_thinking_blocks_when_no_text_blocks(tmp_path):
    """Thinking-enabled models emit reasoning in `type: thinking`
    blocks with no accompanying `type: text`. The thinking content
    lands in `thinking_text`; `text` stays None so callers can tell
    a tool-only-with-reasoning turn apart from a final-text reply."""
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "thinking", "thinking": "Let me think about this..."},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
        ]),
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 1
    t = u.turns[0]
    assert t.text is None
    assert t.thinking_text == "Let me think about this..."
    assert t.thinking_blocks == 1


def test_captures_redacted_thinking_via_signature_bytes(tmp_path):
    """When extended-thinking text is redacted (empty `thinking`
    string) the encrypted `signature` is the only proof reasoning
    happened. Surface `thinking_blocks` and `thinking_signature_bytes`
    so downstream queries can ask "did thinking happen?" without
    parsing blobs."""
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "thinking", "thinking": "", "signature": "a" * 400},
            {"type": "thinking", "thinking": "", "signature": "b" * 200},
            {"type": "text", "text": "answer"},
        ]),
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 1
    t = u.turns[0]
    assert t.text == "answer"
    assert t.thinking_text is None
    assert t.thinking_blocks == 2
    assert t.thinking_signature_bytes == 600


# ── Tool-use ↔ tool-result correlation ──────────────────────────────

def test_tool_calls_correlate_with_results_and_in_flight(tmp_path):
    """tool_use blocks accumulate per turn and pick up is_error from
    the matching tool_result. A tool_use without a matching result in
    this transcript stays is_error=None (in-flight), never coerced to
    False at the boundary."""
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "text", "text": "running a tool"},
            {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {}},
        ]),
        # tool_result for tu_1 with is_error: true.
        {"type": "user", "uuid": "tool-r1", "parentUuid": "asst-1",
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": "tu_1",
              "content": "boom", "is_error": True},
         ]}},
        # second turn: tool_use with no matching tool_result later.
        _asst_with_content(msg_id="m2", uuid="asst-2", parent="tool-r1", content=[
            {"type": "tool_use", "id": "tu_2", "name": "Read", "input": {}},
        ]),
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 2
    assert len(u.turns[0].tool_calls) == 1
    assert u.turns[0].tool_calls[0]["id"] == "tu_1"
    assert u.turns[0].tool_calls[0]["name"] == "Bash"
    assert u.turns[0].tool_calls[0]["is_error"] is True
    # Second turn's tool call is in flight — is_error must be None,
    # NOT False (the absence of a result is not the same as success).
    assert len(u.turns[1].tool_calls) == 1
    assert u.turns[1].tool_calls[0]["id"] == "tu_2"
    assert u.turns[1].tool_calls[0]["name"] == "Read"
    assert u.turns[1].tool_calls[0]["is_error"] is None


def test_server_tool_use_advisor_is_captured(tmp_path):
    """`advisor` (and any future server-side tool) appears in the
    transcript as `type: server_tool_use` and never gets a tool_result
    entry — the result is folded into the same assistant entry's
    `usage.iterations` array. The parser must surface it as a
    `server_side: True` tool_call with token estimates pulled from the
    advisor_message iteration, not the (empty) input estimator.
    """
    path = _write(
        tmp_path,
        _user("user-1", None),
        {
            "type": "assistant",
            "uuid": "asst-1",
            "parentUuid": "user-1",
            "timestamp": "2026-05-15T12:00:00Z",
            "advisorModel": "claude-opus-4-7",
            "message": {
                "id": "m1",
                "model": "claude-opus-4-7",
                "content": [
                    {"type": "text", "text": "asking for advice"},
                    {"type": "server_tool_use",
                     "id": "srvtoolu_abc123", "name": "advisor", "input": {}},
                ],
                "usage": {
                    "input_tokens": 7,
                    "output_tokens": 1329,
                    "cache_read_input_tokens": 233365,
                    "cache_creation_input_tokens": 4599,
                    "iterations": [
                        {"type": "message",
                         "input_tokens": 6, "output_tokens": 1326,
                         "cache_read_input_tokens": 115735,
                         "cache_creation_input_tokens": 1895},
                        {"type": "advisor_message", "model": "claude-opus-4-7",
                         "input_tokens": 120043, "output_tokens": 6931,
                         "cache_read_input_tokens": 0,
                         "cache_creation_input_tokens": 0},
                        {"type": "message",
                         "input_tokens": 1, "output_tokens": 3,
                         "cache_read_input_tokens": 117630,
                         "cache_creation_input_tokens": 2704},
                    ],
                },
            },
        },
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 1
    calls = u.turns[0].tool_calls
    assert len(calls) == 1
    call = calls[0]
    assert call["id"] == "srvtoolu_abc123"
    assert call["name"] == "advisor"
    assert call["server_side"] is True
    assert call["advisor_model"] == "claude-opus-4-7"
    # Tokens come from the advisor_message iteration, NOT the empty-input
    # estimator (which would return ~0 for an empty input dict).
    assert call["output_token_estimate"] == 6931
    assert call["input_token_estimate"] == 120043
    # No tool_result will ever arrive for a server-side tool; is_error
    # stays None by construction.
    assert call["is_error"] is None


def test_server_tool_use_picks_up_advisor_response_text(tmp_path):
    """The advisor's actual response text arrives as an
    `advisor_tool_result` block inside the NEXT assistant entry (not a
    user-side tool_result). The parser must match it by `tool_use_id`
    and patch `response_text` onto the originating server_tool_use call.
    """
    advisor_text = "Implementation looks correct. For the live test..."
    path = _write(
        tmp_path,
        _user("user-1", None),
        {
            "type": "assistant", "uuid": "asst-1", "parentUuid": "user-1",
            "timestamp": "2026-05-15T12:00:00Z",
            "advisorModel": "claude-opus-4-7",
            "message": {
                "id": "m1", "model": "claude-opus-4-7",
                "content": [
                    {"type": "server_tool_use",
                     "id": "srvtoolu_xyz", "name": "advisor", "input": {}},
                ],
                "usage": {"input_tokens": 7, "output_tokens": 100,
                          "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0,
                          "iterations": [
                              {"type": "advisor_message",
                               "input_tokens": 5000, "output_tokens": 800,
                               "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 0},
                          ]},
            },
        },
        # The advisor result rides in the NEXT assistant entry — note
        # type:assistant (not user) and a distinct msg.id so the parser
        # treats it as its own logical turn.
        {
            "type": "assistant", "uuid": "asst-2", "parentUuid": "asst-1",
            "timestamp": "2026-05-15T12:00:05Z",
            "message": {
                "id": "m2", "model": "claude-opus-4-7",
                "content": [
                    {"type": "advisor_tool_result",
                     "tool_use_id": "srvtoolu_xyz",
                     "content": {"type": "advisor_result", "text": advisor_text}},
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1,
                          "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0},
            },
        },
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 2
    # The server-side call lives on the FIRST turn (the one that issued it).
    advisor_call = u.turns[0].tool_calls[0]
    assert advisor_call["id"] == "srvtoolu_xyz"
    assert advisor_call["server_side"] is True
    assert advisor_call["response_text"] == advisor_text


def test_server_tool_use_alongside_regular_tool_use(tmp_path):
    """A single turn can mix server_tool_use and regular tool_use; both
    should appear in tool_calls, with the regular one still able to be
    patched by a later tool_result entry."""
    path = _write(
        tmp_path,
        _user("user-1", None),
        {
            "type": "assistant",
            "uuid": "asst-1",
            "parentUuid": "user-1",
            "timestamp": "2026-05-15T12:00:00Z",
            "message": {
                "id": "m1",
                "model": "claude-opus-4-7",
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {}},
                    {"type": "server_tool_use",
                     "id": "srvtoolu_x", "name": "advisor", "input": {}},
                ],
                "usage": {
                    "input_tokens": 10, "output_tokens": 5,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "iterations": [
                        {"type": "advisor_message", "model": "claude-opus-4-7",
                         "input_tokens": 1000, "output_tokens": 50,
                         "cache_read_input_tokens": 0,
                         "cache_creation_input_tokens": 0},
                    ],
                },
            },
        },
        {"type": "user", "uuid": "tool-r1", "parentUuid": "asst-1",
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": "tu_1",
              "content": "ok"},
         ]}},
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 1
    calls = u.turns[0].tool_calls
    assert len(calls) == 2
    by_id = {c["id"]: c for c in calls}
    # Regular tool: server_side absent, is_error patched from tool_result.
    assert "server_side" not in by_id["tu_1"]
    assert by_id["tu_1"]["is_error"] is False
    # Server tool: tagged server_side, tokens from iterations.
    assert by_id["srvtoolu_x"]["server_side"] is True
    assert by_id["srvtoolu_x"]["output_token_estimate"] == 50
    assert by_id["srvtoolu_x"]["input_token_estimate"] == 1000


def test_tool_result_without_explicit_is_error_defaults_to_false(tmp_path):
    """Anthropic's API treats an absent is_error field as success."""
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "tool_use", "id": "tu_ok", "name": "Read", "input": {}},
        ]),
        {"type": "user", "uuid": "tool-r1", "parentUuid": "asst-1",
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": "tu_ok",
              "content": "ok"},
         ]}},
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 1
    assert len(u.turns[0].tool_calls) == 1
    assert u.turns[0].tool_calls[0]["id"] == "tu_ok"
    assert u.turns[0].tool_calls[0]["name"] == "Read"
    assert u.turns[0].tool_calls[0]["is_error"] is False


def test_multiple_tool_results_in_one_user_entry_all_patch(tmp_path):
    """A single user entry can carry multiple tool_result blocks; each
    must patch is_error onto its originating tool_use."""
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "tool_use", "id": "tu_a", "name": "Read", "input": {}},
            {"type": "tool_use", "id": "tu_b", "name": "Bash", "input": {}},
        ]),
        {"type": "user", "uuid": "tool-r1", "parentUuid": "asst-1",
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": "tu_a", "content": "ok"},
             {"type": "tool_result", "tool_use_id": "tu_b",
              "content": "fail", "is_error": True},
         ]}},
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 1
    calls = {c["id"]: c for c in u.turns[0].tool_calls}
    assert calls["tu_a"]["is_error"] is False
    assert calls["tu_b"]["is_error"] is True


# ── Per-tool token estimates ─────────────────────────────────────────

def test_tool_use_carries_output_token_estimate(tmp_path):
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "tool_use", "id": "tu_x", "name": "Bash",
             "input": {"command": "ls -la /tmp/some/path/with/text"}},
        ]),
    )
    u = read_usage(path)
    assert u is not None
    call = u.turns[0].tool_calls[0]
    assert call["output_token_estimate"] > 0
    # In-flight: no tool_result observed → input estimate still None
    assert call["input_token_estimate"] is None
    assert call["image_token_estimate"] is None


def test_tool_result_patches_input_token_estimate(tmp_path):
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "tool_use", "id": "tu_y", "name": "Read", "input": {"path": "/x"}},
        ]),
        {"type": "user", "uuid": "tr-1", "parentUuid": "asst-1",
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": "tu_y",
              "content": "line one\nline two\nline three of file content"},
         ]}},
    )
    u = read_usage(path)
    assert u is not None
    call = u.turns[0].tool_calls[0]
    assert call["input_token_estimate"] is not None
    assert call["input_token_estimate"] > 0
    assert call["image_token_estimate"] == 0


def test_tool_result_with_image_charges_image_tokens(tmp_path):
    import base64
    import struct
    # Synthetic 1024x768 PNG header
    png = (b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR'
           + struct.pack('>II', 1024, 768) + b'\x08\x02\x00\x00\x00')
    b64 = base64.b64encode(png).decode('ascii')
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "tool_use", "id": "tu_shot",
             "name": "mcp__playwright__browser_take_screenshot",
             "input": {}},
        ]),
        {"type": "user", "uuid": "tr-1", "parentUuid": "asst-1",
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": "tu_shot", "content": [
                 {"type": "image",
                  "source": {"type": "base64", "media_type": "image/png", "data": b64}},
             ]},
         ]}},
    )
    u = read_usage(path)
    assert u is not None
    call = u.turns[0].tool_calls[0]
    # 1024 * 768 / 750 ≈ 1048 image tokens
    assert call["image_token_estimate"] >= 900
    assert call["input_token_estimate"] >= 900
    # image-only result: image tokens should equal total input tokens
    assert call["image_token_estimate"] == call["input_token_estimate"]


# ── Harness attachments ──────────────────────────────────────────────

def _attachment(kind: str, *, uuid: str, attachment: dict,
                parent: str | None = None, timestamp: str | None = None) -> dict:
    e: dict = {
        "type": "attachment",
        "uuid": uuid,
        "attachment": {"type": kind, **attachment},
    }
    if parent is not None:
        e["parentUuid"] = parent
    if timestamp is not None:
        e["timestamp"] = timestamp
    return e


def test_captures_traced_attachments_skips_hook_noise(tmp_path):
    """The harness produces a lot of attachment chatter
    (`hook_success`, `hook_additional_context`) that's pure noise for
    the trace UI. Only the kinds that change agent behaviour are
    captured: task_reminder, skill_listing, deferred_tools_delta."""
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "text", "text": "ok"},
        ]),
        _attachment("hook_success", uuid="att-noise-1",
                    attachment={"name": "PostToolUse"}),
        _attachment("hook_additional_context", uuid="att-noise-2",
                    attachment={"name": "PreToolUse"}),
        _attachment("task_reminder", uuid="att-task-1",
                    attachment={"itemCount": 3, "content": ["a", "b", "c"]}),
        _attachment("skill_listing", uuid="att-skill-1",
                    attachment={"content": "- skill-a\n- skill-b",
                                "skillCount": 2, "isInitial": True}),
        _attachment("deferred_tools_delta", uuid="att-tools-1",
                    attachment={"addedNames": ["TaskCreate", "WebSearch"],
                                "removedNames": [], "readdedNames": [],
                                "pendingMcpServers": []}),
    )
    u = read_usage(path)
    assert u is not None
    kinds = [a.kind for a in u.attachments]
    assert kinds == ["task_reminder", "skill_listing", "deferred_tools_delta"]


def test_attachment_payload_preserved(tmp_path):
    """Attachment payload is normalised (snake_case aliases added) but
    original keys are preserved so callers can read either shape."""
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "text", "text": "ok"},
        ]),
        _attachment("deferred_tools_delta", uuid="att-1",
                    attachment={"addedNames": ["Foo", "Bar"]}),
    )
    u = read_usage(path)
    assert u is not None and len(u.attachments) == 1
    payload = u.attachments[0].payload
    # Both shapes are reachable
    assert payload.get("addedNames") == ["Foo", "Bar"]
    assert payload.get("added_names") == ["Foo", "Bar"]


def test_turn_total_duration_patched_from_system_entry(tmp_path):
    """`system: turn_duration` carries Claude Code's own wall-clock for
    the matched turn (every API call + tools + hooks). It lands on
    the owning turn's `turn_total_duration_ms` — the *whole prompt
    cycle*, distinct from the per-call `inference_duration_ms`."""
    path = _write(
        tmp_path,
        _user("user-1", None, timestamp="2026-04-27T12:00:00Z"),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1",
                            content=[{"type": "text", "text": "answer"}],
                            timestamp="2026-04-27T12:00:03Z"),
        {"type": "system", "subtype": "stop_hook_summary",
         "uuid": "sys-stop-1", "parentUuid": "asst-1",
         "hookInfos": [{"command": "py hook_manager Stop", "durationMs": 245}],
         "hookCount": 1, "hookErrors": []},
        {"type": "system", "subtype": "turn_duration",
         "uuid": "sys-dur-1", "parentUuid": "sys-stop-1",
         "durationMs": 33202},
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 1
    t = u.turns[0]
    assert t.turn_total_duration_ms == 33202
    # Inference latency = assistant ts − user prompt ts = 3 seconds.
    assert t.inference_duration_ms == 3000
    # Both system events surface for downstream consumers.
    kinds = sorted(e.subtype for e in u.system_events)
    assert kinds == ["stop_hook_summary", "turn_duration"]
    stop = next(e for e in u.system_events if e.subtype == "stop_hook_summary")
    assert stop.turn_uuid == "asst-1"


def test_inference_duration_uses_prior_tool_result_timestamp(tmp_path):
    """For a turn that follows a tool_result (multi-iteration prompt
    cycle), the per-call latency measures from the tool_result
    delivery, not from the original user prompt — that's when the
    next API call could actually start."""
    path = _write(
        tmp_path,
        _user("u-1", None, timestamp="2026-04-27T12:00:00Z"),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="u-1",
                            content=[
                                {"type": "tool_use", "id": "t1",
                                 "name": "Bash", "input": {}},
                            ],
                            timestamp="2026-04-27T12:00:01Z"),
        {"type": "user", "uuid": "tr-1", "parentUuid": "asst-1",
         "timestamp": "2026-04-27T12:00:05Z",
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": "t1", "content": "out"},
         ]}},
        _asst_with_content(msg_id="m2", uuid="asst-2", parent="tr-1",
                            content=[{"type": "text", "text": "done"}],
                            timestamp="2026-04-27T12:00:07Z"),
    )
    u = read_usage(path)
    assert u is not None and len(u.turns) == 2
    # First API call: user@T0 → asst@T+1s
    assert u.turns[0].inference_duration_ms == 1000
    # Second API call: tool_result@T+5s → asst@T+7s = 2s
    assert u.turns[1].inference_duration_ms == 2000


def test_durations_none_when_timestamps_missing(tmp_path):
    """A turn without a parseable prior timestamp leaves
    `inference_duration_ms` as None; same for `turn_total_duration_ms`
    when no `turn_duration` system entry exists."""
    path = _write(
        tmp_path,
        _user("u-1", None),  # no timestamp
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="u-1",
                            content=[{"type": "text", "text": "answer"}]),
    )
    u = read_usage(path)
    assert u is not None
    assert u.turns[0].inference_duration_ms is None
    assert u.turns[0].turn_total_duration_ms is None


def test_attachments_without_uuid_skipped(tmp_path):
    """An attachment without a `uuid` can't be deduped by the
    downstream span_id scheme — skip it rather than risk emitting a
    duplicate every time the transcript is re-scanned."""
    path = _write(
        tmp_path,
        _user("user-1", None),
        _asst_with_content(msg_id="m1", uuid="asst-1", parent="user-1", content=[
            {"type": "text", "text": "ok"},
        ]),
        # No uuid on this entry
        {"type": "attachment", "attachment": {"type": "task_reminder", "itemCount": 0}},
    )
    u = read_usage(path)
    assert u is not None
    assert u.attachments == ()
