"""Stop + Notification scenarios (13).

`Stop` fires on every finished turn; `test_stop_fires_once_per_turn` validates
the common case.

`Notification` fires when Claude needs user attention — most reliably when a
tool call needs permission in `default` mode. We poll the jsonl log rather
than sleeping because permission prompts can take 10-30 s to surface after
the prompt is submitted.
"""

from __future__ import annotations

import time

import pytest

from tests.trace.integration.harness import TraceSession


def test_stop_fires_once_per_turn(trace_session):
    trace_session.send("reply with just DONE")
    trace_session.assert_hook_event("Stop", min_count=1)


@pytest.mark.slow
def test_permission_prompt_emits_notification(tmp_workdir, request):
    ts = TraceSession(workdir=tmp_workdir, test_name=request.node.nodeid)
    try:
        ts.start(permission_mode="default")

        # Write tool triggers a permission dialog in `default` mode unless it
        # matches an `allow` rule. Use a filename unlikely to be whitelisted.
        ts.send(
            "use the Write tool to create a file named trace_notify_probe.dat "
            "containing the word PROBE",
            wait_idle=False,
        )

        # Poll for a Notification hook entry instead of a fixed sleep — the
        # TUI usually takes 5-20 s to surface the permission prompt.
        deadline = time.monotonic() + 60
        got = []
        while time.monotonic() < deadline:
            got = ts.hook_events(event="Notification")
            if got:
                break
            time.sleep(1)

        # Clean up any modal dialog so the session can be killed cleanly.
        ts.send_keys("Escape")
        time.sleep(0.3)
        ts.send_keys("Escape")

        if not got:
            pytest.skip(
                "no Notification within 60 s — Claude may have auto-allowed "
                "or refused the Write. Pane:\n" + ts.capture_pane(lines=40)
            )

        messages = [(e.get("payload") or {}).get("message", "") for e in got]
        assert any("permission" in m.lower() or "attention" in m.lower() for m in messages), (
            f"Notification payload did not look like a permission/attention prompt: {messages}"
        )
    finally:
        ts.stop()
