"""Capture Claude Code dynamic-workflow runs as regin session/span trees.

A *dynamic workflow* (the Claude Code `Workflow` tool) runs many subagents
in a background runtime across named *phases*. Its agents never reach
regin's hooks, but the runtime persists the whole run on disk. This module
reads those artifacts and projects each run onto regin's existing
OTel-style span store (``sessions`` + ``session_spans``) with **zero new
schema**, so a workflow run renders in the normal trace UI as
``run -> phase -> agent -> turn``.

On-disk layout, per run ``wf_<id>`` inside a Claude session dir ``<S>``::

    <S>/workflows/wf_<id>.json                 run manifest (written at completion)
    <S>/workflows/scripts/<name>-wf_<id>.js    the script (written at start)
    <S>/subagents/workflows/wf_<id>/
        journal.jsonl                          started/result events (written live)
        agent-<agentId>.jsonl  + .meta.json    each agent's transcript

The manifest is the runtime's authoritative per-iteration snapshot, written at
pause and at completion (not continuously while running, and not at start). It
carries the canonical agent set with phases / labels / states — and crucially
de-duplicates pause→resume *iterations*, where the journal (an append log)
keeps the dead agents of superseded iterations. So capture prefers the
manifest whenever it exists; the journal-only tree is the fallback for a run
that has never paused. See `ingest_run` / `RunRef.terminal`:

* **manifest exists** -> `build_full_spans`: the full ``phase -> agent -> turn``
  tree (real phases, labels, per-agent state, deep per-turn / per-tool spans).
  A live ``running`` snapshot stays open (no ``session.end``); ``completed`` /
  paused ``killed`` close it. A running run's snapshot can lag the newest
  agents (it refreshes only at pause/completion), but that's less wrong than
  the journal's cross-iteration over-count.
* **no manifest yet** (never paused) -> `build_flat_spans`: a flat run + agent
  list keyed off the journal, status ``running``, agents deep-expanded (so a
  live run streams agent work) and — when the script declares them statically
  — grouped under script-derived phases.

Span ids are deterministic (``wfrun-`` / ``wfphase-`` / ``wfagent-`` /
``wfturn-`` / ``wftool-``), so re-ingesting a run is idempotent. `reingest`
clears a run's rows before re-inserting, so the flat tree is cleanly replaced
by the manifest tree once the first snapshot is written.
"""

from __future__ import annotations

import difflib
import glob
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.activity_log import get_activity_logger
from lib.providers import get_active_provider

log = get_activity_logger("trace_ingest")

_WF_PREFIX = "wf_"
_PREVIEW_MAX = 240
# read_usage stores per-turn response text; we only need token + tool
# structure, so cap stored text small to bound memory on big transcripts.
_TURN_TEXT_CAP = 4000


@dataclass(frozen=True)
class RunRef:
    """Filesystem handle to one workflow run, live or completed."""

    run_id: str
    session_dir: Path
    journal_path: Path
    agents_dir: Path
    manifest_path: Path
    script_path: Path | None

    def _manifest_mtime(self) -> float | None:
        try:
            return self.manifest_path.stat().st_mtime
        except OSError:
            return None

    def _activity_mtime(self) -> float:
        """Newest mtime across the journal + every agent transcript — the run's
        live edge. Advances as agents stream output (the journal only ticks on
        start/result events) and, crucially, when a paused run is resumed."""
        mtimes = [0.0]
        for p in (self.journal_path, *self.agents_dir.glob("agent-*.jsonl")):
            try:
                mtimes.append(p.stat().st_mtime)
            except OSError:
                continue
        return max(mtimes)

    def snapshot_stale_since(self) -> float | None:
        """mtime of the manifest when it's a *stale* snapshot, else None.

        The manifest is the runtime's progress snapshot, flushed only at
        pause/completion. While a non-``completed`` run keeps progressing (a
        resume that started agents the manifest doesn't list), the rendered tree
        is frozen at that snapshot — phases/counts lag reality. We can't refresh
        it from disk (resume re-issues agents under new ids the snapshot can't
        be reconciled with), so the UI flags it as "snapshot as of <mtime>"
        instead of silently looking current. None when the snapshot still covers
        the live agents (just paused) or the run is ``completed``."""
        manifest = _read_json(self.manifest_path)
        if manifest is None or manifest.get("status") == "completed":
            return None
        _, agent_ids = _manifest_agents(manifest)
        known = set(agent_ids)
        started, _ = _journal_agents(_read_jsonl(self.journal_path))
        if all(a in known for a in started):
            return None                  # snapshot covers the live agents → current
        return self._manifest_mtime()

    @property
    def terminal(self) -> bool:
        """True once a manifest exists — render from it rather than the journal.

        The manifest is the runtime's authoritative per-iteration snapshot,
        written at pause and at completion, carrying the canonical agent set
        with phases / states. It's preferred over the journal whenever it
        exists because the journal is an append log that accumulates *dead*
        agents across pause→resume iterations (re-dispatched work) — a
        journal-driven tree over-counts (e.g. 183 logged vs 100 canonical). The
        journal-only flat tree is the fallback for a run with no manifest yet
        (never paused): a single iteration, so no over-count, and it streams
        live agent work the frozen manifest can't.

        A running run's manifest lags the newest agents (refreshed only at
        pause/completion), but lagging is less wrong than the journal's
        cross-iteration over-count, and `state_mtime` keeps the watcher
        re-ingesting so the tree refreshes the moment the manifest is rewritten.
        """
        return _manifest_status(self.manifest_path) is not None

    def state_mtime(self) -> float:
        """mtime driving the watcher's re-ingest gate. A ``completed`` run is
        stable (manifest mtime); any other state tracks the live edge across the
        manifest + journal + transcripts, so the watcher re-ingests as the
        manifest is rewritten (pause/resume) and as a no-manifest run streams."""
        if _manifest_status(self.manifest_path) == "completed":
            return self._manifest_mtime() or 0.0
        return max(self._manifest_mtime() or 0.0, self._activity_mtime())


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def discover_runs(projects_dir: str | os.PathLike | None = None) -> list[RunRef]:
    """Find every workflow run under the provider's transcript projects dir.

    Anchored on ``journal.jsonl`` (written at run start) so live and
    completed runs are both found. Reuses
    `lib.providers.base.AgentProvider.transcript_projects_dir`.
    """
    if projects_dir is None:
        projects_dir = str(get_active_provider().transcript_projects_dir())
    # projects/<project>/<session>/subagents/workflows/<run_id>/journal.jsonl
    pattern = os.path.join(
        str(projects_dir), "*", "*", "subagents", "workflows",
        f"{_WF_PREFIX}*", "journal.jsonl",
    )
    refs: list[RunRef] = []
    for journal in glob.glob(pattern):
        agents_dir = Path(journal).parent
        run_id = agents_dir.name
        # <S>/subagents/workflows/<run_id> -> <S>
        session_dir = agents_dir.parent.parent.parent
        manifest_path = session_dir / "workflows" / f"{run_id}.json"
        scripts = glob.glob(
            str(session_dir / "workflows" / "scripts" / f"*-{run_id}.js")
        )
        refs.append(RunRef(
            run_id=run_id,
            session_dir=session_dir,
            journal_path=Path(journal),
            agents_dir=agents_dir,
            manifest_path=manifest_path,
            script_path=Path(scripts[0]) if scripts else None,
        ))
    return refs


# --------------------------------------------------------------------------
# small parse / format helpers
# --------------------------------------------------------------------------

def _iso(ms: int | float | None) -> str | None:
    """Epoch milliseconds -> UTC ISO 8601 string (None-safe)."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _manifest_status(path: Path) -> str | None:
    """The manifest's ``status`` (``completed`` / ``killed`` / …), or None when
    the manifest is absent or unreadable. Used to decide whether a run is
    genuinely finished vs paused-and-resumable — see `RunRef.terminal`."""
    manifest = _read_json(path)
    if manifest is None:
        return None
    status = manifest.get("status")
    return status if isinstance(status, str) else "completed"


def _iter_jsonl(path: Path):
    """Yield parsed entries lazily — lets head-only readers (e.g.
    `_agent_full_prompt`) stop after the first hit instead of parsing a whole
    streaming transcript on every live re-ingest poll."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def _read_jsonl(path: Path) -> list[dict]:
    return list(_iter_jsonl(path))


def _preview(value: object) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value)
    return text[:_PREVIEW_MAX]


# meta-extraction cache, keyed by (path, mtime_ns, size) so repeated live
# re-ingests of the same run don't re-parse the script. Bounded by the number
# of distinct workflow scripts seen in a process — small.
_META_CACHE: dict[tuple, dict] = {}

# Lazily-built (parser, compiled meta query); see `_load_script_meta`.
_TS = None

# Matches `const meta = { ... }`: the `#eq?` predicate isolates the `meta`
# declarator (not a `metadata` decoy or another object-valued const), so the
# query returns the object node directly — no manual tree walk.
_META_QUERY = (
    "(variable_declarator name: (identifier) @name value: (object) @obj "
    "(#eq? @name \"meta\"))"
)


