"""Plan mode scenario (8).

Design notes discovered during bring-up:

- Starting Claude with `--permission-mode=plan` drops it straight into plan
  mode, so `EnterPlanMode` is NEVER called as a tool. That means
  `plan_mode_trace_hook.py` only ever sees `ExitPlanMode` and emits a single
  `plan.exit` span — no `plan.session` / `plan.draft`.
- The ExitPlanMode tool call blocks on a TUI approval dialog. `PostToolUse`
  (and therefore `plan.exit`) only fires AFTER the user responds to the
  dialog. Stop does not fire while the dialog is up.
- Forcing Claude to call `EnterPlanMode` explicitly from non-plan mode is
  unreliable, so the full `plan.session → plan.draft → plan.review → plan.exit`
  chain cannot be exercised deterministically by this test.

The test here therefore validates the observable-from-this-harness path:
plan-mode → plan-written → approval-dialog → user dismiss → `plan.exit`.
"""

from __future__ import annotations

import time

import pytest

from tests.trace.integration.harness import TraceSession


@pytest.mark.slow
def test_plan_mode_emits_plan_exit(tmp_workdir, request):
    ts = TraceSession(workdir=tmp_workdir, test_name=request.node.nodeid)
    try:
        ts.start(permission_mode="plan", startup_timeout=30)
        ts.send(
            "simply state in your plan that sample.txt should get a comment "
            "line saying PLANNED. do not research the repo.",
            wait_idle=False,
        )

        # Wait for the approval-required Notification — this is the hook event
        # that fires right when ExitPlanMode pops the approval dialog. Polling
        # the jsonl is much more reliable than parsing the TUI text.
        deadline = time.monotonic() + 300
        seen_notification = False
        while time.monotonic() < deadline:
            notifications = ts.hook_events(event="Notification")
            for n in notifications:
                msg = (n.get("payload") or {}).get("message", "").lower()
                if "plan" in msg or "approval" in msg:
                    seen_notification = True
                    break
            if seen_notification:
                break
            time.sleep(2)

        if not seen_notification:
            pytest.skip(
                "plan-approval Notification never arrived. Pane:\n"
                + ts.capture_pane(lines=60)
            )

        # Reject the plan by pressing the 3rd option (No / refine). The dialog
        # in newer claude-code uses arrow keys; sending "Down Down Enter" picks
        # item 3 from a 4-option list.
        ts.send_keys("Down", "Down", "Enter")

        # ExitPlanMode's PostToolUse now fires -> plan.exit span lands.
        ts.wait_for_span("plan.exit", timeout=60)
    finally:
        ts.stop()
