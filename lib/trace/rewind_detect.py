"""Recover Claude Code `/rewind` events from a transcript's parentUuid graph.

A `/rewind` never writes an explicit marker. It surfaces as a **fork**: the
user rewound to an earlier message and submitted a new prompt, so the
discarded turns and the new (live) turn share the same `parentUuid`. The
discarded branch is left in the file, orphaned — nothing on the live-leaf
chain descends from it.

The naive "any parent with 2+ children" test is wrong: a single transcript
has many legitimate same-parent siblings — an assistant `tool_use` entry and
the `tool_result` user entry that answers it, sidechain subagent entries, and
near-simultaneous attachment rows. On the real session `cbd00068` that test
yields 13 forks, 12 of them false. A true rewind's **orphan subtree contains a
real prompt** (a user entry that opened a turn — the same `real_prompt_uuids`
set the parser already classifies). A bare interrupt, a `tool_result`, or an
`away_summary` that stays on the live chain never trips it, so a normal
interrupt-then-continue (same branch) is not mistaken for a rewind.

A real prompt alone is *not* enough, though: a user who edits a just-submitted
prompt before any response arrives (fixing a typo, sharpening the ask) also
forks off a discarded branch carrying that abandoned prompt — but with **no
assistant turn under it**, nothing was actually discarded. Surfacing that as a
"REWOUND · 1 prompt discarded" marker is pure noise (the discarded branch has
no captured turns to show). So the second half of the discriminator is that the
orphan subtree must also contain a real assistant turn (`entry_kind == 'assistant'`,
synthetic banners already excluded): a rewind worth surfacing is one that threw
away *work*, not just a re-typed line. On session `f0518744` this drops two
phantom forks (both bare prompt edits) to zero.

The live branch is identified by walking to root from the union of the last
`last-prompt` row's `leafUuid` and the file-tail uuid — seeding from both
keeps the old active branch live while a new branch is only half-written
(mid-rewind), so we never prematurely abandon the soon-to-be-live branch.

Pure module: no DB, no Flask, no disk. Code-rollback enrichment
(`rolled_back_files`) is computed separately in the parser's `finalize` via
`lib.trace.file_history` and grafted onto each fork with
`dataclasses.replace`.
"""

from __future__ import annotations

from collections.abc import Iterable

from lib.trace.transcript_models import RewindFork


def _live_chain(
    seeds: Iterable[str],
    entry_parent: dict[str, str | None],
) -> set[str]:
    """Every uuid on the live branch: the union of the parentUuid chains
    walked to root from each seed. Cycle-guarded per walk so a malformed
    transcript can't loop (mirrors `transcript_parsers._walk_to_prompt`)."""
    live: set[str] = set()
    for seed in seeds:
        cursor: str | None = seed
        while cursor is not None and cursor not in live:
            live.add(cursor)
            cursor = entry_parent.get(cursor)
    return live


def _orphan_subtree(
    root: str,
    children: dict[str, list[str]],
) -> list[str]:
    """All uuids in the subtree rooted at `root`, in discovery order.
    Iterative DFS, visited-guarded against malformed back-edges."""
    out: list[str] = []
    seen: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        out.append(node)
        # Reverse so children are emitted in their original order.
        stack.extend(reversed(children.get(node, [])))
    return out


def _fork_at(
    node: str,
    orphan_root: str,
    on_chain_child: str | None,
    children: dict[str, list[str]],
    real_prompt_uuids: set[str],
    entry_kind: dict[str, str],
    order: dict[str, int],
    ts: dict[str, str],
) -> RewindFork | None:
    """Build a `RewindFork` for one off-chain branch, or None when the
    branch is not a real rewind: it carries no real prompt (a tool_result /
    attachment / sidechain sibling), or it carries a prompt but no assistant
    turn (a prompt edited/resubmitted before any response — nothing was
    actually discarded)."""
    subtree = _orphan_subtree(orphan_root, children)
    abandoned_prompts = [u for u in subtree if u in real_prompt_uuids]
    if not abandoned_prompts:
        return None
    if not any(entry_kind.get(u) == 'assistant' for u in subtree):
        return None
    abandoned_prompts.sort(key=lambda u: order.get(u, 0))
    return RewindFork(
        fork_uuid=node,
        orphan_root=orphan_root,
        orphan_uuids=frozenset(subtree),
        abandoned_prompt_uuids=tuple(abandoned_prompts),
        live_child_uuid=on_chain_child,
        fork_timestamp=ts.get(node) or ts.get(orphan_root),
    )