def _ts():
    """The shared (parser, compiled meta query), built on first use. Raises
    ImportError if the grammar isn't installed — `_load_script_meta` degrades
    to {}."""
    global _TS
    if _TS is None:
        import tree_sitter as ts
        import tree_sitter_javascript as tsjs
        lang = ts.Language(tsjs.language())
        _TS = (ts.Parser(lang), ts.Query(lang, _META_QUERY))
    return _TS


def _ts_text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _ts_string(node, src: bytes) -> str:
    """A string / template-literal value: literal fragments concatenated with
    decoded escape sequences. The grammar exposes each escape as its own node,
    so there's no hand-rolled un-escaping; non-ASCII literal text decodes as
    UTF-8 while only the ASCII escape nodes go through ``unicode_escape``."""
    parts: list[str] = []
    for ch in node.named_children:
        if ch.type == "escape_sequence":
            parts.append(_ts_text(ch, src).encode().decode("unicode_escape"))
        elif ch.type == "string_fragment":
            parts.append(_ts_text(ch, src))
    return "".join(parts)


def _ts_number(node, src: bytes):
    s = _ts_text(node, src)
    return float(s) if ("." in s or "e" in s.lower()) else int(s)


def _ts_array(node, src: bytes) -> list:
    return [_ts_value(ch, src) for ch in node.named_children if ch.type != "comment"]


def _ts_object(node, src: bytes) -> dict:
    out: dict = {}
    for ch in node.named_children:
        if ch.type != "pair":
            continue
        key, val = ch.child_by_field_name("key"), ch.child_by_field_name("value")
        if key is None or val is None:
            continue
        name = _ts_string(key, src) if key.type == "string" else _ts_text(key, src)
        out[name] = _ts_value(val, src)
    return out


# Literal node.type -> handler(node, src); anything unrecognised falls back to
# its raw source text. Defined after the handlers it references.
_TS_HANDLERS = {
    "object": _ts_object, "array": _ts_array,
    "string": _ts_string, "template_string": _ts_string, "number": _ts_number,
    "true": lambda n, s: True, "false": lambda n, s: False,
    "null": lambda n, s: None, "undefined": lambda n, s: None,
}


def _ts_value(node, src: bytes):
    """Convert a pure-literal JS value node to its Python value."""
    if node.type == "unary_expression":   # e.g. a negative number `-1`
        inner = _ts_value(node.named_children[0], src)
        return -inner if _ts_text(node, src).lstrip().startswith("-") else inner
    handler = _TS_HANDLERS.get(node.type)
    return handler(node, src) if handler else _ts_text(node, src)


def _find_meta_object(root, query):
    """The ``const meta = {...}`` object node, located by the tree-sitter
    ``query`` (with its ``#eq?`` predicate) — no manual tree walk. A real
    query, so a comment, a ``metadata`` decoy, or braces in strings can't
    match it the way a regex / hand-rolled scan could."""
    import tree_sitter as ts
    objs = ts.QueryCursor(query).captures(root).get("obj", [])
    return objs[0] if objs else None


def _load_script_meta(script_path: Path | None) -> dict:
    """The run's ``meta`` object (``name``/``description``/``phases``…) read
    from its persisted script — the only source of meta before the completion
    manifest exists, so a live run can show its name + declared phase plan.

    Parsed with tree-sitter (a real JS grammar — no regex and no code
    execution). Cached per (path, mtime, size); ``{}`` on any failure (incl. a
    missing tree-sitter install) so a weird script never breaks ingest.
    """
    if script_path is None or not script_path.exists():
        return {}
    try:
        st = script_path.stat()
        src = script_path.read_bytes()
    except OSError:
        return {}
    key = (str(script_path), st.st_mtime_ns, st.st_size)
    if key in _META_CACHE:
        return _META_CACHE[key]
    try:
        parser, query = _ts()
        obj = _find_meta_object(parser.parse(src).root_node, query)
        meta = _ts_object(obj, src) if obj is not None else {}
    except ImportError:
        meta = {}
    _META_CACHE[key] = meta
    return meta


def _parse_script_meta(script_path: Path | None) -> tuple[str | None, str | None]:
    """``(name, description)`` from a run's script meta (cached node eval)."""
    meta = _load_script_meta(script_path)
    return meta.get("name"), meta.get("description")


def _parse_script_phases(script_path: Path | None) -> list[dict]:
    """Declared ``meta.phases`` (title + detail, in order) from a run's script.

    The only phase info available before the completion manifest exists (which
    carries the real per-agent ``phaseIndex``), so a live run can surface its
    declared phase *plan*. Reuses the cached `_load_script_meta`.
    """
    meta = _load_script_meta(script_path)
    return [{"title": p.get("title"), "detail": p.get("detail")}
            for p in (meta.get("phases") or [])
            if isinstance(p, dict) and p.get("title")]


# Per-agent declaration cache (parallel to _META_CACHE), keyed by
# (path, mtime_ns, size). Value is the ordered declaration list or None.
_AGENTS_CACHE: dict[tuple, list | None] = {}

# Lazily-built compiled (agent-call, phase-call) queries; see `_call_queries`.
_CALL_QUERIES = None

# `@call` captures the whole call node (for source-position ordering + ancestor
# inspection); the `#eq?` predicate isolates the callee identifier.
_AGENT_CALL_QUERY = (
    "(call_expression function: (identifier) @fn arguments: (arguments) "
    "(#eq? @fn \"agent\")) @call"
)
_PHASE_CALL_QUERY = (
    "(call_expression function: (identifier) @fn arguments: (arguments) "
    "(#eq? @fn \"phase\")) @call"
)

# A `agent()` call nested in any of these dispatches a dynamic number of times
# / in non-source order, so static source-order mapping is unsafe.
_LOOP_TYPES = {"for_statement", "for_in_statement", "while_statement",
               "do_statement"}
_ITER_METHODS = {"map", "forEach", "flatMap", "filter", "reduce"}

# Sentinel: an opts value that exists but isn't a static string literal.
_DYNAMIC = object()


def _call_queries():
    """Compiled (agent-call, phase-call) queries, built once. Raises
    ImportError if the grammar isn't installed (caller degrades to None)."""
    global _CALL_QUERIES
    if _CALL_QUERIES is None:
        import tree_sitter as ts
        import tree_sitter_javascript as tsjs
        lang = ts.Language(tsjs.language())
        _CALL_QUERIES = (ts.Query(lang, _AGENT_CALL_QUERY),
                         ts.Query(lang, _PHASE_CALL_QUERY))
    return _CALL_QUERIES


def _ts_literal_str(node, src: bytes):
    """The static string value of a ``string`` / ``template_string`` node, or
    `_DYNAMIC` when the node isn't a pure string literal (a template with a
    ``${...}`` substitution, an identifier, a member expression, …)."""
    if node.type == "string":
        return _ts_string(node, src)
    if node.type == "template_string":
        if any(ch.type == "template_substitution" for ch in node.named_children):
            return _DYNAMIC
        return _ts_string(node, src)
    return _DYNAMIC


def _opts_object(call_node):
    """The 2nd-argument opts ``object`` node of a call, or None when there's no
    second arg or it isn't an object literal."""
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return None
    named = args.named_children
    return named[1] if len(named) >= 2 and named[1].type == "object" else None


def _pair_kv(pair_node, src: bytes) -> tuple:
    """``(key_name, value_node)`` of an object ``pair``, or ``(None, None)``."""
    key, val = pair_node.child_by_field_name("key"), pair_node.child_by_field_name("value")
    if key is None or val is None:
        return None, None
    name = _ts_string(key, src) if key.type == "string" else _ts_text(key, src)
    return name, val


def _agent_call_opts(call_node, src: bytes) -> tuple:
    """``(label, phase)`` for one ``agent(prompt, opts)`` call.

    ``label`` is the literal string or None (absent / dynamic — cosmetic).
    ``phase`` is the literal string, None (no ``opts.phase`` key), or `_DYNAMIC`
    (present but non-literal — structural, forces the caller to fall back).
    """
    obj = _opts_object(call_node)
    if obj is None:
        return None, None
    label, phase = None, None
    for ch in obj.named_children:
        if ch.type != "pair":
            continue
        name, val = _pair_kv(ch, src)
        if name == "label":
            v = _ts_literal_str(val, src)
            label = None if v is _DYNAMIC else v
        elif name == "phase":
            phase = _ts_literal_str(val, src)
    return label, phase


def _in_dynamic_context(node, src: bytes) -> bool:
    """True if ``node`` sits inside a loop, an iteration callback (``.map`` /
    ``.forEach`` / …), or a spread — contexts that dispatch agents in a dynamic
    count / order, so source order no longer tracks dispatch order."""
    anc = node.parent
    while anc is not None:
        if anc.type in _LOOP_TYPES or anc.type == "spread_element":
            return True
        if anc.type == "call_expression":
            fn = anc.child_by_field_name("function")
            if fn is not None and fn.type == "member_expression":
                prop = fn.child_by_field_name("property")
                if prop is not None and _ts_text(prop, src) in _ITER_METHODS:
                    return True
        anc = anc.parent
    return False


