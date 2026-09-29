"""Static extraction of phase / agent declarations from a workflow run's script."""

from __future__ import annotations

from pathlib import Path


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