def _forks_for_node(
    node: str,
    children: dict[str, list[str]],
    live: set[str],
    real_prompt_uuids: set[str],
    entry_kind: dict[str, str],
    order: dict[str, int],
    ts: dict[str, str],
) -> list[RewindFork]:
    """Every rewind fork hanging off one live node (≥1 only when the user
    rewound to it). Empty for the common case of a node with no off-chain
    children or only tool_result/attachment siblings."""
    live_children = children.get(node)
    if not live_children:
        return []
    on_chain_child = next((c for c in live_children if c in live), None)
    out: list[RewindFork] = []
    for orphan_root in (c for c in live_children if c not in live):
        fork = _fork_at(
            node, orphan_root, on_chain_child,
            children, real_prompt_uuids, entry_kind, order, ts,
        )
        if fork is not None:
            out.append(fork)
    return out


def detect_rewinds(
    entry_parent: dict[str, str | None],
    entry_kind: dict[str, str],
    real_prompt_uuids: set[str],
    live_leaf_uuids: Iterable[str],
    *,
    entry_ts: dict[str, str] | None = None,
) -> list[RewindFork]:
    """Find every `/rewind` fork in a parsed transcript graph.

    Args:
        entry_parent: uuid → parentUuid, in file order (insertion order is
            relied on for `abandoned_prompt_uuids` ordering).
        entry_kind: uuid → kind ('assistant' for real assistant turns,
            synthetic banners excluded). Used both to scope which orphan
            uuids back tool spans and as the second discriminator half — a
            fork is only real if its orphan subtree holds an assistant turn.
            May be partial for non-assistant kinds.
        real_prompt_uuids: uuids of user entries that opened a turn — the
            discriminator set.
        live_leaf_uuids: seeds for the live branch (last `last-prompt`
            leafUuid + file-tail uuid). Missing/None seeds are tolerated.
        entry_ts: uuid → ISO timestamp, for the marker's position.

    Returns one `RewindFork` per discarded branch, ordered by fork
    appearance in the file. `rolled_back_files` is left empty here.
    """
    seeds = [u for u in live_leaf_uuids if u]
    if not seeds:
        return []

    children: dict[str, list[str]] = {}
    for uuid, parent in entry_parent.items():
        if parent is not None:
            children.setdefault(parent, []).append(uuid)

    live = _live_chain(seeds, entry_parent)
    ts = entry_ts or {}
    # File-order index for stable ordering of forks and abandoned prompts.
    order = {uuid: i for i, uuid in enumerate(entry_parent)}

    forks: list[RewindFork] = []
    for node in live:
        forks.extend(
            _forks_for_node(
                node, children, live, real_prompt_uuids, entry_kind, order, ts,
            )
        )

    forks.sort(key=lambda f: order.get(f.orphan_root, 0))
    return forks


def orphan_turn_uuids(forks: Iterable[RewindFork], entry_kind: dict[str, str]) -> set[str]:
    """Assistant uuids across all discarded branches — the set a tool span's
    `turn_uuid` is matched against to flag it `rewound_away` (tool spans are
    keyed by their issuing turn, not their own uuid)."""
    out: set[str] = set()
    for fork in forks:
        out.update(u for u in fork.orphan_uuids if entry_kind.get(u) == 'assistant')
    return out