def _phase_markers(phase_calls, src: bytes) -> list[tuple]:
    """``(start_byte, 'phase', title|None)`` per ``phase('X')`` call (None when
    the title isn't a literal — that band just won't be set as current)."""
    out: list[tuple] = []
    for node in phase_calls:
        args = node.child_by_field_name("arguments")
        named = args.named_children if args else []
        title = _ts_literal_str(named[0], src) if named else _DYNAMIC
        out.append((node.start_byte, "phase", None if title is _DYNAMIC else title))
    return out


def _agent_markers(agent_calls, src: bytes) -> list[tuple] | None:
    """``(start_byte, 'agent', (label, phase))`` per ``agent()`` call, or None
    if any agent dispatches dynamically or carries a non-literal phase."""
    out: list[tuple] = []
    for node in agent_calls:
        if _in_dynamic_context(node, src):
            return None
        label, phase = _agent_call_opts(node, src)
        if phase is _DYNAMIC:
            return None
        out.append((node.start_byte, "agent", (label, phase)))
    return out


def _extract_script_agents(src: bytes) -> list[dict] | None:
    """Ordered ``[{label, phase}]`` per ``agent()`` call, or None when the
    script can't be confidently mapped (see `_parse_script_agents`)."""
    import tree_sitter as ts

    parser, _ = _ts()
    root = parser.parse(src).root_node
    agent_q, phase_q = _call_queries()
    agent_calls = ts.QueryCursor(agent_q).captures(root).get("call", [])
    agent_markers = _agent_markers(agent_calls, src)
    if agent_markers is None:
        return None
    phase_calls = ts.QueryCursor(phase_q).captures(root).get("call", [])
    markers = sorted(_phase_markers(phase_calls, src) + agent_markers,
                     key=lambda m: m[0])
    out: list[dict] = []
    current = None
    for _b, kind, payload in markers:
        if kind == "phase":
            current = payload
        else:
            label, explicit = payload
            out.append({"label": label, "phase": explicit or current})
    return out or None


def _parse_script_agents(script_path: Path | None) -> list[dict] | None:
    """Ordered ``[{label, phase}]`` for each ``agent(...)`` call in a run's
    script, or ``None`` when the script can't be statically/confidently mapped.

    Each agent's phase is its literal ``opts.phase`` if present, else the most
    recent preceding top-level ``phase('X')`` call. Returns ``None`` (caller
    falls back to the flat live tree) when any agent dispatches dynamically
    (inside a loop / ``.map`` / spread) or carries a non-literal ``phase`` —
    cases where source order no longer matches dispatch order, or the mapping
    is unknowable. A dynamic ``label`` is cosmetic and tolerated (kept None).
    Cached per (path, mtime, size); ``None`` on any failure (incl. a missing
    tree-sitter install) so a weird script never breaks ingest.
    """
    if script_path is None or not script_path.exists():
        return None
    try:
        st = script_path.stat()
        src = script_path.read_bytes()
    except OSError:
        return None
    key = (str(script_path), st.st_mtime_ns, st.st_size)
    if key in _AGENTS_CACHE:
        return _AGENTS_CACHE[key]
    try:
        result = _extract_script_agents(src)
    except ImportError:
        result = None
    _AGENTS_CACHE[key] = result
    return result


def _parent_trace_id(agents_dir: Path) -> str | None:
    """The trace_id of the Claude Code session a run was launched from.

    On-disk a run lives at ``<S>/subagents/workflows/<run_id>``; ``<S>.name``
    is that session's trace_id (and its transcript is the sibling
    ``<S>.jsonl``). Stored on the run root so the run view can offer a
    "↑ launched from session" jump back to its parent.
    """
    try:
        return agents_dir.parent.parent.parent.name or None
    except AttributeError:
        return None


def _first_agent_value(agents_dir: Path, agent_ids: list[str], key: str) -> str | None:
    """Read ``key`` from the first line of the first available agent jsonl.

    Used for ``cwd`` (present on every transcript's first user entry) so
    the run's session row gets tagged to its repo via the normal ingest
    path, and for an accurate live start timestamp.
    """
    for agent_id in agent_ids:
        path = agents_dir / f"agent-{agent_id}.jsonl"
        try:
            with open(path, encoding="utf-8") as fh:
                first = fh.readline().strip()
        except OSError:
            continue
        if not first:
            continue
        try:
            entry = json.loads(first)
        except ValueError:
            continue
        val = entry.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


# --------------------------------------------------------------------------
# span construction
# --------------------------------------------------------------------------

def _span(trace_id: str, span_id: str, name: str, *, parent_id: str | None = None,
          start_time: str | None = None, end_time: str | None = None,
          duration_ms: int = 0, status_code: str = "OK",
          attrs: dict | None = None, is_test: bool = False) -> dict:
    """One OTel-style span dict in the shape `ingest_session_spans` expects."""
    a = {k: v for k, v in (attrs or {}).items() if v is not None}
    if is_test:
        a["is_test"] = True
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_id": parent_id,
        "name": name,
        "kind": "internal",
        "start_time": start_time,
        "end_time": end_time if end_time is not None else start_time,
        "duration_ms": duration_ms,
        "attributes": a,
        "status_code": status_code,
        "status_message": None,
    }


def _tool_inputs_by_id(path: Path) -> dict:
    """Map tool_use_id -> raw input dict by scanning a transcript's assistant
    content blocks. read_usage only retains tool inputs on errors, so the
    label-driving attrs (command, file_path, ...) come from this scan."""
    out: dict = {}
    for entry in _read_jsonl(path):
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                out[block["id"]] = block.get("input") or {}
    return out


_BASH_OUTPUT_CAP = 12000
_BASH_CMD_CAP = 4000


