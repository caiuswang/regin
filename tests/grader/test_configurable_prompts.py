"""Configurable deep-judge prompts: aspect weaving + per-axis overrides.

Covers `lib/grader/prompts.py` and its wiring into the two agentic judge
prompt builders. The grounded pipelines are untouched — these tests assert
only that reviewer config (aspects + overrides) reaches the judge prompt.
"""

from __future__ import annotations

import pytest

from lib.settings import GraderAspect, settings
from lib.grader.prompts import (
    default_system_prompts, judge_system_prompt, render_aspects_block,
)


@pytest.fixture
def aspects(monkeypatch):
    """Set the grader's aspects list for one test."""
    def _set(items):
        monkeypatch.setattr(settings.grader, "aspects", items)
    return _set


@pytest.fixture
def overrides(monkeypatch):
    def _set(mapping):
        monkeypatch.setattr(settings.grader, "system_prompt_overrides", mapping)
    return _set


def test_render_block_lists_only_enabled_aspects(aspects):
    aspects([
        GraderAspect(key="correctness", label="Correctness", enabled=True,
                     description="claims backed by spans"),
        GraderAspect(key="clarity", label="Clarity", enabled=False,
                     description="readable"),
        GraderAspect(key="safety", label="Safety", enabled=True,
                     description="no destructive actions"),
    ])
    block = render_aspects_block()
    assert "<aspects>" in block and "</aspects>" in block
    assert "Correctness: claims backed by spans" in block
    assert "Safety: no destructive actions" in block
    assert "Clarity" not in block          # disabled aspect omitted


def test_render_block_empty_when_no_enabled(aspects):
    aspects([GraderAspect(key="x", label="X", enabled=False)])
    assert render_aspects_block() == ""


def test_render_block_per_run_whitelist_overrides_enabled_flags(aspects):
    # the per-run keys win over each aspect's persisted `.enabled` flag:
    # an enabled-by-config aspect is dropped, a disabled-by-config one is kept.
    aspects([
        GraderAspect(key="correctness", label="Correctness", enabled=True,
                     description="claims backed by spans"),
        GraderAspect(key="safety", label="Safety", enabled=False,
                     description="no destructive actions"),
    ])
    block = render_aspects_block(enabled_keys=["safety"])
    assert "Safety: no destructive actions" in block
    assert "Correctness" not in block


def test_render_block_empty_when_whitelist_empty(aspects):
    aspects([GraderAspect(key="safety", label="Safety", enabled=True)])
    assert render_aspects_block(enabled_keys=[]) == ""


def test_block_inserts_before_output_format(aspects, overrides):
    aspects([GraderAspect(key="safety", label="Safety", enabled=True,
                          description="no rm -rf")])
    overrides({})
    default = "<role>judge</role>\n<output_format>JSON only</output_format>"
    result = judge_system_prompt("correctness", default)
    assert result.index("<aspects>") < result.index("<output_format>")
    assert "no rm -rf" in result
    # the strict-JSON close is preserved and stays last
    assert result.rstrip().endswith("JSON only</output_format>")


def test_override_replaces_default(aspects, overrides):
    aspects([])                                   # no aspect block to confuse
    overrides({"process": "  CUSTOM PROCESS JUDGE  "})
    assert judge_system_prompt("process", "BUILTIN") == "CUSTOM PROCESS JUDGE"


def test_blank_override_falls_back_to_default(aspects, overrides):
    aspects([])
    overrides({"correctness": "   "})
    assert judge_system_prompt("correctness", "BUILTIN") == "BUILTIN"


def test_agentic_build_prompt_weaves_aspects_and_substitutes(aspects, overrides):
    aspects([GraderAspect(key="clarity", label="Clarity", enabled=True,
                          description="state conclusions plainly")])
    overrides({})
    from lib.grader.agentic import _build_prompt
    prompt = _build_prompt("trace-xyz", ".venv/bin/python")
    assert "state conclusions plainly" in prompt
    assert "trace-xyz" in prompt and "{trace_id}" not in prompt


def test_agentic_build_prompt_honors_per_run_aspect_whitelist(aspects, overrides):
    aspects([
        GraderAspect(key="clarity", label="Clarity", enabled=True,
                     description="state conclusions plainly"),
        GraderAspect(key="safety", label="Safety", enabled=True,
                     description="no destructive actions"),
    ])
    overrides({})
    from lib.grader.agentic import _build_prompt
    prompt = _build_prompt("trace-xyz", ".venv/bin/python",
                           enabled_aspects=["safety"])
    assert "no destructive actions" in prompt
    assert "state conclusions plainly" not in prompt   # not in the whitelist


def test_default_system_prompts_exposes_both_axes():
    defaults = default_system_prompts()
    assert set(defaults) == {"correctness", "process"}
    assert "<output_format>" in defaults["correctness"]
    assert "PROCESS judge" in defaults["process"]
