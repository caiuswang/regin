"""Hook-event drift validation — `validate_event` + the hook envelope.

These exercise the hook_event subject_kind path added alongside the tool
path. They isolate schemas under a tmp overlay dir (via the settings
singleton) and use event names with NO committed baseline, so
`_load_schema` returns exactly the controlled overlay — the assertions
don't depend on the repo's generated `_hooks/*.schema.json` shapes.
"""

from __future__ import annotations

import json

import pytest

from lib import settings as _s
from lib.trace import payload_validation as pv


# An event name that must never have a committed baseline, so tests own
# its schema entirely through the overlay.
_TEST_EVENT = "XyzUnitTestEvent"


def _write_overlay_schema(overlay_root, event: str, properties: dict) -> None:
    path = overlay_root / "claude" / "_hooks" / f"{event}.schema.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": True,
        "properties": properties,
        "required": [],
    }))


@pytest.fixture
def overlay_schema(tmp_path, monkeypatch):
    """Point the overlay dir at tmp_path and hand back a writer. Clears
    the lru_cache before and after so cases don't bleed into each other."""
    monkeypatch.setattr(_s.settings, "payload_schemas_overlay_dir", str(tmp_path))
    pv._load_schema.cache_clear()
    yield lambda event, props: _write_overlay_schema(tmp_path, event, props)
    pv._load_schema.cache_clear()


def _envelope(event: str = _TEST_EVENT) -> dict:
    return {
        "hook_event_name": event,
        "session_id": "s-1",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": "/repo",
    }


def test_validate_event_ignores_non_dict_and_empty_event():
    assert pv.validate_event("Stop", None) == []
    assert pv.validate_event(None, {"hook_event_name": "Stop"}) == []


def test_unseen_event_yields_single_unknown_event_finding():
    # No baseline, no overlay -> the hook analog of unknown_tool.
    findings = pv.validate_event("NoSuchEvent_zzz", _envelope("NoSuchEvent_zzz"))
    assert len(findings) == 1
    f = findings[0]
    assert f.drift_kind == "unknown_event"
    assert f.subject_kind == "hook_event"
    assert f.field_path == "(root)"
    assert f.tool_name == "NoSuchEvent_zzz"


def test_envelope_keys_never_flagged(overlay_schema):
    # The four envelope keys are known implicitly. Declare a real property
    # so the schema isn't an opaque object (an empty `properties` would
    # make the walker skip descent and trivially pass).
    overlay_schema(_TEST_EVENT, {"reason": {"type": "string"}})
    payload = {**_envelope(), "reason": "clear"}
    assert pv.validate_event(_TEST_EVENT, payload) == []


def test_clean_payload_with_declared_field(overlay_schema):
    overlay_schema(_TEST_EVENT, {"reason": {"type": "string"}})
    payload = {**_envelope(), "reason": "clear"}
    assert pv.validate_event(_TEST_EVENT, payload) == []


def test_unknown_top_level_field_flagged(overlay_schema):
    overlay_schema(_TEST_EVENT, {"reason": {"type": "string"}})
    payload = {**_envelope(), "reason": "clear", "surprise": 42}
    findings = pv.validate_event(_TEST_EVENT, payload)
    kinds = {(f.drift_kind, f.field_path) for f in findings}
    assert ("unknown_field", "surprise") in kinds
    assert all(f.subject_kind == "hook_event" for f in findings)


def test_permission_mode_is_not_a_hook_envelope_key(overlay_schema):
    """Regression guard for the envelope split: permission_mode is part of
    the TOOL envelope but is NOT universal across hook events, so for a
    hook event that doesn't declare it, it must surface as drift rather
    than be silently swallowed like on the PostToolUse path."""
    overlay_schema(_TEST_EVENT, {"reason": {"type": "string"}})
    payload = {**_envelope(), "reason": "clear", "permission_mode": "default"}
    findings = pv.validate_event(_TEST_EVENT, payload)
    assert any(
        f.drift_kind == "unknown_field" and f.field_path == "permission_mode"
        for f in findings
    )


def test_tool_path_envelope_unaffected():
    """The tool validator still treats permission_mode as a known envelope
    key — the split must not regress the PostToolUse behavior."""
    assert "permission_mode" in pv._ENVELOPE_KEYS
    assert "permission_mode" not in pv._HOOK_COMMON_KEYS