def _result_block_text(inner: object) -> str:
    """A tool_result block's content is either a plain string or a list of
    content parts; collapse both to text."""
    if isinstance(inner, str):
        return inner
    if isinstance(inner, list):
        return "\n".join(b.get("text", "") for b in inner
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _tool_outputs_by_id(path: Path) -> dict:
    """Map tool_use_id -> (output_text, is_error) from a transcript's
    tool_result blocks. Source of Bash stdout/stderr (the manifest/journal
    carry no tool output)."""
    out: dict = {}
    for entry in _read_jsonl(path):
        if entry.get("type") != "user":
            continue
        content = entry.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id"):
                out[block["tool_use_id"]] = (
                    _result_block_text(block.get("content")), bool(block.get("is_error")))
    return out


def _count_diff_lines(diff: list[str]) -> tuple[int, int]:
    added = removed = 0
    for d in diff:
        if d.startswith(("+++", "---")):
            continue
        if d.startswith("+"):
            added += 1
        elif d.startswith("-"):
            removed += 1
    return added, removed


def _unified_diff(old: str, new: str) -> tuple[str, int, int]:
    """Standard unified diff of two strings + added/removed line counts."""
    diff = list(difflib.unified_diff(
        (old or "").splitlines(), (new or "").splitlines(), lineterm="", n=2))
    added, removed = _count_diff_lines(diff)
    body = "\n".join(d for d in diff if not d.startswith(("---", "+++")))
    return body[:6000], added, removed


def _attach_diff(attrs: dict, name: str, tinput: dict) -> None:
    """Populate diff/edit_op/added_lines/removed_lines for edit-family tools,
    from the tool input alone (no tool_response needed)."""
    if name == "Write":
        old, new = "", tinput.get("content") or ""
    elif name == "MultiEdit":
        edits = tinput.get("edits") or []
        old = "\n".join(e.get("old_string", "") for e in edits)
        new = "\n".join(e.get("new_string", "") for e in edits)
    else:  # Edit
        old, new = tinput.get("old_string") or "", tinput.get("new_string") or ""
    diff, added, removed = _unified_diff(old, new)
    if diff:
        attrs.update(diff=diff, edit_op=name.lower(),
                     added_lines=added, removed_lines=removed)


def _tool_attrs(agent_id: str, name: str, tinput: dict) -> dict:
    """Rebuild the label/render attributes a tool span needs from its input
    (what post_tool_trace computes live for hook-sourced sessions)."""
    attrs: dict = {"agent_id": agent_id, "tool_name": name, "tool_input": tinput}
    if tinput.get("command"):
        cmd = str(tinput["command"])
        attrs["command_preview"] = cmd[:200]
        attrs["command"] = cmd[:_BASH_CMD_CAP]
    fp = tinput.get("file_path") or tinput.get("path") or tinput.get("notebook_path")
    if fp:
        attrs["file_path"] = fp
    if tinput.get("query"):
        attrs["query"] = tinput["query"]
    if tinput.get("pattern"):
        attrs["pattern"] = tinput["pattern"]
    if name in ("Edit", "Write", "MultiEdit"):
        _attach_diff(attrs, name, tinput)
    return attrs


def _attach_bash_output(attrs: dict, name: str, output) -> None:
    """Attach Bash stdout/stderr from its tool_result. The transcript gives
    one combined output string, so it lands in stdout (or stderr on error)."""
    if name != "Bash" or not output:
        return
    text, is_err = output
    if not text:
        return
    body, dropped = text[:_BASH_OUTPUT_CAP], max(0, len(text) - _BASH_OUTPUT_CAP)
    key = "stderr" if is_err else "stdout"
    attrs[key] = body
    if dropped:
        attrs[f"{key}_truncated_bytes"] = dropped


def _tool_spans(run_id: str, parent_id: str, agent_id: str, turn_idx: int,
                ts: str | None, tool_calls, tool_inputs: dict,
                tool_outputs: dict, is_test: bool) -> list[dict]:
    """``tool.<name>`` spans for one turn, with full label/render attributes.

    Skips the *successful* ``StructuredOutput`` — that call *is* the agent's
    result, surfaced by the agent's RESULT card rather than as a giant raw
    tool row. A *failed* StructuredOutput is an error, not a result, so it's
    kept: the retries are exactly the story the workflow trace should show
    (mirrors the main session's `tool.failure` capture).
    """
    out: list[dict] = []
    for jdx, call in enumerate(tool_calls or ()):
        tname = call.get("name") or "tool"
        if tname == "StructuredOutput" and not call.get("is_error"):
            continue
        attrs = _tool_attrs(agent_id, tname, tool_inputs.get(call.get("id")) or {})
        _enrich_call_attrs(attrs, call)
        _attach_bash_output(attrs, tname, tool_outputs.get(call.get("id")))
        out.append(_span(
            run_id, f"wftool-{run_id}-{agent_id}-{turn_idx}-{jdx}",
            f"tool.{tname}", parent_id=parent_id, start_time=ts, end_time=ts,
            status_code="ERROR" if call.get("is_error") else "OK",
            attrs=attrs, is_test=is_test,
        ))
    return out


def _enrich_call_attrs(attrs: dict, call: dict) -> None:
    """Carry per-call flags (error, server-side advisor reply) onto the span."""
    if call.get("is_error"):
        attrs["is_error"] = True
    if call.get("server_side"):
        attrs["server_side"] = True
    if call.get("response_text"):  # advisor / web tools carry their reply
        attrs["response_text"] = call["response_text"]
    if call.get("advisor_model"):
        attrs["advisor_model"] = call["advisor_model"]


def _agent_turn_spans(run_id: str, agent_span_id: str, agent_id: str,
                      agents_dir: Path, is_test: bool) -> list[dict]:
    """Expand one agent's transcript into per-turn + per-tool spans.

    Reuses `lib.trace.transcript_usage.read_usage`. ``assistant_response``
    spans carry ``output_tokens`` in their attributes, which the ingest
    INSERT promotes to the column the trace tree renders.
    """
    from lib.trace.transcript_usage import read_usage

    path = agents_dir / f"agent-{agent_id}.jsonl"
    if not path.exists():
        return []
    usage = read_usage(str(path), max_text_bytes=_TURN_TEXT_CAP)
    if usage is None or not usage.turns:
        return []
    tool_inputs = _tool_inputs_by_id(path)
    tool_outputs = _tool_outputs_by_id(path)
    spans: list[dict] = []
    for idx, turn in enumerate(usage.turns):
        ts = turn.timestamp
        resp_id = f"wfturn-{run_id}-{agent_id}-{idx}"
        # A turn emits a response card when it produced text, else a thinking
        # card when it only reasoned; tools nest under whichever exists (or the
        # agent itself), avoiding an empty bubble / orphan parent_id.
        think_id = f"wfthink-{run_id}-{agent_id}-{idx}"
        spans.extend(_turn_head_spans(run_id, resp_id, think_id, agent_span_id,
                                      agent_id, turn, ts, is_test))
        tool_parent = (resp_id if turn.text
                       else think_id if turn.thinking_blocks
                       else agent_span_id)
        spans.extend(_tool_spans(run_id, tool_parent, agent_id, idx, ts,
                                 turn.tool_calls, tool_inputs, tool_outputs, is_test))
    return spans


def _stagger_before_ts(ts: str | None) -> str | None:
    """`ts` minus 1 ms so the thinking span sorts immediately ahead of its
    response in the start_time-ordered conversation tree (same idiom as
    span_posters._stagger_before). Returns `ts` unchanged when it isn't a
    parseable ISO timestamp."""
    if not ts:
        return ts
    try:
        base = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
        return (datetime.fromisoformat(base) - timedelta(milliseconds=1)).isoformat()
    except (ValueError, TypeError):
        return ts


def _turn_head_spans(run_id: str, resp_id: str, think_id: str, parent_id: str,
                     agent_id: str, turn, ts: str | None,
                     is_test: bool) -> list[dict]:
    """Per-turn head span(s): an ``assistant_response`` (text) and/or an
    ``assistant.thinking`` (reasoning). A turn with both emits BOTH — the
    thinking staggered 1 ms earlier so the start_time-ordered tree renders
    it first. Mirrors ``span_posters._maybe_emit_assistant_span``.

    Token split — this reconstruction does NOT attribute tokens to
    individual tool spans, so the head spans account for the whole turn:

      * no thinking          -> response carries the full turn
                                output_tokens (unchanged; tool_use stays
                                lumped with the text as before).
      * thinking present     -> response carries the text estimate and the
                                thinking span carries the remainder
                                (output - text) = reasoning + tool_use.

    Either way ``response + thinking == turn.output_tokens`` so nothing is
    lost. The prior code dropped an encrypted thinking-only turn entirely
    (no thinking_text -> no span) and folded thinking into the response
    bucket on text turns — both fixed here."""
    from lib.tokens.token_estimator import estimate_text_tokens  # type: ignore
    spans: list[dict] = []
    has_think = turn.thinking_blocks > 0
    text_out = estimate_text_tokens(turn.text) if turn.text else 0
    base = {"agent_id": agent_id, "input_tokens": turn.input_tokens,
            "model": turn.model, "turn_uuid": turn.uuid}
    if turn.text:
        resp_out = text_out if has_think else (turn.output_tokens or 0)
        spans.append(_span(
            run_id, resp_id, "assistant_response", parent_id=parent_id,
            start_time=ts, end_time=ts,
            duration_ms=turn.inference_duration_ms or 0,
            attrs={**base, "output_tokens": resp_out,
                   "text": turn.text, "truncated": turn.text_truncated},
            is_test=is_test))
    if has_think:
        spans.append(_thinking_head_span(
            run_id, think_id, parent_id, base, turn, ts, text_out,
            has_text=bool(turn.text), is_test=is_test))
    return spans


def _thinking_head_span(run_id: str, think_id: str, parent_id: str, base: dict,
                        turn, ts: str | None, text_out: int, *,
                        has_text: bool, is_test: bool) -> dict:
    """The turn's ``assistant.thinking`` span. Output is the turn's output
    minus the text estimate (reasoning + un-attributed tool_use); when the
    turn also has text it's staggered 1 ms ahead and carries no duration
    (the response span owns the per-call latency)."""
    tattrs = {**base, "output_tokens": max(0, (turn.output_tokens or 0) - text_out),
              "thinking_blocks": turn.thinking_blocks,
              "thinking_signature_bytes": turn.thinking_signature_bytes}
    if turn.thinking_text:
        tattrs["thinking_text"] = turn.thinking_text
        tattrs["thinking_truncated"] = turn.thinking_text_truncated
    think_ts = _stagger_before_ts(ts) if has_text else ts
    return _span(
        run_id, think_id, "assistant.thinking", parent_id=parent_id,
        start_time=think_ts, end_time=think_ts,
        duration_ms=0 if has_text else (turn.inference_duration_ms or 0),
        attrs=tattrs, is_test=is_test)


def _root_and_title_spans(run_id: str, *, title: str, start: str | None,
                          end: str | None, attrs: dict, is_test: bool) -> list[dict]:
    """The run-root ``session.start`` span (carries agent_type/model/cwd that
    drive the session row) plus the ``prompt`` span that becomes its title."""
    root_id = f"wfrun-{run_id}"
    return [
        _span(run_id, root_id, "session.start", start_time=start, end_time=end,
              attrs=attrs, is_test=is_test),
        _span(run_id, f"wfprompt-{run_id}", "prompt", parent_id=root_id,
              start_time=start, end_time=start,
              attrs={"text": title}, is_test=is_test),
    ]


def _run_bounds(manifest: dict) -> tuple[str | None, str | None]:
    """(start, end) ISO strings from the manifest's epoch-ms start+duration."""
    start_ms = manifest.get("startTime")
    dur = manifest.get("durationMs") or 0
    end_ms = start_ms + dur if start_ms else None
    return _iso(start_ms), _iso(end_ms)


def _manifest_agents(manifest: dict) -> tuple[list[dict], list[str]]:
    """The ``workflow_agent`` progress entries and their agent ids."""
    progress = manifest.get("workflowProgress") or []
    agents = [e for e in progress if e.get("type") == "workflow_agent"]
    return agents, [a["agentId"] for a in agents if a.get("agentId")]


def _phase_spans(run_id: str, root_id: str, phases: list, start: str | None,
                 end: str | None, is_test: bool) -> tuple[list[dict], dict[int, str]]:
    """One ``workflow.phase`` span per declared phase, plus an index map so
    agents can be parented to their phase. Driven by a ``[{title, detail}]``
    list — the manifest's ``phases`` when terminal, or the script's declared
    ``meta.phases`` while live (so both paths build identical ``wfphase-`` ids
    and the live→complete re-ingest stays idempotent)."""
    spans: list[dict] = []
    phase_ids: dict[int, str] = {}
    for i, phase in enumerate(phases or [], start=1):
        pid = f"wfphase-{run_id}-{i}"
        phase_ids[i] = pid
        spans.append(_span(run_id, pid, "workflow.phase", parent_id=root_id,
                           start_time=start, end_time=end,
                           attrs={"title": phase.get("title"),
                                  "detail": phase.get("detail"), "index": i},
                           is_test=is_test))
    return spans, phase_ids


def build_full_spans(manifest: dict, agents_dir: Path, *, deep: bool = True,
                     is_test: bool = False,
                     snapshot_stale_at: str | None = None) -> list[dict]:
    """Build the full phase tree from a run's manifest.

    Used whenever a manifest exists — a completed run, a paused (``killed``)
    run, or a running run whose snapshot is being shown (see `RunRef.terminal`).
    A live ``running`` manifest gets no ``session.end`` span (the run hasn't
    ended); every other status closes the run with one. ``snapshot_stale_at``
    (an ISO time) is stamped on the root when the run has progressed past this
    snapshot (a resume) so the UI can flag the tree as a stale snapshot."""
    run_id = manifest["runId"]
    root_id = f"wfrun-{run_id}"
    start, end = _run_bounds(manifest)
    status = manifest.get("status") or "completed"
    agents, agent_ids = _manifest_agents(manifest)
    title = manifest.get("summary") or manifest.get("workflowName") or run_id

    spans = _root_and_title_spans(
        run_id, title=title, start=start, end=end,
        attrs={"agent_type": "claude", "model": manifest.get("defaultModel"),
               "cwd": _first_agent_value(agents_dir, agent_ids, "cwd"),
               "run_id": run_id, "task_id": manifest.get("taskId"),
               "parent_trace_id": _parent_trace_id(agents_dir),
               "workflow_name": manifest.get("workflowName"),
               "workflow_status": status, "agent_count": manifest.get("agentCount"),
               "snapshot_stale_at": snapshot_stale_at,
               "total_tokens": manifest.get("totalTokens"),
               "total_tool_calls": manifest.get("totalToolCalls")},
        is_test=is_test)

    phases, phase_ids = _phase_spans(run_id, root_id, manifest.get("phases") or [],
                                     start, end, is_test)
    spans.extend(phases)
    # Full per-agent results live in the journal (the manifest only previews).
    _, results = _journal_agents(_read_jsonl(agents_dir / "journal.jsonl"))
    for agent in agents:
        spans.extend(_full_agent_spans(
            run_id, root_id, phase_ids, agent, agents_dir, results, deep, is_test,
            run_start=start))

    # A live (still-running) manifest snapshot hasn't ended — don't close it
    # with a session.end (that would render an in-flight run as finished).
    if status != "running":
        spans.append(_span(run_id, f"wfend-{run_id}", "session.end", parent_id=root_id,
                           start_time=end, end_time=end,
                           status_code="OK" if status == "completed" else "ERROR",
                           attrs={"reason": status}, is_test=is_test))
    return spans


def _agent_full_prompt(agents_dir: Path, agent_id: str) -> str | None:
    """The agent's full dispatched prompt = its transcript's first user
    message (the manifest only carries a ~400-char preview). Iterates lazily —
    the prompt is the transcript's first entry, so the live poll path reads
    one line, not the whole streaming file."""
    for entry in _iter_jsonl(agents_dir / f"agent-{agent_id}.jsonl"):
        if entry.get("type") != "user":
            continue
        content = entry.get("message", {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text = "\n".join(b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text")
            return text or None
        return None
    return None


def _result_text(result: object) -> str | None:
    """Render an agent's result for display: structured results (schema'd
    agents) pretty-print as JSON; text results pass through."""
    if result is None:
        return None
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(result)


def _agent_tool_count(turn_spans: list[dict], deep: bool, agent_id, fallback):
    """The agent header's tool-call count. When the deep tree was built, count
    the ``tool.*`` spans actually captured (and rendered) — this includes
    server-side tools like advisor and excludes the skipped StructuredOutput,
    so the header matches the rendered rows. The manifest's ``toolCalls``
    undercounts server-side tools, so it's only the fallback when no deep spans
    were built."""
    if not (deep and agent_id):
        return fallback
    return sum(1 for s in turn_spans if s["name"].startswith("tool."))


def _agent_bounds(agent: dict, run_start: str | None) -> tuple:
    """``(start_ts, end_ts)`` ISO strings for an agent span. Un-started (queued)
    agents have no ``startedAt`` — fall back to ``queuedAt`` then the run start
    so ``start_time`` is never null (the DB requires it)."""
    started = agent.get("startedAt")
    start_ts = _iso(started) or _iso(agent.get("queuedAt")) or run_start
    if started:
        return start_ts, _iso(started + (agent.get("durationMs") or 0))
    return start_ts, start_ts


def _full_agent_spans(run_id: str, root_id: str, phase_ids: dict[int, str],
                      agent: dict, agents_dir: Path, results: dict, deep: bool,
                      is_test: bool, run_start: str | None = None) -> list[dict]:
    """One agent's ``subagent.start`` span plus its deep turn children.

    Prompt + result are sourced from the transcript / journal (full text),
    not the manifest's truncated previews, so "Show full prompt" / the RESULT
    card show the complete content.
    """
    agent_id = agent.get("agentId")
    parent = phase_ids.get(agent.get("phaseIndex"), root_id)
    dur = agent.get("durationMs") or 0
    # A live/paused manifest also lists *queued* agents that have no agentId
    # yet; key those by their manifest index so they don't all collide on
    # ``wfagent-…-None`` (and render as one broken row).
    span_id = f"wfagent-{run_id}-{agent_id or 'q' + str(agent.get('index'))}"
    start_ts, end_ts = _agent_bounds(agent, run_start)
    full_prompt = (_agent_full_prompt(agents_dir, agent_id) if agent_id else None) \
        or agent.get("promptPreview")
    result_full = _result_text(results.get(agent_id)) or agent.get("resultPreview")
    turn_spans = _agent_turn_spans(run_id, span_id, agent_id, agents_dir, is_test) \
        if (deep and agent_id) else []
    tool_calls = _agent_tool_count(turn_spans, deep, agent_id, agent.get("toolCalls"))
    spans = [_span(
        run_id, span_id, "subagent.start", parent_id=parent,
        start_time=start_ts, end_time=end_ts, duration_ms=dur,
        attrs={"agent_id": agent_id, "agent_type": agent.get("agentType"),
               "agent_name": agent.get("label"), "label": agent.get("label"),
               "model": agent.get("model"), "state": agent.get("state"),
               "phase_title": agent.get("phaseTitle"),
               "prompt": full_prompt,
               "result_full": result_full,
               "result_preview": _preview(result_full),
               "tokens": agent.get("tokens"), "tool_calls": tool_calls},
        is_test=is_test)]
    spans.extend(turn_spans)
    return spans


def _journal_agents(events: list[dict]) -> tuple[list[str], dict[str, object]]:
    """(started agent ids in order, {agentId: result}) from journal events."""
    started: list[str] = []
    results: dict[str, object] = {}
    for e in events:
        agent_id = e.get("agentId")
        if not agent_id:
            continue
        if e.get("type") == "started":
            started.append(agent_id)
        elif e.get("type") == "result":
            results[agent_id] = e.get("result")
    return started, results


def _journal_key_order(events: list[dict]) -> tuple[list[str], dict[str, str]]:
    """(distinct dispatch ``key``s in first-seen order, {key: latest agentId}).

    Each ``started`` event carries a ``key`` (a stable hash of the agent's
    dispatch input) and an ``agentId``. A paused→resumed run re-dispatches the
    same logical agent under the same key with a new agentId, so keying on
    ``key`` (keeping the latest agentId) collapses kill/resume duplicates to the
    canonical agent set — the same set the completion manifest records — in the
    order agents were first dispatched (which matches the script's source order;
    see `_parse_script_agents`)."""
    key_order: list[str] = []
    key_to_agent: dict[str, str] = {}
    for e in events:
        if e.get("type") != "started":
            continue
        key, agent_id = e.get("key"), e.get("agentId")
        if not key or not agent_id:
            continue
        if key not in key_to_agent:
            key_order.append(key)
        key_to_agent[key] = agent_id
    return key_order, key_to_agent


def _flat_agent_span(run_ref: RunRef, parent_id: str, agent_id: str,
                     results: dict, start: str | None, is_test: bool, *,
                     label: str | None = None,
                     phase_title: str | None = None) -> dict:
    """One agent's live ``subagent.start`` span with its current token total.

    ``parent_id`` is the run root in the coarse fallback, or the agent's
    ``workflow.phase`` span when the script let us map agents to phases live.
    ``label`` / ``phase_title`` (script-derived) are stamped so the rail shows
    real labels under real phases instead of generic ``workflow-subagent`` rows.
    """
    from lib.trace.transcript_usage import read_usage

    path = run_ref.agents_dir / f"agent-{agent_id}.jsonl"
    usage = read_usage(str(path), max_text_bytes=0) if path.exists() else None
    meta = _read_json(run_ref.agents_dir / f"agent-{agent_id}.meta.json") or {}
    # Store the FULL result (not just a preview) so the RESULT card can offer
    # "Show full" — mirrors `_full_agent_spans`. The journal carries the whole
    # result for a done agent; previewing it without keeping the full text left
    # the live path's RESULT card permanently trimmed at the preview cap.
    result_full = _result_text(results.get(agent_id))
    return _span(
        run_ref.run_id, f"wfagent-{run_ref.run_id}-{agent_id}", "subagent.start",
        parent_id=parent_id, start_time=start,
        attrs={"agent_id": agent_id, "agent_type": meta.get("agentType"),
               "label": label, "agent_name": label, "phase_title": phase_title,
               "state": "done" if agent_id in results else "running",
               # Dispatched prompt, available from the transcript the moment
               # the agent starts — mirrors `_full_agent_spans` so the task
               # prompt shows while the agent is still running.
               "prompt": _agent_full_prompt(run_ref.agents_dir, agent_id),
               "result_full": result_full,
               "result_preview": _preview(result_full),
               "tokens": usage.output_tokens if usage else None},
        is_test=is_test)


def _live_phase_list(decls: list[dict], script_path: Path | None) -> list | None:
    """The ordered ``[{title, detail}]`` phases for a confident live layout: the
    script's declared ``meta.phases`` (with detail), else derived from the
    agents' own phase values in first-seen order. None if any agent's phase is
    missing or names a phase outside the set — i.e. the parse model is wrong."""
    phases = _parse_script_phases(script_path)
    titles = [p["title"] for p in phases]
    if not titles:
        for d in decls:
            if d["phase"] and d["phase"] not in titles:
                titles.append(d["phase"])
        phases = [{"title": t, "detail": None} for t in titles]
    if any(d["phase"] not in titles for d in decls):
        return None
    return phases


def _confident_live_layout(run_ref: RunRef, root_id: str, key_order: list[str],
                           key_to_agent: dict[str, str], start: str | None,
                           is_test: bool) -> tuple | None:
    """``(phase_spans, [(agent_id, label, phase_title, parent_id)])`` when the
    script maps cleanly to the live agents — agents parented under synthesized
    ``workflow.phase`` spans, mirroring the completed tree. None (→ coarse flat
    fallback) when the script is dynamic, unmatched, or count-mismatched."""
    decls = _parse_script_agents(run_ref.script_path)
    if not decls or len(decls) != len(key_order):
        return None
    phases = _live_phase_list(decls, run_ref.script_path)
    if phases is None:
        return None
    phase_spans, phase_ids = _phase_spans(run_ref.run_id, root_id, phases,
                                          start, None, is_test)
    index_by_title = {p["title"]: i + 1 for i, p in enumerate(phases)}
    ordered = [(key_to_agent[k], d["label"], d["phase"],
                phase_ids[index_by_title[d["phase"]]])
               for k, d in zip(key_order, decls)]
    return phase_spans, ordered


def build_flat_spans(run_ref: RunRef, *, deep: bool = True,
                     is_test: bool = False) -> list[dict]:
    """Build the live tree for an *in-progress* run from the journal.

    No completion manifest exists yet. When the run's script can be statically
    mapped (`_confident_live_layout`), we synthesize the same
    ``workflow.phase`` → ``subagent.start`` structure the completed tree builds
    — real phases, real labels, agents marked running/done — so the run's own
    view renders identically to a finished run while still live. Otherwise we
    fall back to the coarse tree: agents hang directly off the run root and the
    rail shows the read-only declared phase plan above a flat "Running" band.

    When ``deep`` (the default), each agent's transcript is expanded into
    per-turn / per-tool spans via `_agent_turn_spans` (matching the full tree's
    ``wfagent-…`` ids, so the live→complete transition is idempotent).
    """
    run_id = run_ref.run_id
    root_id = f"wfrun-{run_id}"
    events = _read_jsonl(run_ref.journal_path)
    started, results = _journal_agents(events)
    key_order, key_to_agent = _journal_key_order(events)
    name, desc = _parse_script_meta(run_ref.script_path)
    start = _first_agent_value(run_ref.agents_dir, started, "timestamp") \
        or _iso(int(run_ref.state_mtime() * 1000))

    layout = _confident_live_layout(run_ref, root_id, key_order, key_to_agent,
                                    start, is_test)
    if layout is not None:
        phase_spans, agent_plan = layout
    else:
        # Fallback: today's coarse tree — raw started order, no phase mapping.
        phase_spans = []
        agent_plan = [(a, None, None, root_id) for a in started]

    spans = _root_and_title_spans(
        run_id, title=(desc or name or run_id), start=start, end=None,
        attrs={"agent_type": "claude", "workflow_name": name, "run_id": run_id,
               "workflow_status": "running",
               "parent_trace_id": run_ref.session_dir.name,
               "cwd": _first_agent_value(run_ref.agents_dir, started, "cwd"),
               # Declared phase plan from the script. The rail shows it as a
               # read-only plan only in the fallback (when no real phase spans
               # exist); once `_confident_live_layout` synthesizes phases it is
               # superseded but harmlessly retained.
               "phase_plan": _parse_script_phases(run_ref.script_path),
               "agent_count": len(agent_plan)},
        is_test=is_test)
    spans.extend(phase_spans)
    for agent_id, label, phase_title, parent_id in agent_plan:
        spans.append(_flat_agent_span(run_ref, parent_id, agent_id, results,
                                      start, is_test, label=label,
                                      phase_title=phase_title))
        if deep and agent_id:
            spans.extend(_agent_turn_spans(
                run_id, f"wfagent-{run_id}-{agent_id}", agent_id,
                run_ref.agents_dir, is_test))
    return spans


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def _clear_run(run_id: str) -> None:
    """Delete a run's existing rows so re-ingest can't double-count or leave
    stale tree-map rows (the live flat tree is replaced by the full tree)."""
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in ("session_spans", "session_trace_map", "sessions",
                      "turn_usage"):
            conn.execute(f"DELETE FROM {table} WHERE trace_id = ?", (run_id,))
        conn.commit()
    finally:
        conn.close()


def _session_token_split(agents_dir: Path, agent_ids: list[str]) -> dict:
    """Sum the per-agent token split (input / output / cache) from the agent
    transcripts via ``read_usage`` — the same source the normal aggregator and
    the per-turn spans use. Used to populate the session row with an honest
    output-only ``output_tokens`` instead of the manifest's grand
    ``totalTokens`` (which folds in input + cache; cache usually dominates)."""
    from lib.trace.transcript_usage import read_usage

    tot = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    for aid in agent_ids:
        if not aid:
            continue
        usage = read_usage(str(agents_dir / f"agent-{aid}.jsonl"), max_text_bytes=0)
        if usage is None:
            continue
        tot["input"] += usage.input_tokens or 0
        tot["output"] += usage.output_tokens or 0
        tot["cache_read"] += usage.cache_read_tokens or 0
        tot["cache_creation"] += usage.cache_creation_tokens or 0
    return tot


def _set_session_tokens(run_id: str, split: dict) -> None:
    """Stamp the run's token split onto the session row, matching how the
    normal per-turn aggregator (which never runs for workflow runs) populates a
    session: separate input / output / cache columns, so ``output_tokens`` is
    output-only.

    This is what keeps the "Tokens by tool" rollup honest — its untagged
    remainder is ``output_tokens - attributed_output``, so stuffing the grand
    ``totalTokens`` into ``output_tokens`` (the old behaviour) surfaced the
    whole cache+input total as bogus "untagged" output. ``peak_context_tokens``
    stays NULL — a fan-out has no single context window, so context% is blank.
    """
    if not any(split.values()):
        return
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sessions SET input_tokens = ?, output_tokens = ?, "
            "cache_read_tokens = ?, cache_creation_tokens = ? WHERE trace_id = ?",
            (split["input"], split["output"], split["cache_read"],
             split["cache_creation"], run_id))
        conn.commit()
    finally:
        conn.close()


def _run_turn_usage_rows(agents_dir: Path, agent_ids: list[str]) -> list[dict]:
    """Per-turn usage rows for a run's agents, sourced from the agent
    transcripts via ``read_usage`` and priced per-turn at each turn's own
    context tier — the same source and pricing the main session bill uses.

    Workflow agents never reach the live turn-usage hook, so the run otherwise
    has no ``turn_usage`` rows at all: ``_session_bill_cost`` (which reads
    turn_usage) then returns a zeroed split and the whole bill renders $0. These
    rows let the workflow trace bill exactly like a normal session.

    ``turn_uuid`` is namespaced by agent id (``<agent_id>:<uuid|idx>``) because a
    run's agents can legitimately reuse a turn uuid (and tests share one
    transcript across agents) — without the prefix they'd collide on the
    ``(trace_id, turn_uuid)`` primary key and over-write each other.
    """
    from lib.trace.transcript_usage import read_usage

    rows: list[dict] = []
    for aid in agent_ids:
        if not aid:
            continue
        usage = read_usage(str(agents_dir / f"agent-{aid}.jsonl"), max_text_bytes=0)
        if usage is None:
            continue
        for idx, turn in enumerate(usage.turns):
            rows.append(_turn_usage_row(aid, idx, turn, usage.model))
    return rows


def _turn_usage_row(aid: str, idx: int, turn, default_model: str | None) -> dict:
    """One ``turn_usage`` row dict for a workflow agent turn, priced at its own
    context tier (cache reads/writes fold into ``cost_usd``)."""
    from lib.tokens.pricing import TokenBreakdown, cost as price_cost

    in_tok = int(turn.input_tokens or 0)
    out_tok = int(turn.output_tokens or 0)
    cache_r = int(turn.cache_read_tokens or 0)
    cache_w = int(turn.cache_creation_tokens or 0)
    ctx = in_tok + cache_r + cache_w
    model = turn.model or default_model
    usd = price_cost(model, TokenBreakdown(
        input_tokens=in_tok, output_tokens=out_tok,
        cache_read_tokens=cache_r, cache_creation_tokens=cache_w,
    ), context_tokens=ctx) if model else None
    return {
        "turn_uuid": f"{aid}:{turn.uuid or idx}", "turn_index": idx,
        "timestamp": turn.timestamp or "", "model": model,
        "input_tokens": in_tok, "output_tokens": out_tok,
        "cache_read_tokens": cache_r, "cache_creation_tokens": cache_w,
        "context_used_tokens": ctx, "cost_usd": usd,
    }


def _set_session_cost(run_id: str, agents_dir: Path, agent_ids: list[str]) -> None:
    """Persist the run's per-turn usage + total cost onto its trace.

    Inserts one ``turn_usage`` row per agent turn (priced at its context tier)
    so the rollup's per-bucket bill split computes, and stamps the summed cost on
    ``sessions.cost_usd`` so the footer total is real — mirroring how the normal
    per-turn aggregator bills an interactive session (a path workflow runs never
    hit). ``_clear_run`` wipes the run's turn_usage first, so the insert is the
    final, deterministic value. No-op when no turn carries usage.
    """
    rows = _run_turn_usage_rows(agents_dir, agent_ids)
    if not rows:
        return
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "INSERT OR REPLACE INTO turn_usage (trace_id, turn_uuid, turn_index, "
            "timestamp, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_creation_tokens, context_used_tokens, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(run_id, r["turn_uuid"], r["turn_index"], r["timestamp"], r["model"],
              r["input_tokens"], r["output_tokens"], r["cache_read_tokens"],
              r["cache_creation_tokens"], r["context_used_tokens"], r["cost_usd"])
             for r in rows])
        conn.execute(
            "UPDATE sessions SET cost_usd = ? WHERE trace_id = ?",
            (sum(r["cost_usd"] or 0.0 for r in rows), run_id))
        conn.commit()
    finally:
        conn.close()


