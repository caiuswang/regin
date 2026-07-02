"""Stage 2 bridge: the deep-grader judge prompts through the editable surface.

The safety bar for wiring `judge_system_prompt` to read an editable DB skeleton
is that NOTHING about the existing override + aspects + single-brace-token
behavior changes until a reviewer actually edits the skeleton. These tests pin:

(a) with no DB row and no settings override, the result is BYTE-IDENTICAL to the
    frozen pre-change logic (`_legacy_judge_system_prompt` below);
(b) a legacy `settings` override still wins over the built-in default when no
    edited DB row exists;
(c) an edited DB skeleton becomes the base and its `{trace_id}` token is still
    substituted by grader code (DB wins over settings, too);
(d) the `{trace_id}`/`{python}` single-brace tokens are never mangled by the
    surface/engine layer (they are not declared as `{{variables}}`).

`_legacy_judge_system_prompt` is a frozen copy of the resolver BEFORE this
stage (settings-override → default, then substitute, then splice aspects). If
(a) ever diverges from it, the bridge changed default behavior — a regression.
"""

from __future__ import annotations

import pytest

from lib.grader.prompts import judge_system_prompt, render_aspects_block
from lib.prompt_templates import seed_builtin_skeletons, update_template
from lib.prompts import render_surface
from lib.prompts.surfaces.grader import (
    CORRECTNESS_SURFACE_ID,
    PROCESS_SURFACE_ID,
)
from lib.settings import GraderAspect, settings

_OUTPUT_MARKER = "<output_format>"

# A base that exercises both the substitution loop and the aspects splice.
_DEFAULT = (
    "<role>correctness judge</role>\n"
    "<session_id>{trace_id}</session_id>\n"
    "Run `{python} cli/regin.py trace dump {trace_id}`.\n"
    "<output_format>JSON only</output_format>"
)


def _legacy_judge_system_prompt(axis, default, *, substitutions=None,
                                enabled_aspects=None):
    """Frozen copy of the pre-Stage-2 resolver: settings-override-or-default,
    substitute, then splice aspects before <output_format>."""
    overrides = settings.grader.system_prompt_overrides or {}
    override = overrides.get(axis)
    base = override.strip() if isinstance(override, str) and override.strip() else default
    for token, value in (substitutions or {}).items():
        base = base.replace(token, value)
    block = render_aspects_block(enabled_aspects)
    if not block:
        return base
    if _OUTPUT_MARKER in base:
        return base.replace(_OUTPUT_MARKER, f"{block}\n\n{_OUTPUT_MARKER}", 1)
    return f"{base}\n\n{block}"


@pytest.fixture
def aspects(monkeypatch):
    def _set(items):
        monkeypatch.setattr(settings.grader, "aspects", items)
    return _set


@pytest.fixture
def overrides(monkeypatch):
    def _set(mapping):
        monkeypatch.setattr(settings.grader, "system_prompt_overrides", mapping)
    return _set


# ── (a) byte-identical to legacy when unconfigured ──────────────────

def test_no_row_no_override_matches_legacy_plain(aspects, overrides):
    aspects([])
    overrides({})
    got = judge_system_prompt("correctness", _DEFAULT)
    assert got == _legacy_judge_system_prompt("correctness", _DEFAULT)
    assert got == _DEFAULT  # nothing changed the base


def test_no_row_no_override_matches_legacy_with_subs_and_aspects(aspects, overrides):
    aspects([GraderAspect(key="safety", label="Safety", enabled=True,
                          description="no rm -rf")])
    overrides({})
    subs = {"{trace_id}": "T-123", "{python}": ".venv/bin/python"}
    got = judge_system_prompt("correctness", _DEFAULT, substitutions=subs)
    exp = _legacy_judge_system_prompt("correctness", _DEFAULT, substitutions=subs)
    assert got == exp
    assert "T-123" in got and "{trace_id}" not in got
    assert got.index("<aspects>") < got.index(_OUTPUT_MARKER)


def test_seeded_unedited_row_still_matches_legacy(aspects, overrides):
    # A seeded skeleton that equals the built-in default must NOT change
    # behavior — the whole safety premise of the dual-read.
    seed_builtin_skeletons()
    aspects([])
    overrides({})
    from lib.grader.agentic import _PROMPT as correctness_default
    got = judge_system_prompt("correctness", correctness_default)
    assert got == _legacy_judge_system_prompt("correctness", correctness_default)
    assert got == correctness_default


