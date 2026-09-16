"""Session spans as first-class search documents.

The body of a span is whatever a human would read on its card: prose,
thinking, the shell command and its output, the diff, the rule verdicts, a
subagent's description. The field map mirrors
`frontend/src/utils/presentModel.js` — the reader and the index must agree on
what a span *says*, or a term visible on screen would not be findable.
"""

from __future__ import annotations

import json

from lib.fulltext.docs import (
    KIND_SPAN, SearchDoc, Scored, clip_body, rank, score_doc, like_patterns,
    LIKE_ESCAPE,
)

# name -> attribute keys holding the span's prose, best first.
PROSE_KEYS: dict[str, tuple[str, ...]] = {
    "prompt": ("expanded_text", "text"),
    "assistant_response": ("text",),
    "assistant.thinking": ("thinking_text",),
    "harness.recap": ("content",),
    "task.notification": ("result", "summary"),
    "tool.failure": ("error",),
}

# Categories drive the row tag and the palette's colour accent. Same buckets
# the conversation feed's filter chips use.
_EXACT_CATEGORIES = {
    "prompt": "prompt",
    "assistant.thinking": "thinking",
    "assistant_response": "assistant",
    "rule.check": "rule",
}
# Longest prefix wins, so `tool.` can be the catch-all for every other tool.
_PREFIX_CATEGORIES = (
    ("tool.Edit", "edit"), ("tool.Write", "edit"),
    ("tool.MultiEdit", "edit"), ("tool.NotebookEdit", "edit"),
    ("tool.Bash", "shell"),
    ("tool.Task", "agent"), ("agent.", "agent"),
    ("tool.", "tool"),
)


def category_of(name: str) -> str:
    if name in _EXACT_CATEGORIES:
        return _EXACT_CATEGORIES[name]
    for prefix, category in _PREFIX_CATEGORIES:
        if name.startswith(prefix):
            return category
    return "system"


# Categories whose body is code, a diff, or terminal output: the preview
# renders those monospaced and must not reflow them.
MONO_CATEGORIES = frozenset({"shell", "edit", "tool"})


def _rule_lines(attrs: dict) -> list[str]:
    out = []
    for rule in attrs.get("applicable_rules") or []:
        if not isinstance(rule, dict):
            continue
        mark = "✗" if rule.get("violated") else "✓"
        out.append(f"{mark} {rule.get('id') or ''}  {rule.get('summary') or ''}".rstrip())
    return out


def _prose(name: str, attrs: dict) -> str:
    for key in PROSE_KEYS.get(name, ()):
        value = attrs.get(key)
        if isinstance(value, str) and value:
            return value
    if attrs.get("server_side") and isinstance(attrs.get("response_text"), str):
        return attrs["response_text"]
    if isinstance(attrs.get("user_message"), str):
        return attrs["user_message"]
    return ""


def _str(value) -> str:
    return value if isinstance(value, str) else ""


def span_body(name: str, attrs: dict) -> str:
    """Every readable field of one span, newline-joined, order preserved."""
    parts = [
        _prose(name, attrs),
        _str(attrs.get("file_path")),
        _str(attrs.get("diff")),
        _str(attrs.get("command")) or _str(attrs.get("command_preview")),
        _str(attrs.get("stdout")),
        _str(attrs.get("stderr")),
        _str(attrs.get("description")),
        _str(attrs.get("summary")),
        _str(attrs.get("pattern")),
        _str(attrs.get("rule_file")),
        "\n".join(_rule_lines(attrs)),
    ]
    return clip_body("\n".join(p for p in parts if p))


# One identifying detail per tool, so a list of nine `tool.Bash` rows is
# still scannable when the snippet below them is not the differentiator.
def _subject(attrs: dict) -> str:
    path = _str(attrs.get("file_path"))
    if path:
        return path
    command = _str(attrs.get("command")) or _str(attrs.get("command_preview"))
    if command:
        return command.strip().split("\n", 1)[0][:80]
    return _str(attrs.get("description"))[:80]


def span_sub(attrs: dict, duration_ms) -> str:
    """The one-line context under the row title."""
    bits = []
    turn = attrs.get("turn_index")
    if isinstance(turn, int):
        bits.append(f"turn #{turn}")
    if duration_ms:
        secs = duration_ms / 1000
        bits.append(f"{secs:.1f}s" if secs < 60 else f"{secs / 60:.1f}m")
    subject = _subject(attrs)
    if subject:
        bits.append(subject)
    return " · ".join(bits)


def redacted_body(attrs: dict) -> str | None:
    """A redacted span keeps its title and tag searchable but exposes no
    lines — the preview says so rather than rendering an empty pane."""
    if attrs.get("redacted") or attrs.get("content_redacted"):
        return "content redacted"
    return None


def row_to_doc(row) -> SearchDoc:
    """One `session_spans` row (span_id, name, attributes, duration_ms,
    agent_id) as a SearchDoc."""
    span_id, name, raw_attrs, duration_ms, agent_id = row
    try:
        attrs = json.loads(raw_attrs) if raw_attrs else {}
    except (ValueError, TypeError):
        attrs = {}
    if not isinstance(attrs, dict):
        attrs = {}
    category = category_of(name)
    body = redacted_body(attrs)
    if body is None:
        body = span_body(name, attrs)
    crumb_bits = []
    turn = attrs.get("turn_index")
    if isinstance(turn, int):
        crumb_bits.append(f"turn #{turn}")
    if agent_id:
        crumb_bits.append("subagent")
    crumb_bits.append(name)
    subject = _subject(attrs)
    if subject:
        crumb_bits.append(subject)
    tag = category
    if attrs.get("compacted"):
        tag = f"{category} · compacted"
    return SearchDoc(
        kind=KIND_SPAN,
        id=span_id,
        title=name,
        sub=span_sub(attrs, duration_ms),
        tag=tag,
        crumb=" · ".join(crumb_bits),
        body=body,
        mono=category in MONO_CATEGORIES,
    )


def _prefilter_clause(query: str) -> tuple[str, list]:
    """`(sql, params)` narrowing to rows that *might* match, so the scan
    parses JSON for a handful of rows instead of the whole session."""
    patterns = like_patterns(query)
    likes = " OR ".join(
        [f"attributes LIKE ? ESCAPE '{LIKE_ESCAPE}'"] * len(patterns)
    )
    name_patterns = like_patterns(query.replace("\\", ""))[:1]
    likes += f" OR name LIKE ? ESCAPE '{LIKE_ESCAPE}'"
    return f"({likes})", patterns + name_patterns


def search_session_spans(conn, trace_ids: list[str], query: str,
                         limit: int) -> list[Scored]:
    """Rank the spans of one session (plus its alias group) against `query`.

    Scoped by `trace_id`, which is indexed, so the LIKE pre-filter runs over
    one session's rows rather than the whole table.
    """
    if not trace_ids or not query:
        return []
    clause, params = _prefilter_clause(query)
    placeholders = ",".join("?" * len(trace_ids))
    sql = (
        "SELECT span_id, name, attributes, duration_ms, agent_id "
        "FROM session_spans "
        f"WHERE trace_id IN ({placeholders}) AND {clause} "
        "ORDER BY id ASC"
    )
    scored: list[Scored] = []
    seen: set[str] = set()
    for order, row in enumerate(conn.execute(sql, [*trace_ids, *params])):
        if row[0] in seen:
            continue
        seen.add(row[0])
        hit = score_doc(row_to_doc(row), query, order)
        if hit:
            scored.append(hit)
    return rank(scored)[:limit]