def _set_session_title(run_id: str, title: str | None) -> None:
    """Title the run's session row with the *workflow name*, not its objective.

    The synthetic ``prompt`` span carries the run's objective (the script
    description / manifest summary), so the normal earliest-prompt rule
    (`lib.trace.trace_service.ingest._handle_prompt_title`) would label the
    session with that whole sentence and tag it ``first_prompt`` — which
    then drives the wrong source chip + tooltip + truncation in the UI. The
    workflow *name* (``meta.name``, e.g. "review-changes") is the short
    canonical identifier, so we stamp it directly with an honest source.
    ``_clear_run`` wipes the row each re-ingest, so this UPDATE is the
    final, deterministic value.
    """
    if not title:
        return
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sessions SET title = ?, title_source = 'workflow_name' "
            "WHERE trace_id = ?", (title, run_id))
        conn.commit()
    finally:
        conn.close()


def _set_session_origin(run_id: str) -> None:
    """Mark the run's session row as a captured workflow on the *origin* axis.

    ``sessions.origin`` is orthogonal to ``agent_type``: ``agent_type`` is
    the launching agent's vendor ('claude' for a workflow run, since the
    Workflow tool is a Claude Code feature), while ``origin`` records what
    KIND of row this is — 'session' for a real interactive agent session
    (the default) vs 'workflow' for a captured dynamic-workflow run. The
    run-root span carries ``agent_type='claude'``, so without this stamp the
    row would be indistinguishable from a normal Claude session; here we set
    its ``origin`` so the Sessions list can filter captured runs in/out.
    ``_clear_run`` wipes the row each re-ingest, so this UPDATE is the
    final, deterministic value.
    """
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sessions SET origin = 'workflow' WHERE trace_id = ?",
            (run_id,))
        conn.commit()
    finally:
        conn.close()


