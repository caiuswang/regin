"""Rendering layer for the memory tree-walk: root → expand → fetch (+ read).

These are plain functions returning the exact text a caller shows an agent, so
the walk has two front-ends over one implementation: the `memory` MCP server
(`mcp__memory__index_root`, …) for harnesses that speak MCP, and
`regin memory index-root|index-expand|index-fetch|read` for every harness that
can only run a shell command. Splitting them would let the two drift, and the
CLI exists precisely so a non-MCP harness can run the same recall arm.

Every regin import stays local to a function: the MCP server is spawned per
session and must list its tools without paying for the store/reflect layers,
which importing `lib.memory` at module scope would pull in.
"""

from __future__ import annotations

from typing import Optional

DISABLED = "agent memory is disabled (settings.agent_memory.enabled)"

#: Leads of every "nothing to show you" render. A caller that fingerprints the
#: walk for the anti-skip gate must not count these: `read <typo>` did not walk
#: anything, and certifying it as a walk is a one-command bypass of the gate.
_MISS_LEADS = (
    DISABLED,
    "no topic node ",
    "no memory ",
    "no part ",
    "topic graph has no nodes",
    "no stored experience matched",
)


#: Both front-ends share these strings, so neither may name only its own call
#: form — a CLI-only harness told to "call index_root" has nothing to call.
_NO_NODE = ("no topic node {node_id!r} — list the roots first (index_root, or "
            "`regin memory index-root`)")

#: The step-2 → step-3 handoff, and the walk's most load-bearing instruction.
_LEAF_HINT = ("\n\n(leaf — read its memories with index_fetch, or "
              "`regin memory index-fetch <node>`)")


def is_result(rendered: str) -> bool:
    """Did this render actually surface tree/memory content?"""
    return not rendered.startswith(_MISS_LEADS)


def _part_index(rest: str, parts: list) -> str:
    """The `memory_read` follow-up line: how much was withheld and, when the
    body carries authored seams, what they are. Names are the addresses
    `memory_read(part=…)` accepts."""
    if parts:
        listed = " · ".join(f"{name} ({len(text)}ch)" for name, text in parts)
        return (f"  ⋯ +{len(rest)} chars in {len(parts)} parts — "
                f'memory_read("{{id}}", part=…) / regin memory read {{id}} '
                f'--part …: {listed}')
    return (f"  ⋯ +{len(rest)} chars (no sections) — "
            f'memory_read("{{id}}") / regin memory read {{id}}')


def format_memory(m: dict, *, score: Optional[float] = None,
                  brief: bool = True) -> str:
    """One hit. `brief` returns the lead plus a part index instead of the
    whole body — the same addresses-not-contents contract `index_fetch`
    already honours, and the reason `recall` no longer dominates the memory
    token budget. The id is always shown: without it a caller cannot cite
    what it recalled to `regin goal feedback --included`."""
    head = (f"[{m['kind']}|{m['scope']}|score {score:.2f}]"
            if score is not None else f"[{m['kind']}|{m['scope']}]")
    # 8 chars, the prefix convention the inject block already displays: a
    # full 32-hex id costs ~20 tokens on every hit, and `get_dict` resolves
    # prefixes, so the short form is still a working address.
    head += f" (id: {m['id'][:8]})"
    title = f" — {m['title']}" if m.get("title") else ""
    # Provenance is a drill-down address, not something acted on inline — a
    # 36-char session UUID per hit is pure overhead in a survey. `brief=False`
    # (and `memory_read`) still carry it.
    src = ("" if brief else
           f" (from session {m['source_trace_id']})"
           if m.get("source_trace_id") else "")
    body = m["body"]
    if brief:
        from lib.memory import parts
        lead, rest = parts.split_lead(body)
        if rest:
            index = _part_index(rest, parts.named_parts(rest))
            body = lead + "\n" + index.replace("{id}", m["id"])
    return f"{head}{title}\n{body}{src}"


def render_recall(query: str, top_k: int = 5, scope: str = "",
                  reinforce: bool = True, brief: bool = True,
                  mode: str = "auto") -> str:
    """Semantic recall across the whole store, rendered for an agent."""
    import lib.memory as memory
    if not memory.enabled():
        return DISABLED
    hits = memory.recall(query, top_k=max(1, min(int(top_k), 20)),
                         scope=scope or None, mode=mode,
                         reinforce=bool(reinforce))
    if not hits:
        return "no stored experience matched this query"
    return "\n\n".join(
        format_memory(h.memory, score=h.score, brief=bool(brief))
        for h in hits)


