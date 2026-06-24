"""Goal feedback — close the loop from a verified build back into memory.

The back half of the loop-engineering workflow (Slice 2). After a
`/goal-verified` run finishes, this records two things into the existing
memory store:

1. **Engagement, with a clean signal.** A lesson recalled into the
   roadmap (`goal preflight --with-lessons`) either *made it into the
   approved acceptance checklist* or it didn't. That inclusion is a far
   higher-precision "did this memory help" verdict than the trace-referent
   heuristic in `lib.memory.feedback.score_injection_usefulness` — there is
   no guessing whether a file the memory mentioned later appeared in a
   span; the human approved (or dropped) the lesson explicitly. Included
   lessons are reinforced via the store's own `reinforce`; dropped ones are
   left untouched so reflect's `_decay_chronically_ignored` handles decay —
   exactly the division of labour the existing feedback loop already uses.

2. **New lessons from failures.** Each acceptance item that FAILED in
   verification is a transferable rule the next session should know. It is
   written as a `lesson` memory (the same kind `send_to_user(type=lesson)`
   produces), tagged by area so the next roadmap recalls it. The skill
   phrases failures as rules (not episodes), so this stays LLM-free without
   reproducing the "diary entry" failure mode of heuristic distillation.

This module only *reuses* memory primitives (`remember`, `reinforce`); it
adds no store, table, or index of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Tag stamped on every failure-derived lesson, so they are filterable and
# the loop's own output is auditable.
FAIL_TAG = "goal-verified-fail"

# `source` recorded on the (memory, topic-node) link, so a hand-filed lesson
# is distinguishable in the audit log from distill/reflect auto-filing.
TOPIC_LINK_SOURCE = "goal-feedback"

# Explicit "no related topic" inputs for `--topic`. The agent walked the tree,
# found no matching node, and is deliberately filing the lesson *unbound*
# rather than letting a fuzzy keyword guess misfile it under a wrong node.
# Treated as an intentional skip (no warning), distinct from an unresolved id.
NO_TOPIC_TOKENS = frozenset({"none", "no-topic", "notopic", "null", "n/a", "na", "-"})


def _is_no_topic(raw: str) -> bool:
    """True if `raw` is an explicit no-topic sentinel (case-insensitive)."""
    return (raw or "").strip().lower() in NO_TOPIC_TOKENS


@dataclass
class OutcomeResult:
    """Summary of what the feedback pass changed in the store."""

    reinforced: list[str] = field(default_factory=list)
    unreinforced: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    new_lessons: list[str] = field(default_factory=list)
    linked_topics: list[str] = field(default_factory=list)
    unresolved_topics: list[str] = field(default_factory=list)
    skipped_topics: list[str] = field(default_factory=list)
    disabled: bool = False


def record_outcome(goal: str, *,
                   included_ids: list[str] | None = None,
                   offered_ids: list[str] | None = None,
                   failures: list[str] | None = None,
                   tags: list[str] | None = None,
                   topics: list[str] | None = None,
                   trace_id: str | None = None,
                   importance: float = 0.6,
                   is_test: bool = False) -> OutcomeResult:
    """Fold one `/goal-verified` outcome back into memory.

    `included_ids` — recalled lessons the agent folded into the approved
    roadmap (the engaged set). `offered_ids` — every lesson preflight
    surfaced; any offered-but-not-included id is recorded as ignored (no
    penalty here — decay is reflect's job). `failures` — acceptance items
    that failed verification, each phrased as a transferable rule; written
    as new lessons tagged by area + FAIL_TAG. `topics` — authoritative
    topic short-paths (node ids the agent already knows from its tree walk)
    to file every new failure-lesson under, so the next roadmap recalls it
    by subsystem instead of waiting for the async classifier. Returns what
    changed.
    """
    included = _dedup(included_ids or [])
    offered = _dedup(offered_ids or [])
    fails = [f.strip() for f in (failures or []) if f.strip()]

    result = OutcomeResult()

    import lib.memory as memory
    if not memory.enabled():
        result.disabled = True
        return result

    store = memory.get_store()
    result.reinforced, result.unreinforced = _reinforce_all(store, included)
    result.ignored = [mid for mid in offered if mid not in set(included)]
    result.new_lessons = _write_failures(
        memory, fails, tags=list(tags or []), importance=importance,
        trace_id=trace_id, is_test=is_test)
    (result.linked_topics, result.unresolved_topics,
     result.skipped_topics) = _link_topics(store, result.new_lessons, topics)
    return result


def _reinforce_all(store, ids: list[str]) -> tuple[list[str], list[str]]:
    """Reinforce each id; return (matched, missed). An id misses when it
    resolves to no memory — a bad id or an ambiguous prefix — so the count is
    truthful and the caller can warn instead of claiming false success (the
    inject block shows 8-char prefixes, so a hand-passed id often is one)."""
    matched: list[str] = []
    missed: list[str] = []
    for mid in ids:
        (matched if store.reinforce(mid) else missed).append(mid)
    return matched, missed


def _write_failures(memory, fails: list[str], *, tags: list[str],
                    importance: float, trace_id: str | None,
                    is_test: bool) -> list[str]:
    lesson_tags = _dedup(tags + [FAIL_TAG])
    return [
        memory.remember(fail, kind="lesson", tags=lesson_tags,
                        importance=importance, source_trace_id=trace_id,
                        is_test=is_test)
        for fail in fails
    ]


def _link_topics(store, memory_ids: list[str],
                 topics: list[str] | None,
                 ) -> tuple[list[str], list[str], list[str]]:
    """File each new failure-lesson under the agent-supplied topic short-paths.

    `topics` are node ids the agent already knows from its tree walk. An
    explicit no-topic sentinel (`none`, `-`, … see `NO_TOPIC_TOKENS`) is
    honoured as a *deliberate skip* — the agent walked the tree and found no
    related node, so the lesson is filed unbound rather than misrouted. Every
    other input resolves to one authoritative node (exact id or slashed leaf)
    and is linked to each id in `memory_ids`. Returns (linked_node_ids,
    unresolved_inputs, skipped_sentinels). Best-effort: a graph/link failure is
    reported as unresolved, never raised — filing must not break the write."""
    asked = _dedup(topics or [])
    skipped = [raw for raw in asked if _is_no_topic(raw)]
    wanted = [raw for raw in asked if not _is_no_topic(raw)]
    if not wanted or not memory_ids:
        # No real topics asked for, or no new lessons to file them under —
        # either way nothing is unresolved (a valid topic isn't "unresolved"
        # just because there was nothing to attach it to).
        return [], [], skipped

    nodes = _load_topic_nodes()
    if nodes is None:
        return [], wanted, skipped

    linked, unresolved = _file_under_topics(store, memory_ids, wanted, nodes)
    return linked, unresolved, skipped


def _load_topic_nodes() -> dict | None:
    """The authoritative topic graph's `topics` dict, or None if it can't be
    loaded — the signal to report every wanted topic as unresolved."""
    try:
        from lib.topics.route import load_authoritative_graph
        from lib.settings import settings
        return load_authoritative_graph(str(settings.project_root)).get("topics", {})
    except Exception:
        return None


def _file_under_topics(store, memory_ids: list[str], wanted: list[str],
                       nodes: dict) -> tuple[list[str], list[str]]:
    """Link each memory to each resolvable topic; collect the unresolvable
    ones. Returns (linked_node_ids, unresolved_inputs)."""
    linked: list[str] = []
    unresolved: list[str] = []
    for raw in wanted:
        node = _resolve_topic(nodes, raw)
        if node is None:
            unresolved.append(raw)
            continue
        for mid in memory_ids:
            store.link_authoritative_topic(mid, node, source=TOPIC_LINK_SOURCE)
        linked.append(node)
    return _dedup(linked), unresolved


def _resolve_topic(nodes: dict, raw: str) -> str | None:
    """Resolve one short-path to an authoritative node id, or None.

    Exact node id wins; else the last `/`-segment of a slashed path. There is
    deliberately **no fuzzy keyword fallback** here: this is an *authoritative
    write*, and `match_topic` is a short-query *read* heuristic that confidently
    misroutes on a single coincidental token (e.g. `--topic skills` →
    `add-new-agent-provider`). A wrong authoritative link is worse than none, so
    anything that is not an exact id or slashed leaf is left unresolved
    (reported, not filed) — pass a no-topic sentinel to skip on purpose, or fix
    the id against the tree. Never raises."""
    needle = (raw or "").strip()
    if not needle:
        return None
    if needle in nodes:
        return needle
    leaf = needle.rstrip("/").split("/")[-1]
    if leaf in nodes:
        return leaf
    return None


def _dedup(seq: list[str]) -> list[str]:
    return list(dict.fromkeys(seq))


def render_summary(result: OutcomeResult) -> str:
    """Human-readable one-block summary for the CLI."""
    if result.disabled:
        return "memory is disabled — nothing recorded."
    lines = [
        f"reinforced {len(result.reinforced)} lesson(s) folded into the roadmap",
        f"left {len(result.ignored)} offered-but-unused lesson(s) to decay",
        f"wrote {len(result.new_lessons)} new lesson(s) from failures",
    ]
    if result.new_lessons:
        lines.append("  new lesson ids: " + ", ".join(result.new_lessons))
    if result.linked_topics:
        lines.append(
            f"  filed new lesson(s) under {len(result.linked_topics)} topic(s): "
            + ", ".join(result.linked_topics))
    if result.skipped_topics and result.new_lessons:
        lines.append(
            "  left new lesson(s) unfiled at your request (no related topic): "
            + ", ".join(result.skipped_topics))
    if result.unresolved_topics:
        lines.append(
            f"  ⚠ {len(result.unresolved_topics)} --topic short-path(s) matched no "
            f"node (not filed — check the id against the tree): "
            + ", ".join(result.unresolved_topics))
    if result.unreinforced:
        lines.append(
            f"  ⚠ {len(result.unreinforced)} --included id(s) matched NOTHING "
            f"(not reinforced — bad id or ambiguous prefix): "
            + ", ".join(result.unreinforced))
    return "\n".join(lines)


def outcome_to_dict(result: OutcomeResult) -> dict:
    return {
        "reinforced": result.reinforced,
        "unreinforced": result.unreinforced,
        "ignored": result.ignored,
        "new_lessons": result.new_lessons,
        "linked_topics": result.linked_topics,
        "unresolved_topics": result.unresolved_topics,
        "skipped_topics": result.skipped_topics,
        "disabled": result.disabled,
    }