def _tag_workflow_sessions(run_id: str, parent_trace_id: str | None) -> None:
    """Auto-tag the run session AND its launching session with `workflow`.

    The builtin category axis is origin-only, so the *owner* session (a
    normal interactive session whose origin stays NULL) can't be reached
    through it — a stored `session_tags` row is the one mechanism that lets
    the tag facet select "everything workflow-related": the captured runs
    plus the sessions that launched them. The parent is tagged only once its
    session row exists (mirrors `_stamp_parent_link`'s skip-when-absent):
    `/api/session-tags` counts raw tag rows without joining sessions, so an
    orphan row would inflate the facet count. Re-ingests re-assert the tag
    (conflict-ignore, never downgrading a manual row) — which also means a
    hand-removed `workflow` tag returns on the next ingest pass; derived
    tags mirror the runs on disk.
    """
    from lib.orm.engine import get_connection
    from lib.trace.session_tags import upsert_auto_tags

    conn = get_connection()
    try:
        upsert_auto_tags(conn, run_id, ["workflow"])
        if parent_trace_id and conn.execute(
                "SELECT 1 FROM sessions WHERE trace_id = ?",
                (parent_trace_id,)).fetchone():
            upsert_auto_tags(conn, parent_trace_id, ["workflow"])
        conn.commit()
    finally:
        conn.close()


