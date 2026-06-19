"""Parsing helpers shared by the agentic judges.

A judge that is a tool-using CLI (Kimi prints a `{"suppressOutput": true}`
hook echo and a `To resume: …` footer around its answer; Claude `--print`
is cleaner) can wrap its JSON answer in prose. `extract_json_object` returns
the LAST top-level balanced `{…}` that parses as an object, so wrapper noise
before the real answer never corrupts the parse. String-aware brace matching
keeps braces inside JSON string values from being miscounted.
"""

from __future__ import annotations

import json


def _advance_string(ch: str, esc: bool) -> tuple[bool, bool]:
    """Consume one char already known to be inside a JSON string; return
    (still_in_string, next_escape)."""
    if esc:
        return True, False
    if ch == "\\":
        return True, True
    return ch != '"', False


def _top_level_objects(text: str) -> list[str]:
    """Every top-level balanced `{…}` substring, in order, string-aware."""
    segments: list[str] = []
    depth = 0
    start = -1
    in_str = esc = False
    for i, ch in enumerate(text):
        if in_str:
            in_str, esc = _advance_string(ch, esc)
        elif ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                segments.append(text[start:i + 1])
    return segments


def extract_json_object(text: str | None) -> dict | None:
    """The last top-level balanced object in `text` that parses to a dict, or
    None. Tolerates leading wrapper objects/prose from a noisy judge CLI."""
    if not text:
        return None
    for segment in reversed(_top_level_objects(text)):
        try:
            parsed = json.loads(segment)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


__all__ = ["extract_json_object"]