def render_memory_read(memory_id: str, part: str = "") -> str:
    """One memory in full, or just the named part — the `recall` follow-up."""
    import lib.memory as memory
    if not memory.enabled():
        return DISABLED
    m = memory.get_store().get_dict(memory_id)
    if m is None:
        return f"no memory {memory_id!r} — check the id from the recall hit"
    if not part:
        return format_memory(m, brief=False)
    from lib.memory import parts as parts_mod
    # Resolve against the withheld portion first, because that is what the
    # recall hit's part index was built from. Searching the whole body would
    # hand back a section of the lead the caller already has, under a name and
    # char-count the index never advertised.
    text = (parts_mod.find_part(parts_mod.split_lead(m["body"])[1], part)
            or parts_mod.find_part(m["body"], part))
    if text is None:
        named = [name for name, _ in parts_mod.named_parts(m["body"])]
        available = f"available parts: {', '.join(named)}" if named else (
            "this memory has no named parts — omit `part` / `--part` "
            "to read it whole")
        return f"no part {part!r} in {memory_id} — {available}"
    return f"[{m['kind']}|{m['scope']}] (id: {m['id']}) — part {part!r}\n{text}"


def _load_graph():
    """The repo's approved topic graph (the taxonomy tree the index walks),
    plus the global meta-roots overlay (`skills` / `preferences`) so
    cross-repo skill-usage and preference memories are navigable from here."""
    from lib.settings import settings
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.meta_roots import merge_meta_roots
    return merge_meta_roots(
        load_authoritative_graph(str(settings.project_root)))


def _subtree_mem_count(store, graph, node_id: str, scope: str) -> int:
    from lib.topics.tree import subtree_ids
    ids = subtree_ids(graph, node_id)
    return len(store.memories_for_topic_subtree(ids, scope=scope or None))


def _format_card(card: dict, mem_count: Optional[int],
                 read_count: int = 0) -> str:
    shape = f"{card['child_count']} sub" if card["child_count"] else "leaf"
    mc = f", {mem_count} mem" if mem_count is not None else ""
    rc = f", read×{read_count}" if read_count else ""
    return (f"- {card['id']} · {card['label']} ({shape}{mc}{rc})"
            f"\n    {card['blurb']}")


def _orphan_nav_card(node_id: str, label: str, blurb: str, count: int) -> str:
    """The synthetic 'unfiled' bucket rendered as a nav card, so a memory with
    no authoritative-topic link is visible in the index walk (parity with the
    WebUI taxonomy tree) instead of hanging under no subtree. A leaf: its
    members are read with `index_fetch`, not descended into."""
    return _format_card(
        {"id": node_id, "label": label, "child_count": 0, "blurb": blurb},
        count)


def _wiki_section(node_id: str, read_count: int = 0) -> tuple[str, bool]:
    """Address of the curated per-topic wiki — the agent Reads it if it wants
    the narrative. We hand over the path, not the contents. Returns
    (section_text, wiki_exists); the bool lets index_fetch record an exposure
    only when a real wiki was actually surfaced. `read_count` annotates how
    many past sessions actually read this wiki (a battle-tested signal)."""
    from lib.settings import settings
    from lib.topics.wiki import wiki_dir
    if not (wiki_dir(settings.project_root) / f"{node_id}.md").exists():
        return "## wiki\n(none — bucket or un-accepted topic)", False
    consulted = f"read in {read_count} past session(s); " if read_count else ""
    return (f"## wiki\n.regin/topics/wiki/{node_id}.md  "
            f"({consulted}Read this for the full topic narrative)"), True


_REF_CAP = 12  # role-bearing anchors are enough; the wiki has the full file map


def _refs_section(node: dict) -> str:
    """High-signal source-file addresses (path + role). Role-bearing anchors
    first, capped — the full file list lives in the wiki, so we don't dump
    every low-signal path here."""
    refs = node.get("refs") or []
    if not refs:
        return "## source refs\n(none)"
    ranked = sorted(refs, key=lambda r: (r.get("role") in (None, ""),))
    shown = ranked[:_REF_CAP]
    lines = [f"  {r.get('path')} ({r.get('role') or '—'})" for r in shown]
    more = len(refs) - len(shown)
    tail = f"\n  … +{more} more (full file map in the wiki)" if more > 0 else ""
    return f"## source refs ({len(refs)})\n" + "\n".join(lines) + tail


def _memory_headline(m: dict) -> str:
    title = m.get("title") or (m.get("body") or "").strip()[:60] or "(untitled)"
    return f"- [{m['kind']}|imp {m.get('importance', 0):.1f}] {title}  (id: {m['id']})"


