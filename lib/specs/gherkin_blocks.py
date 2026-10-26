"""Acceptance fences → scenarios, through gherkin-official's own parser.

A spec's ```gherkin fence usually has no `Feature:` line, which the parser
requires, so one is added above it and subtracted again when a location is
turned into a spec line. Steps written straight under that synthetic
feature, with no `Scenario:` title, parse cleanly into the feature's
description and yield zero scenarios — so zero is reported, never passed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from gherkin.errors import CompositeParserException, ParserError
from gherkin.parser import Parser
from gherkin.token_scanner import TokenScanner

from lib.specs.markdown import Fence
from lib.specs.problems import Problem

_SYNTHETIC_FEATURE = "Feature: _\n"


@dataclass(frozen=True)
class Scenario:
    title: str
    test_name: Optional[str]
    #: 1-based line in the spec.
    line: int
    keyword: str


@dataclass(frozen=True)
class RawScenario:
    title: str
    line: int
    keyword: str


def _has_feature_line(content: str) -> bool:
    return any(line.strip().startswith("Feature:")
               for line in content.splitlines())


def _scenario_nodes(children: Iterable[dict]) -> Iterable[dict]:
    for child in children:
        if "scenario" in child:
            yield child["scenario"]
        elif "rule" in child:
            yield from _scenario_nodes(child["rule"].get("children", ()))


def _errors(exc: ParserError) -> list[ParserError]:
    if isinstance(exc, CompositeParserException):
        return list(exc.errors)
    return [exc]


def _location_line(error: ParserError) -> Optional[int]:
    location = getattr(error, "location", None) or {}
    return location.get("line")


def read_fence(fence: Fence) -> tuple[list[RawScenario], list[Problem]]:
    """The scenarios one gherkin fence holds, or what stopped it parsing."""
    offset = 0 if _has_feature_line(fence.content) else 1
    source = ("" if offset == 0 else _SYNTHETIC_FEATURE) + fence.content

    def spec_line(gherkin_line: Optional[int]) -> int:
        if gherkin_line is None:
            return fence.start + 1
        return fence.start + 1 + gherkin_line - offset

    try:
        document = Parser().parse(TokenScanner(source))
    except ParserError as exc:
        return [], [Problem(
            "gherkin_syntax", spec_line(_location_line(error)), "gherkin",
            str(error).split("): ", 1)[-1])
            for error in _errors(exc)]
    feature = document.get("feature") or {}
    scenarios = [
        RawScenario(node.get("name", "").strip(),
                    spec_line(node["location"]["line"]),
                    node.get("keyword", "Scenario").strip())
        for node in _scenario_nodes(feature.get("children", ()))]
    if not scenarios:
        return [], [Problem(
            "untitled_block", fence.start + 1, "gherkin",
            "this gherkin block has no Scenario: — steps outside a titled "
            "scenario are read as description and name no test")]
    return scenarios, []
