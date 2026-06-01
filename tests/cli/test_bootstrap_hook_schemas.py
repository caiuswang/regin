"""Hook-schema bootstrap inference (`regin bootstrap-hook-schemas`).

Covers the pure schema-inference step: which keys become properties, how
types are inferred, and — crucially — that `required` is always empty.
Inferring `required` from sample presence produced false `missing_required`
drift in practice (e.g. `agent_id` is on main-agent payloads but absent on
subagent ones), so the generator must never mark event fields required.
"""

from __future__ import annotations

from cli.commands import schema as schema_cmd


def _infer(payloads):
    return schema_cmd._infer_event_schema("SomeEvent", payloads)


def test_required_is_always_empty_even_for_ubiquitous_fields():
    # `agent_id` appears in 100% of the sample, but presence != mandatory.
    payloads = [
        {"session_id": "s", "transcript_path": "t", "cwd": "c",
         "hook_event_name": "SomeEvent", "agent_id": "a1"},
        {"session_id": "s", "transcript_path": "t", "cwd": "c",
         "hook_event_name": "SomeEvent", "agent_id": "a2"},
    ]
    assert _infer(payloads)["required"] == []


def test_envelope_keys_excluded_from_properties():
    payloads = [{
        "session_id": "s", "transcript_path": "t", "cwd": "c",
        "hook_event_name": "SomeEvent", "reason": "clear",
    }]
    props = _infer(payloads)["properties"]
    for envelope in ("session_id", "transcript_path", "cwd", "hook_event_name"):
        assert envelope not in props
    assert props["reason"] == {"type": "string"}


def test_types_inferred_per_json_value():
    payloads = [{
        "session_id": "s", "transcript_path": "t", "cwd": "c",
        "hook_event_name": "SomeEvent",
        "flag": True, "count": 3, "ratio": 1.5,
        "items": [1, 2], "meta": {"k": "v"}, "blank": None,
    }]
    props = _infer(payloads)["properties"]
    assert props["flag"]["type"] == "boolean"      # bool before int
    assert props["count"]["type"] == "integer"
    assert props["ratio"]["type"] == "number"
    assert props["items"]["type"] == "array"
    assert props["meta"]["type"] == "object"
    assert props["blank"]["type"] == "null"


def test_schema_is_open_for_drift_detection():
    schema = _infer([{
        "session_id": "s", "transcript_path": "t", "cwd": "c",
        "hook_event_name": "SomeEvent", "reason": "x",
    }])
    assert schema["additionalProperties"] is True
    assert schema["type"] == "object"
    assert schema["title"] == "SomeEvent hook payload"