def _memories_section(store, ids: list[str], top_k: int) -> str:
    """Memory addresses (kind · title · id), importance-ranked and capped —
    labels for the agent to choose from, not a body dump. The agent reads a
    chosen one with `recall`."""
    total = len(ids)
    if not total:
        return "## memories\n(none linked under this subtree)"
    cap = max(1, min(int(top_k), 50))
    shown = [m for m in (store.get_dict(mid) for mid in ids[:cap]) if m]
    lines = "\n".join(_memory_headline(m) for m in shown)
    more = total - len(shown)
    tail = f"\n… +{more} more (raise top_k / --top-k)" if more > 0 else ""
    return (f"## memories ({total}, importance-ranked; titles only — "
            f"recall to read one)\n{lines}{tail}")


def render_index_root(scope: str = "") -> str:
    """The taxonomy roots as router cards — step 1 of the walk."""
    import lib.memory as memory
    from lib.topics.tree import build_tree, node_card
    if not memory.enabled():
        return DISABLED
    graph = _load_graph()
    store = memory.get_store()
    reads = store.wiki_read_counts()
    lines = [_format_card(node_card(graph, rid),
                          _subtree_mem_count(store, graph, rid, scope),
                          reads.get(rid, 0))
             for rid in build_tree(graph)["roots"]]
    # Surface the unfiled bucket only when it holds something — untopiced
    # memories hang under no subtree, so without this they're invisible here.
    orphans = store.orphaned_memory_ids(scope=scope or None)
    if orphans:
        lines.append(_orphan_nav_card(
            memory.ORPHAN_NODE_ID, memory.ORPHAN_LABEL, memory.ORPHAN_BLURB,
            len(orphans)))
    if not lines:
        return "topic graph has no nodes (run `regin topics scan`)"
    return ("top-level topics (then expand / fetch a node — index_expand / "
            "index_fetch, or `regin memory index-expand|index-fetch`):\n"
            + "\n".join(lines))


def render_index_expand(node_id: str, scope: str = "") -> str:
    """One node's card plus its children — step 2, the descend/stop decision."""
    import lib.memory as memory
    from lib.topics.tree import build_tree, node_card
    if not memory.enabled():
        return DISABLED
    if node_id == memory.ORPHAN_NODE_ID:
        orphans = memory.get_store().orphaned_memory_ids(scope=scope or None)
        return (f"{memory.ORPHAN_NODE_ID} · {memory.ORPHAN_LABEL} "
                f"({len(orphans)} mem in subtree)\n{memory.ORPHAN_BLURB}"
                + _LEAF_HINT)
    graph = _load_graph()
    card = node_card(graph, node_id)
    if card is None:
        return _NO_NODE.format(node_id=node_id)
    store = memory.get_store()
    self_mc = _subtree_mem_count(store, graph, node_id, scope)
    head = (f"{node_id} · {card['label']} "
            f"({self_mc} mem in subtree)\n{card['blurb']}")
    kids = build_tree(graph)["children"].get(node_id, [])
    if not kids:
        return head + _LEAF_HINT
    reads = store.wiki_read_counts()
    ranked = sorted(kids, key=lambda k: reads.get(k, 0), reverse=True)
    lines = [_format_card(node_card(graph, k),
                          _subtree_mem_count(store, graph, k, scope),
                          reads.get(k, 0))
             for k in ranked]
    return (head + "\n\nchildren (most-read wiki first):\n"
            + "\n".join(lines))


def render_index_fetch(node_id: str, top_k: int = 10, scope: str = "",
                       reinforce: bool = True) -> str:
    """A node's wiki path, source refs and memory titles — step 3, addresses."""
    import lib.memory as memory
    from lib.topics.tree import subtree_ids
    if not memory.enabled():
        return DISABLED
    if node_id == memory.ORPHAN_NODE_ID:
        store = memory.get_store()
        ids = store.orphaned_memory_ids(scope=scope or None)
        sections = ["## wiki\n(none — unfiled memories, not a topic)",
                    "## source refs\n(none)",
                    _memories_section(store, ids, top_k)]
        return (f"{memory.ORPHAN_NODE_ID} · {memory.ORPHAN_LABEL}\n\n"
                + "\n\n".join(sections))
    graph = _load_graph()
    node = (graph.get("topics") or {}).get(node_id)
    if node is None:
        return _NO_NODE.format(node_id=node_id)
    store = memory.get_store()
    ids = store.memories_for_topic_subtree(subtree_ids(graph, node_id),
                                           scope=scope or None)
    label = node.get("label") or node_id
    read_count = store.wiki_read_counts().get(node_id, 0)
    wiki_text, wiki_exists = _wiki_section(node_id, read_count)
    if wiki_exists and reinforce:
        store.bump_wiki_recall(node_id, signal="exposure")
    sections = [wiki_text, _refs_section(node),
                _memories_section(store, ids, top_k)]
    return f"{node_id} · {label}\n\n" + "\n\n".join(sections)