def _workflow_block(block: object) -> tuple[str, str] | None:
    """``(tool_use_id, script)`` if ``block`` is a ``Workflow`` tool_use."""
    if not isinstance(block, dict):
        return None
    if block.get("type") != "tool_use" or block.get("name") != "Workflow":
        return None
    tid, src = block.get("id"), (block.get("input") or {}).get("script")
    return (tid, src) if tid and isinstance(src, str) else None


def _iter_workflow_tool_uses(transcript: Path):
    """Yield ``(tool_use_id, script)`` for each ``Workflow`` call in a
    parent transcript's assistant content blocks."""
    for entry in _read_jsonl(transcript):
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            hit = _workflow_block(block)
            if hit:
                yield hit


def _find_parent_tool_use(transcript: Path, script: str) -> str | None:
    """tool_use_id of the parent ``Workflow`` call whose script launched a run.

    The runtime persists each run's script verbatim — byte-identical to the
    parent's ``Workflow`` tool_use ``input.script`` — so an exact match
    uniquely ties a run to the call that started it (distinct runs carry
    distinct scripts). Whitespace-stripped compare as a safety net.
    """
    target = script.strip()
    for tid, src in _iter_workflow_tool_uses(transcript):
        if src.strip() == target:
            return tid
    return None


def _iter_workflow_tool_results(transcript: Path):
    """Yield ``(tool_use_id, result_text)`` for each tool_result in a parent
    transcript. Tool results come back as content blocks on the following
    message; the ``Workflow`` result embeds the launched run's dir."""
    for entry in _read_jsonl(transcript):
        content = entry.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = block.get("tool_use_id")
            raw = block.get("content")
            if tid:
                yield tid, raw if isinstance(raw, str) else json.dumps(raw)


def _find_parent_tool_use_by_run_id(transcript: Path, run_id: str) -> str | None:
    """tool_use_id of the ``Workflow`` call that launched ``run_id``, recovered
    from the call's RESULT instead of its input script.

    The script lives in the tool_use ``input`` and is the first thing
    transcript compaction strips — which is why the script match misses on
    older sessions. The tool_result text (``Workflow launched … Transcript
    dir: …/workflows/<run_id>``) is far more durable, so matching the run dir
    path re-links runs whose launching script was summarised away. Returns the
    first match; a run relaunched/resumed under the same dir maps to several
    calls, and any of them is a valid anchor for the run's agents.
    """
    needle = f"workflows/{run_id}"
    for tid, text in _iter_workflow_tool_results(transcript):
        if needle in text:
            return tid
    return None