# ── (b) legacy settings override still wins when no edited DB row ────

def test_settings_override_wins_without_db_row(aspects, overrides):
    aspects([])
    overrides({"correctness": "  LEGACY {trace_id}  "})
    got = judge_system_prompt("correctness", _DEFAULT,
                              substitutions={"{trace_id}": "T"})
    assert got == "LEGACY T"


def test_seeded_unedited_row_does_not_shadow_settings_override(aspects, overrides):
    # Row exists but equals default → treated as unedited → settings wins.
    seed_builtin_skeletons()
    aspects([])
    overrides({"process": "LEGACY PROCESS"})
    from lib.grader.process_agentic import _PROMPT as process_default
    assert judge_system_prompt("process", process_default) == "LEGACY PROCESS"


# ── (c) edited DB skeleton becomes the base; tokens still fill ───────

def test_edited_skeleton_is_used_and_token_substituted(aspects, overrides):
    seed_builtin_skeletons()
    update_template(CORRECTNESS_SURFACE_ID, {"body": "X {trace_id}"})
    aspects([])
    overrides({})
    got = judge_system_prompt("correctness", _DEFAULT,
                              substitutions={"{trace_id}": "T-9"})
    assert got == "X T-9"


def test_edited_skeleton_wins_over_settings_override(aspects, overrides):
    seed_builtin_skeletons()
    update_template(CORRECTNESS_SURFACE_ID, {"body": "DB-BASE {trace_id}"})
    aspects([])
    overrides({"correctness": "SETTINGS-BASE"})
    got = judge_system_prompt("correctness", _DEFAULT,
                              substitutions={"{trace_id}": "T"})
    assert got == "DB-BASE T"


def test_edited_skeleton_still_gets_aspects_splice(aspects, overrides):
    seed_builtin_skeletons()
    update_template(CORRECTNESS_SURFACE_ID,
                    {"body": "BASE\n<output_format>JSON</output_format>"})
    aspects([GraderAspect(key="safety", label="Safety", enabled=True,
                          description="no destructive actions")])
    overrides({})
    got = judge_system_prompt("correctness", _DEFAULT)
    assert "no destructive actions" in got
    assert got.index("<aspects>") < got.index(_OUTPUT_MARKER)


def test_process_axis_edited_skeleton(aspects, overrides):
    seed_builtin_skeletons()
    update_template(PROCESS_SURFACE_ID, {"body": "PROC {python}"})
    aspects([])
    overrides({})
    got = judge_system_prompt("process", _DEFAULT,
                              substitutions={"{python}": "py"})
    assert got == "PROC py"


# ── (d) single-brace tokens are never mangled by the surface layer ──

def test_seeded_skeleton_body_keeps_single_brace_tokens():
    # Seeding copies the built-in default verbatim, tokens and all.
    seed_builtin_skeletons()
    from lib.prompt_templates import get_template_by_slug
    body = get_template_by_slug(CORRECTNESS_SURFACE_ID)["body"]
    assert "{trace_id}" in body
    assert "{python}" in body
    assert "{{trace_id}}" not in body  # not doubled into an engine variable


def test_render_surface_passes_single_brace_tokens_through():
    # The engine only treats {{double}} as a slot; {trace_id}/{python} survive.
    out = render_surface(CORRECTNESS_SURFACE_ID, {})
    assert "{trace_id}" in out
    assert "{python}" in out


def test_edited_skeleton_with_literal_single_brace_not_mangled(aspects, overrides):
    # A reviewer edit keeping the tokens: they must survive verbatim, then the
    # grader's own replace() fills only the tokens it was handed.
    seed_builtin_skeletons()
    update_template(CORRECTNESS_SURFACE_ID,
                    {"body": "id={trace_id} py={python} literal={x}"})
    aspects([])
    overrides({})
    got = judge_system_prompt("correctness", _DEFAULT,
                              substitutions={"{trace_id}": "T"})
    # {trace_id} filled; {python} untouched (no sub given); {x} left literal.
    assert got == "id=T py={python} literal={x}"
