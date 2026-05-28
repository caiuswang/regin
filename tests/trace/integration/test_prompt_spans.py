"""Scenario 1: prompt span carries the submitted text attribute."""

from __future__ import annotations


def test_prompt_span_carries_text_attribute(trace_session):
    sentinel = "echo-back-trace-test-9182"
    trace_session.send(f"reply with just {sentinel}")

    spans = trace_session.assert_span("prompt", count=1)
    text = (spans[0].get("attributes") or {}).get("text", "")
    assert sentinel in text, f"prompt span.text missing sentinel: {text!r}"
