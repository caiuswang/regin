#!/usr/bin/env python
"""Seed a minimal session trace that contains a `<skill_experience>` inject
span, for `scripts/verify_skill_experience_span.mjs --seed`.

Posts three spans through the real ingest path (`lib.hook_plugin.post_span`):
a `prompt` anchor, an `assistant_response`, and the `memory.recall` span an
injected `<skill_experience>` block produces (marked `source='skill_experience'`).
Self-contained so the browser verifier doesn't depend on a live, growing
session whose conversation view tail-windows the row out of reach.

Usage:  .venv/bin/python scripts/_seed_skill_experience_trace.py [trace_id]
Requires `regin serve` running (post_span POSTs to the ingest endpoint).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from lib.hook_plugin import post_span

DEFAULT_TRACE_ID = "verify-skill-experience"


def seed(trace_id: str) -> str:
    t0 = datetime.now().replace(microsecond=0)

    def iso(dt: datetime) -> str:
        return dt.isoformat()

    post_span(trace_id, "prompt", attributes={"text": "/playwright-skill"},
              span_id="prompt-verifyskill",
              start_time=iso(t0), end_time=iso(t0))
    post_span(trace_id, "assistant_response",
              attributes={"text": "Launching playwright-skill"},
              parent_id="prompt-verifyskill", span_id="resp-verifyskill",
              start_time=iso(t0 + timedelta(seconds=1)),
              end_time=iso(t0 + timedelta(seconds=1)))
    # Parentless on purpose: grafts under the prompt at serve-time, exactly
    # like the real PreToolUse/UserPromptSubmit inject span.
    post_span(trace_id, "memory.recall", attributes={
        "source": "skill_experience",
        "skill_id": "playwright-skill",
        "hit_count": 2,
        "block": ("<skill_experience>\nPast-session lessons filed under the "
                  "`playwright-skill` skill.\n- [procedure] regin WebUI E2E "
                  "auth\n</skill_experience>"),
        "hits": [
            {"id": "af21160a", "kind": "procedure",
             "title": "regin WebUI E2E auth", "scope": "global"},
            {"id": "7283edc1", "kind": "procedure",
             "title": "Drive regin live Vue state", "scope": "repo:regin"},
        ],
    }, span_id="mr-verifyskill",
       start_time=iso(t0 + timedelta(milliseconds=1500)),
       end_time=iso(t0 + timedelta(milliseconds=1500)))
    return trace_id


if __name__ == "__main__":
    tid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TRACE_ID
    print(seed(tid))
