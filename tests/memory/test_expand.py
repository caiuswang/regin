"""Tests for query expansion (`lib.memory.expand`).

The contract under test is "never worse than raw": every degradation mode
must return the original query verbatim, so swapping recall onto the
expanded query can only add latency, never break recall.
"""

from __future__ import annotations

from lib.memory.expand import expand_query


class _StubLLM:
    def __init__(self, reply):
        self._reply = reply
        self.calls = 0

    def complete(self, prompt, *, max_tokens=1024):
        self.calls += 1
        return self._reply


def test_no_llm_returns_raw():
    assert expand_query("fix the trace span bug", None) == "fix the trace span bug"


def test_blank_query_returns_blank():
    assert expand_query("   ", _StubLLM("anything long enough here")) == ""


def test_expansion_appended_to_original():
    llm = _StubLLM("Trace span reparenting and serve-time merge in lib/trace.")
    out = expand_query("fix span bug", llm)
    assert out.startswith("fix span bug. ")
    assert "reparenting" in out
    assert llm.calls == 1


def test_degenerate_expansion_falls_back_to_raw():
    # Completion adds no real lexical surface → recall on the raw query.
    raw = "fix the broken trace span reparenting logic now"
    assert expand_query(raw, _StubLLM("short")) == raw


def test_llm_failure_returns_raw():
    class _Boom:
        def complete(self, prompt, *, max_tokens=1024):
            raise RuntimeError("agent died")

    raw = "investigate the missing memory.recall span"
    assert expand_query(raw, _Boom()) == raw


def test_label_prefix_stripped():
    llm = _StubLLM("Expanded query: dark-mode theme palette inversion in the Vue frontend")
    out = expand_query("dark mode broken", llm)
    assert "Expanded query:" not in out
    assert "palette inversion" in out
