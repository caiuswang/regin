"""Unit tests for the prompt substitution engine (lib/prompts/engine.py)."""

from __future__ import annotations

import pytest

from lib.prompts.engine import (
    IncludeCycle,
    MissingInclude,
    UnknownVariable,
    includes_in,
    render,
    variables_in,
)


def test_substitutes_double_brace_variable():
    assert render("Hi {{name}}!", {"name": "Ada"}) == "Hi Ada!"


def test_single_braces_and_single_brace_tokens_pass_through():
    # literal JSON and the grader's {trace_id}/{python} tokens must survive
    body = 'JSON {"id": 1, "x": [{"a": 2}]} and {trace_id} token, var {{v}}'
    assert render(body, {"v": "Z"}) == 'JSON {"id": 1, "x": [{"a": 2}]} and {trace_id} token, var Z'


def test_whitespace_inside_placeholder_tolerated():
    assert render("{{  name  }}", {"name": "x"}) == "x"


def test_missing_variable_raises():
    with pytest.raises(UnknownVariable):
        render("{{nope}}", {})


def test_value_is_string_coerced():
    assert render("n={{n}}", {"n": 7}) == "n=7"


def test_include_expands_and_renders_recursively():
    loader = {"frag": "FRAG({{name}})"}.get
    assert render("a {{include:frag}} b", {"name": "Q"}, include_loader=loader) == "a FRAG(Q) b"


def test_missing_include_raises():
    with pytest.raises(MissingInclude):
        render("{{include:ghost}}", {}, include_loader=lambda _s: None)


def test_include_without_loader_raises():
    with pytest.raises(MissingInclude):
        render("{{include:x}}", {})


def test_include_cycle_detected():
    loader = {"a": "{{include:b}}", "b": "{{include:a}}"}.get
    with pytest.raises(IncludeCycle):
        render("{{include:a}}", {}, include_loader=loader)


def test_variables_in_excludes_includes_and_dedups():
    assert variables_in("{{a}} {{include:frag}} {{a}} {{b}}") == ["a", "b"]


def test_includes_in_lists_slugs():
    assert includes_in("{{include:one}} {{x}} {{include:two}} {{include:one}}") == ["one", "two"]


def test_empty_template_is_empty():
    assert render("", {"a": 1}) == ""
    assert render(None, {}) == ""
