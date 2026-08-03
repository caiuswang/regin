"""Trace-derived gates for *unenforced* skill steps.

regin doesn't own the agent loop, so a skill step it can't enforce (e.g.
`goal-verified-treenav`'s memory-tree-nav recall arm) is honour-system — unless
its tool leaves a fingerprint in the trace. Every MCP/tool call is persisted as
a `session_spans` row, so "did the step run?" collapses to "did its spans appear
for this session?" — a cheap, scriptable check the `regin gate` CLI exposes.

This module is the single source of truth for those span fingerprints, so the
patterns live in code (typed, testable) instead of copy-pasted SQL inside a
`SKILL.md` heredoc. Add a gate by appending a `SpanGate` to `GATES`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpanGate:
    """One named "did this tool run?" check, expressed as span-name matchers.

    `like` holds SQL `LIKE` patterns and `exact` holds literal span names; a
    span matches the gate if it matches *any* entry in either. `describe` is the
    human label shown in CLI help and the failure message.
    """

    key: str
    like: tuple[str, ...] = ()
    exact: tuple[str, ...] = ()
    # `(span name, attribute key, substring)` triples, for steps whose tool is
    # generic — `tool.Read` proves nothing on its own, only *what it read*
    # does. The substring is tested against the named attribute AFTER parsing,
    # never against the raw JSON: Read spans embed the file's whole content
    # there, so a raw match counts any file that mentions the path.
    attr_matchers: tuple[tuple[str, str, str], ...] = ()
    describe: str = ""
    # What must be installed for the gated step to be *runnable* at all. A
    # count of 0 only means "you skipped it" once this is known present —
    # otherwise it may just mean the tool was never there, and reporting the
    # two identically is what made the retired `ui-verified` gate unpassable.
    capability: str = ""
    # True when running regin proves the capability (the span comes from a
    # regin CLI command, so anyone who can invoke the gate could have invoked
    # the step). False when the capability is an MCP server that may or may
    # not be loaded in the caller's session.
    capability_self_evident: bool = False
    # True when the gated tools are served by the *memory* MCP server, which
    # also serves `mcp__memory__gate`. Reaching that tool then proves this
    # gate's capability — one FastMCP instance, so if the gate was callable
    # the step's tools were too. Only that path may honour this flag; the CLI
    # cannot see which MCP servers a session loaded.
    served_by_memory_mcp: bool = False


#: Where `regin memory export-tree` writes the navigable memory tree. A
#: `tool.Read`/`tool.Glob` naming a path under it is a tree-walk step.
_TREE_SEG = ".regin/memory/tree"

# The recall arm has two legs and either one proves it ran:
#   - the filesystem walk over the exported tree (a tool.Read whose file_path
#     is inside the tree dir) — the cheap navigation leg;
#   - the memory MCP server's semantic leg (tool.mcp__memory__recall,
#     memory_read, and the legacy index_* walk) — see lib/memory/mcp_server.py;
#   - the same walk driven from the CLI (`regin memory index-root|index-expand|
#     index-fetch|read|recall --session`), which emits `memory.index.nav`. That
#     leg is what keeps this gate passable on a harness with no MCP at all: the
#     renderer is shared (lib/memory/tree_nav.py), so it is the same walk, and
#     an agent that did it honestly must not read as a skip.
#
# `tool.Glob` is deliberately NOT a matcher: a Glob span records only its
# pattern, never its result count, so globbing a tree that was never exported
# would pass a gate on a walk that saw nothing. A Read proves a file existed
# and was opened.
# The MCP span name carries the SERVER's registered name, which is not fixed:
# a directly-configured server lands as `mcp__memory__*`, the same server
# shipped through the plugin lands as `mcp__plugin_regin-agents_memory__*`.
# Matching the direct prefix only made 59 real nav calls across 8 sessions
# invisible, and because `mcp__…__gate` is reached under the same prefix,
# `served_by_memory_mcp` proved the capability — so an honest walk got the
# hardest verdict the module has: "the step was skipped. Go back and run it."
# Match on the server-name-agnostic middle instead.
RECALL_ARM = SpanGate(
    key="recall-ran",
    like=("tool.mcp__%memory__index_%",
          "tool.mcp__%memory__recall",
          "tool.mcp__%memory__memory_read"),
    exact=("memory.index.nav",),
    attr_matchers=(("tool.Read", "file_path", _TREE_SEG),),
    describe="memory tree-walk / recall arm (goal-verified-treenav step 1b)",
    capability=("Read/Glob over .regin/memory/tree, the `regin memory "
                "index-*` CLI, or the memory MCP server"),
    # Self-evident since the walk moved to the filesystem: Read and Glob are
    # core tools present in every session, so a session that produced neither
    # a tree read nor a recall genuinely skipped the step. (Before, the arm
    # was MCP-only and 0 spans in an MCP-less session proved nothing — hence
    # the INCONCLUSIVE path, which now only triggers for gates that are still
    # capability-contingent.)
    capability_self_evident=True,
    served_by_memory_mcp=True,
)

# `regin memory recall-for-task` emits one `memory.recall.task` span per call
# (see cli/commands/memory.py:cmd_recall_for_task). This gate proves the
# task-scoped recall arm fired — the spawner-baked recall the goal-verified loop
# relies on. v0 is session-level ("did any task-recall happen this session"),
# not per-stage correlation.
TASK_RECALL = SpanGate(
    key="task-recall-ran",
    exact=("memory.recall.task",),
    describe="task-scoped recall (goal-verified recall arm)",
    capability="the regin CLI (`regin memory recall-for-task`)",
    # Self-evident everywhere: the span comes from a regin CLI command, so any
    # caller able to reach this gate could have run the step. 0 spans is a
    # genuine skip, never an absent instrument.
    capability_self_evident=True,
)

# A `ui-verified` gate lived here: it counted Playwright *MCP* browser spans to
# prove a UI goal was rendered rather than asserted from the diff. Removed —
# its premise ("the browser MCP is available in every regin session") turned out
# to be false, and a gate that cannot distinguish "you skipped the render" from
# "the instrument was absent" fails identically in both cases. That is worse
# than no gate: the only way past it is for the agent to talk itself out of a
# red gate, which is a habit that does not stay confined to one gate.
#
# The invariant it stood for now has a real enforcer that needs no MCP: the
# no-horizontal-overflow cases in `frontend/tests/responsive.spec.js` (detectors
# in `tests/helpers/overflow.js`) assert it at mobile/tablet/desktop widths on
# every run, and `scripts/dom-measure.mjs --overflow [--baseline]` reports the
# same measurement interactively. Prefer extending those over reviving a
# tool-presence proxy.

GATES: dict[str, SpanGate] = {
    g.key: g for g in (RECALL_ARM, TASK_RECALL)
}


#: Exit code / status for a gate whose spans are absent but whose capability
#: could not be shown to have been present. Distinct from FAIL on purpose: it
#: means "no evidence either way", and it must never read as PASS.
INCONCLUSIVE = "INCONCLUSIVE"
PASS = "PASS"
FAIL = "FAIL"

STATUS_EXIT = {PASS: 0, FAIL: 1, INCONCLUSIVE: 2}


def verdict(gate: SpanGate, count: int, capability_proven: bool) -> tuple[str, str]:
    """Resolve (status, human message) for a gate result.

    Shared by the `regin gate` CLI and the `mcp__memory__gate` MCP tool so the
    two can't drift into disagreeing about what 0 spans means.

    `capability_proven` answers "do we know the gated step was even runnable
    here?". When it is False and no spans exist, the honest answer is
    INCONCLUSIVE, not FAIL: telling an agent "you skipped the step, go back
    and run it" when the tool was never installed is an instruction it cannot
    follow, and the only way past an unfollowable gate is to argue around a
    red one. That is the failure that retired the `ui-verified` gate.
    """
    if count > 0:
        return PASS, "GATE PASS — arm ran"
    if capability_proven:
        return FAIL, (
            "GATE FAIL — no spans for this gate, and its tools WERE available "
            f"({gate.capability}); the step was skipped. Go back and run it."
        )
    return INCONCLUSIVE, (
        "GATE INCONCLUSIVE — no spans, but this path cannot show that "
        f"{gate.capability} was present, so 0 proves nothing. Re-check from a "
        "context that establishes the capability (for recall-ran, the "
        "`mcp__memory__gate` tool: reaching it proves the arm's tools were "
        "loaded). Do NOT record this as a pass."
    )


#: How long a gate read may wait on a busy database before giving up. Two
#: seconds, not the ORM's five: a gate sits on the critical path of a shipping
#: loop, and an answer it cannot get quickly is worth less than not blocking.
_GATE_BUSY_TIMEOUT_MS = 2000


def unresolved_session_id(session: str | None) -> str | None:
    """The reason `session` cannot identify a trace, or None if it looks usable.

    An MCP tool cannot expand a shell variable, so a caller that passes
    `"$CLAUDE_CODE_SESSION_ID"` as a literal gets a guaranteed 0 — read as
    "you skipped the step" rather than "you passed me a variable name". Seen
    in the trace; cheap to catch here rather than in each caller.
    """
    text = (session or "").strip()
    if not text:
        return "no session id"
    if text.startswith("$") or text.startswith("${"):
        return (f"the literal string {text!r} — a shell variable that was "
                "never expanded (an MCP tool cannot expand one; pass the "
                "resolved id)")
    return None


def span_count(trace_id: str, gate: SpanGate) -> int:
    """Count `session_spans` for `trace_id` whose name matches `gate`.

    Reads through a short-lived **read-only** connection when it can. The
    read-write path (`SessionLocal`) issues `PRAGMA journal_mode = WAL` on
    every connect and checkpoints the WAL when the last connection closes;
    against the live multi-GB DB with hook writers active, both can block far
    past any `busy_timeout`, which covers statement contention only. Two gate
    calls measured at 120s and 300s that way — the 300s one was killed by
    `timeout` and never returned an answer at all, while the query itself is
    ~20ms. A gate is a read on the critical path of a shipping loop; it must
    not be able to hold that loop open.

    Falls back to the ORM when read-only open fails (a WAL DB with no `-shm`
    and no live writer — the common shape in tests). Returns 0 for a gate with
    no matchers (never matches everything by accident).
    """
    from sqlalchemy import func, or_
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models.trace import SessionSpan

    conds = [SessionSpan.name.like(p) for p in gate.like]
    conds += [SessionSpan.name == n for n in gate.exact]
    if not conds and not gate.attr_matchers:
        return 0

    readonly = _span_count_readonly(trace_id, gate)
    if readonly is not None:
        return readonly

    total = 0
    with SessionLocal() as session:
        if conds:
            stmt = (select(func.count(SessionSpan.id))
                    .where(SessionSpan.trace_id == trace_id)
                    .where(or_(*conds)))
            total += int(session.exec(stmt).one())
        for name, key, substring in gate.attr_matchers:
            # The LIKE is only a prefilter. It cannot be the test: a Read span
            # stores the file's whole CONTENT in `attributes`, so matching the
            # raw JSON counts any file that merely *mentions* the path — a
            # session that read this skill's own docs would pass the anti-skip
            # gate without walking anything. Decide on the parsed key instead,
            # exactly as `lib/memory/wiki_reads.py` does.
            rows = session.exec(
                select(SessionSpan.attributes)
                .where(SessionSpan.trace_id == trace_id)
                .where(SessionSpan.name == name)
                .where(SessionSpan.attributes.like(f"%{substring}%"))).all()
            total += sum(1 for attrs in rows
                         if substring in _attr_value(attrs, key))
    return total


def _span_count_readonly(trace_id: str, gate: SpanGate) -> int | None:
    """`span_count`'s read-only leg, or None when it can't open the DB.

    None means "use the ORM instead", not "no spans" — a gate must never turn
    an infrastructure problem into an accusation that the agent skipped a step.
    """
    import sqlite3

    from lib.orm.engine import DB_PATH

    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        conn.execute(f"PRAGMA busy_timeout = {_GATE_BUSY_TIMEOUT_MS}")
        return (_count_by_name(conn, trace_id, gate)
                + _count_by_attr(conn, trace_id, gate))
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _count_by_name(conn, trace_id: str, gate: SpanGate) -> int:
    """Spans matching the gate's `like`/`exact` name patterns."""
    clauses = ([("name LIKE ?", p) for p in gate.like]
               + [("name = ?", n) for n in gate.exact])
    if not clauses:
        return 0
    where = " OR ".join(clause for clause, _ in clauses)
    params = [trace_id] + [value for _, value in clauses]
    return conn.execute(
        f"SELECT count(*) FROM session_spans "
        f"WHERE trace_id = ? AND ({where})", params).fetchone()[0]


def _count_by_attr(conn, trace_id: str, gate: SpanGate) -> int:
    """Spans matching the gate's `attr_matchers`. The SQL LIKE is only a
    prefilter — see the note in `span_count`'s ORM leg for why the decision
    has to be made on the parsed attribute."""
    total = 0
    for name, key, substring in gate.attr_matchers:
        rows = conn.execute(
            "SELECT attributes FROM session_spans "
            "WHERE trace_id = ? AND name = ? AND attributes LIKE ?",
            (trace_id, name, f"%{substring}%")).fetchall()
        total += sum(1 for (attrs,) in rows
                     if substring in _attr_value(attrs, key))
    return total


def _attr_value(attributes: str, key: str) -> str:
    """One span-attribute value as text, or "" when absent/unparseable."""
    import json

    try:
        return str(json.loads(attributes or "{}").get(key) or "")
    except (ValueError, TypeError):
        return ""
