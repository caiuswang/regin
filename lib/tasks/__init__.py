"""Task capture — getting an idea into regin from anywhere.

A task is an idea first: captured rows always land with `status='idea'` and
carry no execution parameter, so the worst a leaked capture key can do is add
a backlog row. Arming and execution are a later, user-authenticated surface.

  * `capture.py`   — parse/validate a capture payload (unknown fields are a
                     loud rejection, never a silent drop)
  * `store.py`     — capture, edits and arming; replay is resolved by the
                     `(source, external_id)` unique constraint
  * `arm.py`       — the execution-parameter allowlist, mirroring
                     `supervisor.launch_run`'s signature
  * `deps.py`      — the task DAG; cycles are rejected at write time
  * `runs.py`      — the task ↔ run join: launch, settle, fire dependents. It
                     delegates to `lib/agent_sdk` and owns no lifecycle
  * `ratelimit.py` — per-API-key sliding window for the HTTP capture route

The local CLI (`regin task …`) goes straight through `store`, not over HTTP.
"""

from __future__ import annotations

from lib.tasks.capture import CaptureRejected, parse_capture

__all__ = ["CaptureRejected", "parse_capture"]
