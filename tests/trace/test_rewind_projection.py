"""Serve-time projection of `/rewind` markers.

Verifies that `merge_spans` (→ `_graft_orphans` → `_mark_rewound_away`)
flags every span on a discarded branch and collapses the abandoned prompts
under their `rewind` marker, while leaving the live branch untouched.
"""

from __future__ import annotations

from lib.trace.merge import merge_spans


def _span(span_id, name, *, parent_id=None, turn_uuid=None, attrs=None, start="2026-06-13T17:00:00"):
    return {
        "span_id": span_id,
        "trace_id": "t1",
        "name": name,
        "kind": "internal",
        "parent_id": parent_id,
        "turn_uuid": turn_uuid,
        "start_time": start,
        "end_time": start,
        "duration_ms": 0,
        "status_code": "OK",
        "status_message": None,
        "attributes": attrs or {},
    }


def _marker(orphan_root13, orphan_keys, abandoned_prompt_keys, **extra):
    a = {
        "kind": "rewind",
        "orphan_keys": orphan_keys,
        "abandoned_prompt_keys": abandoned_prompt_keys,
        **extra,
    }
    return _span(f"rewind-{orphan_root13}", "rewind", attrs=a,
                 start="2026-06-13T17:00:05")


def test_marker_flags_branch_and_collapses_abandoned_prompts():
    # Abandoned branch: prompt AB -> response (turn AB) -> a tool span.
    ab = "aaaaaaaa-bbbb"          # orphan_root / abandoned prompt key
    ab_turn = "aaaaaaaa-bbbb-1111-2222-333333333333"
    spans = [
        _span("conversation-x", "conversation", start="2026-06-13T16:59:00"),
        _marker(ab, orphan_keys=[ab, ab_turn[:13]], abandoned_prompt_keys=[ab]),
        # abandoned spans (initially conversation roots / null parent)
        _span(f"prompt-{ab}", "prompt", start="2026-06-13T17:00:06"),
        _span(f"resp-{ab_turn[:13]}", "assistant_response",
              parent_id=f"prompt-{ab}", turn_uuid=ab_turn,
              start="2026-06-13T17:00:07"),
        _span("tool.Bash-xyz", "tool.Bash", turn_uuid=ab_turn,
              start="2026-06-13T17:00:08"),
        # live branch
        _span("prompt-cccccccc-dddd", "prompt", start="2026-06-13T17:00:20"),
    ]
    out = {s["span_id"]: s for s in merge_spans(spans)}

    # Abandoned prompt is re-parented under the marker.
    assert out[f"prompt-{ab}"]["parent_id"] == f"rewind-{ab}"
    # Every abandoned span is flagged.
    for sid in (f"prompt-{ab}", f"resp-{ab_turn[:13]}", "tool.Bash-xyz"):
        assert out[sid]["attributes"].get("rewound_away") is True
        assert out[sid]["attributes"].get("rewind_fork_id") == f"rewind-{ab}"
    # The marker itself and the live prompt are NOT flagged.
    assert "rewound_away" not in out[f"rewind-{ab}"]["attributes"]
    assert "rewound_away" not in out["prompt-cccccccc-dddd"]["attributes"]


def test_marker_is_a_top_level_boundary_not_under_a_prompt():
    ab = "aaaaaaaa-bbbb"
    spans = [
        _span("conversation-x", "conversation", start="2026-06-13T16:59:00"),
        _span("prompt-eeeeeeee-ffff", "prompt", start="2026-06-13T16:59:30"),
        _marker(ab, orphan_keys=[ab], abandoned_prompt_keys=[ab]),
        _span(f"prompt-{ab}", "prompt", start="2026-06-13T17:00:06"),
    ]
    out = {s["span_id"]: s for s in merge_spans(spans)}
    # Boundary grafts to the conversation root, never under a prompt.
    assert out[f"rewind-{ab}"]["parent_id"] == "conversation-x"


def test_no_marker_is_a_noop():
    spans = [
        _span("prompt-aaaaaaaa-bbbb", "prompt"),
        _span("resp-aaaaaaaa-bbbb", "assistant_response",
              parent_id="prompt-aaaaaaaa-bbbb",
              turn_uuid="aaaaaaaa-bbbb-1111-2222-333333333333"),
    ]
    out = merge_spans(spans)
    assert all("rewound_away" not in (s.get("attributes") or {}) for s in out)