def _stamp_parent_link(run_ref: RunRef, workflow_name: str | None,
                       agent_ids: list[str] | None = None) -> None:
    """Cross-link a run with the ``tool.Workflow`` span that launched it.

    Stamps ``workflow_run_id`` + ``workflow_name`` onto the parent session's
    tool span (so the session view can render an inline "⚙ <name> · view run
    →" card) and ``parent_span_id`` onto the run root (so the run view's
    backlink deep-links to that exact tool call).

    Also re-parents the parent session's own ``subagent.start`` spans for this
    run's agents under that ``tool.Workflow`` span. Claude Code's SubagentStart
    hook records those in the *launching* session as orphans (``parent_id``
    NULL, carrying only ``agent_id``/``agent_type``) — so the session view
    floated them as siblings of the tool call. Matching ``agent_id`` against
    the run's manifest agent set makes the parent→child edge real in the DB,
    so the timeline nests + lazily folds them natively instead of the frontend
    grafting them by time window.
    Best-effort and idempotent: a missing script, an unmatched call, or a
    parent span that hasn't been ingested yet just skips silently — the
    ``parent_trace_id`` already on the root keeps the backlink working
    regardless.

    One-shot dependency: a terminal run is ingested once (the watcher gates
    on the now-stable manifest mtime), so the forward stamp only lands if
    the parent ``tool.Workflow`` span already exists then. In practice the
    PostToolUse hook records it at *launch* — long before completion — so it
    does. If it were ever absent, only the forward "view run →" link is lost;
    the backlink still resolves via ``parent_trace_id``.
    """
    parent_trace_id = run_ref.session_dir.name
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        parent_span_id = _resolve_parent_tool_span(conn, run_ref, parent_trace_id)
        if parent_span_id is None:
            return
        conn.execute(
            "UPDATE session_spans SET attributes = json_set("
            "attributes, '$.workflow_run_id', ?, '$.workflow_name', ?) "
            "WHERE trace_id = ? AND span_id = ?",
            (run_ref.run_id, workflow_name, parent_trace_id, parent_span_id))
        conn.execute(
            "UPDATE session_spans "
            "SET attributes = json_set(attributes, '$.parent_span_id', ?) "
            "WHERE trace_id = ? AND name = 'session.start'",
            (parent_span_id, run_ref.run_id))
        # Re-parent this run's launching-session subagents under the tool span.
        _reparent_session_subagents(
            conn, parent_trace_id, parent_span_id, agent_ids)
        conn.commit()
    finally:
        conn.close()


def _resolve_parent_tool_span(conn, run_ref, parent_trace_id: str) -> str | None:
    """span_id of the launching ``tool.Workflow`` call for this run, or None.

    Two resolution paths, most-durable first:
      1. The span already carries ``workflow_run_id`` — stamped live at
         PostToolUse from the tool result. Transcript-independent, so it
         survives any later compaction.
      2. Resolve a ``tool_use_id`` from the parent transcript (exact script
         match, then the run-dir reference in the tool result) and look the
         span up by that. The legacy path for spans captured before (1).
    """
    row = conn.execute(
        "SELECT span_id FROM session_spans WHERE trace_id = ? "
        "AND name = 'tool.Workflow' "
        "AND json_extract(attributes, '$.workflow_run_id') = ?",
        (parent_trace_id, run_ref.run_id)).fetchone()
    if row is not None:
        return row[0]
    if run_ref.script_path is None or not run_ref.script_path.exists():
        return None
    try:
        script = run_ref.script_path.read_text(encoding="utf-8")
    except OSError:
        return None
    transcript = run_ref.session_dir.parent / f"{parent_trace_id}.jsonl"
    if not transcript.exists():
        return None
    tool_use_id = (_find_parent_tool_use(transcript, script)
                   or _find_parent_tool_use_by_run_id(transcript, run_ref.run_id))
    if not tool_use_id:
        return None
    row = conn.execute(
        "SELECT span_id FROM session_spans WHERE trace_id = ? "
        "AND name = 'tool.Workflow' "
        "AND json_extract(attributes, '$.tool_use_id') = ?",
        (parent_trace_id, tool_use_id)).fetchone()
    return row[0] if row is not None else None


def _reparent_session_subagents(conn, parent_trace_id: str, parent_span_id: str,
                                agent_ids: list[str] | None) -> None:
    """Set ``parent_id`` of the launching session's ``subagent.start`` spans to
    the run's ``tool.Workflow`` span, matched by ``agent_id``.

    Scoped to the run's own agent set, so a session that launched several
    workflows nests each run's agents under its own tool call. Idempotent — a
    re-ingest just re-asserts the same parent edge.
    """
    ids = [a for a in (agent_ids or []) if a]
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        "UPDATE session_spans SET parent_id = ? "
        "WHERE trace_id = ? AND name = 'subagent.start' "
        f"AND json_extract(attributes, '$.agent_id') IN ({placeholders})",
        (parent_span_id, parent_trace_id, *ids))


def reingest(run_id: str, spans: list[dict]) -> tuple[int, int]:
    """Clear then re-insert a run's spans via the shared ingest service.

    Returns ``(ingested, skipped)`` from
    `lib.trace.trace_service.ingest_session_spans`.
    """
    from lib.trace.trace_service import ingest_session_spans

    _clear_run(run_id)
    normalised = [(span, span["attributes"]) for span in spans]
    return ingest_session_spans(normalised)


def ingest_run(run_ref: RunRef, *, deep: bool = True,
               is_test: bool = False) -> tuple[int, int] | None:
    """Ingest one run: the manifest's full phase tree when it covers the live
    agent set (`RunRef.terminal`), else the journal-driven flat live tree."""
    if run_ref.terminal:
        manifest = _read_json(run_ref.manifest_path)
        if manifest is None or not manifest.get("runId"):
            return None
        stale = run_ref.snapshot_stale_since()
        spans = build_full_spans(
            manifest, run_ref.agents_dir, deep=deep, is_test=is_test,
            snapshot_stale_at=_iso(int(stale * 1000)) if stale else None)
        result = reingest(run_ref.run_id, spans)
        _, agent_ids = _manifest_agents(manifest)
        _set_session_tokens(run_ref.run_id,
                            _session_token_split(run_ref.agents_dir, agent_ids))
        _set_session_cost(run_ref.run_id, run_ref.agents_dir, agent_ids)
        name = manifest.get("workflowName")
        _set_session_title(run_ref.run_id, name or manifest.get("summary"))
        _set_session_origin(run_ref.run_id)
        _tag_workflow_sessions(run_ref.run_id, run_ref.session_dir.name)
        _stamp_parent_link(run_ref, name, agent_ids)
        return result
    spans = build_flat_spans(run_ref, deep=deep, is_test=is_test)
    result = reingest(run_ref.run_id, spans)
    started, _ = _journal_agents(_read_jsonl(run_ref.journal_path))
    _set_session_tokens(run_ref.run_id,
                        _session_token_split(run_ref.agents_dir, started))
    _set_session_cost(run_ref.run_id, run_ref.agents_dir, started)
    name = _parse_script_meta(run_ref.script_path)[0]
    _set_session_title(run_ref.run_id, name)
    _set_session_origin(run_ref.run_id)
    _tag_workflow_sessions(run_ref.run_id, run_ref.session_dir.name)
    _stamp_parent_link(run_ref, name, started)
    return result


def ingest_all(*, deep: bool = True) -> dict:
    """Ingest every discoverable run once. Returns a small summary dict."""
    summary = {"runs": 0, "spans": 0, "failed": 0}
    for run_ref in discover_runs():
        try:
            result = ingest_run(run_ref, deep=deep)
        except Exception:
            summary["failed"] += 1
            log.error("workflow_run_ingest_failed", exc_info=True,
                      run_id=run_ref.run_id)
            continue
        if result is not None:
            summary["runs"] += 1
            summary["spans"] += result[0]
            log.write("workflow_run_ingested", run_id=run_ref.run_id,
                      terminal=run_ref.terminal, spans=result[0])
    return summary


def watch(poll_seconds: float = 5.0, *, deep: bool = True, stop=None) -> None:
    """Poll for new/changed runs and (re)ingest them until ``stop`` is set.

    Re-ingest is gated on state mtime + terminal-ness so an unchanged run
    is skipped: a live run re-ingests (flat) only when its journal grows,
    and the one-time deep parse happens on the single completion pass.
    Intended to run in a daemon thread started by ``regin serve``.
    """
    seen: dict[str, tuple[float, bool]] = {}
    while stop is None or not stop.is_set():
        for run_ref in discover_runs():
            mtime = run_ref.state_mtime()
            if seen.get(run_ref.run_id) == (mtime, run_ref.terminal):
                continue
            try:
                ingest_run(run_ref, deep=deep)
                seen[run_ref.run_id] = (mtime, run_ref.terminal)
            except Exception:
                log.error("workflow_run_ingest_failed", exc_info=True,
                          run_id=run_ref.run_id)
        if stop is not None:
            stop.wait(poll_seconds)
        else:
            time.sleep(poll_seconds)
