"""Git-shareable markdown export/import for the agent-memory store.

Mirrors how `.regin/topics/topics/` + `.regin/topics/wiki/*.md` already
share the topic taxonomy: memories are written as one markdown file per
authoritative topic node, nested into the same directory shape the topic
tree has (`node_path`), so a team can review and merge curated lessons via
normal git diffs instead of a shared database.

A memory linked to several topic nodes gets exactly one **canonical** file
(under the lexicographically-smallest linked node — deterministic so a
re-export never reshuffles which copy holds the body) plus a frontmatter-only
**stub** at every other linked node, so the lesson text is never duplicated
on disk. A memory with no authoritative link at all lands under the
reserved `_unfiled/` directory (distinct from the topic graph's own
`unclassified` bucket, which is a real routable node).

Import is the inverse, two-pass: canonical files upsert the `Memory` row
(by explicit id, via `SqliteMemoryStore.import_memory`) and link it to the
node its directory names; stub files (deferred to pass 2, since a stub may
be read before its canonical counterpart) link the same memory id to their
own directory's node.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Optional

import yaml

from lib.activity_log import get_activity_logger
from lib.memory.models import MemoryInput
from lib.memory.store import title_from_body
from lib.topics.tree import UNCLASSIFIED, effective_parent, is_bucket

log = get_activity_logger("memory")

DEFAULT_TREE_DIR = ".regin/memory/tree"
UNFILED_DIR = "_unfiled"

# Frontmatter block: "---\n<yaml>---\n" optionally followed by "\n<body>\n".
# Anchored at the start of the file (frontmatter is always first), so a
# literal "---" line inside the body itself can't confuse the split.
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)

_SLUG_SAFE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 60) -> str:
    """Filesystem-safe slug: lowercase `[a-z0-9-]`, repeats collapsed, ends
    trimmed. Falls back to `"untitled"` for text with no safe characters."""
    slug = _SLUG_SAFE.sub("-", (text or "").lower()).strip("-")
    slug = slug[:max_len].strip("-")
    return slug or "untitled"


def node_path(graph: dict, node_id: str) -> list[str]:
    """Root-first chain of node ids from the containing bucket down to
    `node_id` (inclusive); `[node_id]` alone if it IS a bucket. Falls back
    to `UNCLASSIFIED` when the chain is unplaced (null/dangling/cyclic
    `parent_id`), same as `effective_parent`. Cycle-safe via a `seen` guard
    independent of (but consistent with) `effective_parent`'s own."""
    topics = graph.get("topics") or {}
    node = topics.get(node_id)
    if node is None or is_bucket(node):
        return [node_id]
    buckets = {tid for tid, n in topics.items() if is_bucket(n)}
    chain = [node_id]
    seen = {node_id}
    cur = node_id
    while True:
        parent = effective_parent(topics, buckets, cur)
        if parent == UNCLASSIFIED or parent in seen:
            chain.append(UNCLASSIFIED)
            break
        chain.append(parent)
        if parent in buckets:
            break
        seen.add(parent)
        cur = parent
    chain.reverse()
    return chain


def _atomic_write(path: Path, data: str) -> None:
    """Write-tmp + fsync + rename, so a reader never sees a partial file.
    Duplicated (not imported) from `lib.topics.graph_io._atomic_write`, per
    this package's stated convention of staying self-contained."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                              dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_memory_file(path: Path, frontmatter: dict, body: str = "") -> None:
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=True,
                               default_flow_style=False, allow_unicode=True)
    content = f"---\n{yaml_text}---\n"
    if body:
        content += f"\n{body}\n"
    _atomic_write(path, content)


def _parse_memory_file(path: Path) -> tuple[dict, str]:
    content = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}, content
    frontmatter = yaml.safe_load(m.group(1)) or {}
    return frontmatter, m.group(2).strip("\n")


def _memory_filename(mem: dict) -> str:
    """`<title-slug>-<full-memory-id>.md`. The full id (not a truncated
    prefix) makes a filename collision require the actual primary key to
    collide -- impossible by construction, since ids are unique."""
    title = (mem.get("title") or "").strip() or title_from_body(mem["body"])
    return f"{slugify(title or mem['id'])}-{mem['id']}.md"


def _canonical_frontmatter(mem: dict, also_filed_under: list[str]) -> dict:
    """Every field `_import_canonical` reads back, and nothing else.

    `created_at`/`updated_at` were dropped: the importer never reads them (it
    lets the store stamp fresh ones), so they round-tripped nothing while
    costing ~26 tokens of every agent read of the tree. `importance` is
    rounded for the same reason — 17 significant digits is noise on a [0,1]
    score. `tier` stays: it is not re-imported either, but it is the one
    remaining field a human scanning a git diff of the tree actually reads.
    """
    fm = {
        "id": mem["id"], "kind": mem["kind"], "tier": mem["tier"],
        "title": mem.get("title"), "scope": mem["scope"],
        "tags": mem.get("tags") or [],
        "importance": round(float(mem["importance"]), 3),
        "veracity": mem["veracity"], "status": mem["status"],
        "source_trace_id": mem.get("source_trace_id"),
    }
    if also_filed_under:
        fm["also_filed_under"] = sorted(also_filed_under)
    return fm


def _canonical_node_id(linked: list[str], topics: dict) -> Optional[str]:
    """The deterministic canonical pick: the lexicographically-smallest
    linked node id that still exists in the current topic graph. A single
    since-deleted node only knocks itself off the candidate list -- it does
    NOT discard the memory's other still-valid links, so this only falls
    back to `_unfiled` (returns None) when NONE of `linked` exists in the
    graph anymore."""
    for candidate in sorted(linked):
        if candidate in topics:
            return candidate
    return None


def _export_one(base: Path, graph: dict, mem: dict,
                linked: list[str]) -> tuple[bool, int, set[Path]]:
    """Write one memory's canonical file (+ stubs at its other linked
    nodes), or file it under `_unfiled/` when it has no (still-valid)
    linked node. Every eligible memory gets exactly one canonical file
    either way, so the caller always counts one 'canonical' file; this
    returns `(is_unfiled, stub_count, written_paths)` -- `written_paths` is
    every file this call wrote, so the caller can prune anything the
    memory used to own that isn't among them anymore."""
    topics = graph.get("topics") or {}
    filename = _memory_filename(mem)
    canonical_id = _canonical_node_id(linked, topics)
    if canonical_id is None:
        path = base / UNFILED_DIR / filename
        _write_memory_file(path, _canonical_frontmatter(mem, []),
                           body=mem["body"])
        return True, 0, {path}
    also = [nid for nid in linked if nid != canonical_id and nid in topics]
    canon_rel = Path(*node_path(graph, canonical_id), filename)
    canon_path = base / canon_rel
    _write_memory_file(canon_path, _canonical_frontmatter(mem, also),
                       body=mem["body"])
    written = {canon_path}
    for nid in also:
        stub_fm = {"id": mem["id"], "canonical": canon_rel.as_posix()}
        stub_path = base.joinpath(*node_path(graph, nid), filename)
        _write_memory_file(stub_path, stub_fm)
        written.add(stub_path)
    return False, len(also), written


def _existing_tree_files(base: Path) -> dict[str, set[Path]]:
    """`{memory_id: {existing file paths that claim to belong to it}}`,
    built by parsing every `*.md` file under `base` for its `id:`
    frontmatter. Used to find files a *previous* export run wrote that this
    run needs to prune (moved link, retired/deleted memory). Files that
    don't parse as memory-tree files at all (missing/malformed frontmatter)
    are left out -- defensive: this map only feeds deletions, so anything
    not positively attributable to a memory id must never be touched."""
    existing: dict[str, set[Path]] = {}
    if not base.is_dir():
        return existing
    for path in base.rglob("*.md"):
        frontmatter, _ = _parse_memory_file(path)
        mid = frontmatter.get("id")
        if not mid:
            continue
        existing.setdefault(mid, set()).add(path)
    return existing


def _prune_ineligible(existing: dict[str, set[Path]],
                      all_eligible_ids: set[str]) -> None:
    """Remove every previously-exported file belonging to a memory id that
    is genuinely gone from the WHOLE store (retired, hard-deleted, kind
    changed to digest, or became a test row) -- the export tree's contract
    is that it mirrors current DB state, so a stale id's old files are the
    same class of bug as a moved link.

    `all_eligible_ids` must be scope-unaware (built from an unfiltered
    `list_memories` call): a memory id merely excluded from THIS run's
    scope filter -- still active, just under some other scope -- is not in
    the caller's per-run `rows`/eligible set either, but it must NOT be
    pruned here. Passing a scope-filtered set instead would delete every
    other scope's already-exported files on each scoped export call."""
    for mid, paths in existing.items():
        if mid in all_eligible_ids:
            continue
        for path in paths:
            path.unlink(missing_ok=True)


def _eligible_rows(store, scope: Optional[str]) -> list[dict]:
    """Active, non-test, non-digest memories, optionally scope-filtered."""
    return [m for m in store.list_memories(status="active", scope=scope,
                                           include_tests=False, limit=100_000)
            if m["kind"] != "digest"]


def _all_eligible_ids(store, scope: Optional[str], rows: list[dict]) -> set[str]:
    """Scope-UNAWARE "still active somewhere" ids, for `_prune_ineligible`.
    No extra query needed when this run is already unfiltered -- `rows`
    already IS that set."""
    if scope is None:
        return {mem["id"] for mem in rows}
    return {mem["id"] for mem in _eligible_rows(store, scope=None)}


#: How many distinct memory ids an existing tree may hold before a
#: zero-row export is treated as a wrong-DB accident rather than a real
#: emptying. Small enough that a hand-made or single-lesson tree still
#: prunes normally; large enough that a real corpus is never wiped.
_WIPE_GUARD_MIN_IDS = 10


def export_memory_tree(repo_path: str, *, out_dir: Optional[str] = None,
                       scope: Optional[str] = None) -> dict:
    """Write every eligible active memory into a git-shareable markdown
    tree mirrored onto the authoritative topic graph. Returns
    `{"canonical": N, "stub": N, "unfiled": N}`."""
    from lib.memory import get_store
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.meta_roots import merge_meta_roots

    base = Path(out_dir) if out_dir else Path(repo_path) / DEFAULT_TREE_DIR
    graph = merge_meta_roots(load_authoritative_graph(str(repo_path)))
    store = get_store()
    rows = _eligible_rows(store, scope)

    existing = _existing_tree_files(base)
    if not rows and len(existing) > _WIPE_GUARD_MIN_IDS:
        # A store that reads as empty while the tree holds a whole corpus is
        # not a real transition — retiring or deleting every memory at once
        # is not something any code path does. It means this process is
        # looking at an uninitialised or wrong memory DB, and pruning would
        # silently delete the entire tree. That is how this repo's tree
        # reached 0 files while `index-root` still reported 500+ memories,
        # which dead-ends the `goal-verified-treenav` walk and then walls
        # its step-2 recall gate, with no hint as to why.
        log.error("memory_tree_export_refused_empty_store",
                  existing_ids=len(existing), scope=scope or "*",
                  base=str(base))
        return {"canonical": 0, "stub": 0, "unfiled": 0, "refused": 1}
    _prune_ineligible(existing, _all_eligible_ids(store, scope, rows))

    counts = {"canonical": 0, "stub": 0, "unfiled": 0}
    for mem in rows:
        linked = sorted(store.authoritative_topics_of(mem["id"]))
        is_unfiled, stub_count, written = _export_one(base, graph, mem, linked)
        counts["canonical"] += 1
        counts["stub"] += stub_count
        if is_unfiled:
            counts["unfiled"] += 1
        for stale in existing.get(mem["id"], set()) - written:
            stale.unlink(missing_ok=True)
    log.write("memory_tree_exported", **counts)
    return counts


#: Written beside the tree so a re-export can tell "nothing changed" from
#: "never exported" without walking 200 files.
STAMP_FILE = ".stamp"


def store_signature(scope: Optional[str] = None) -> str:
    """Fingerprint of everything an export's output depends on.

    Covers the memory rows *and* the authoritative-topic links, because a
    memory's file PATH comes from its links, not its body: `link_authoritative_
    topic` writes only the link table and never bumps `Memory.updated_at`. A
    memories-only signature therefore misses the most common transition of all
    — a lesson captured unclassified (exported to `_unfiled/`) and filed under
    a topic moments later — leaving it stranded in `_unfiled/` forever.

    Scope is part of the fingerprint so a scoped export is never skipped on the
    strength of a full export's stamp.

    Aggregates only (no row loading), so the on-write guard stays two queries
    rather than the export's full `list_memories` + file walk.
    """
    from sqlalchemy import func
    from sqlmodel import select

    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import Memory, MemoryAuthoritativeTopic

    mem_stmt = (select(func.count(Memory.id), func.max(Memory.updated_at))
                .where(Memory.status == "active")
                .where(Memory.kind != "digest")
                .where(Memory.is_test == False))  # noqa: E712 — SQL, not Python
    if scope:
        mem_stmt = mem_stmt.where(Memory.scope == scope)
    link_stmt = select(func.count(MemoryAuthoritativeTopic.memory_id),
                       func.max(MemoryAuthoritativeTopic.added_at))
    with MemorySessionLocal() as session:
        count, newest = session.exec(mem_stmt).one()
        links, linked_at = session.exec(link_stmt).one()
    return (f"{scope or '*'}:{count or 0}:{newest or ''}"
            f":{links or 0}:{linked_at or ''}")


def export_tree_if_stale(repo_path: str, *, out_dir: Optional[str] = None,
                         scope: Optional[str] = None) -> Optional[dict]:
    """Export only when the store has moved since the last export.

    The tree is the agent's navigation surface, so a lesson written mid-session
    must reach it before the next prompt — but every memory write calling a
    full 200-file export would be wasteful. Returns the export counts when it
    ran, or None when the stamp already matched.

    Call this after any write that changes what the tree should contain: the
    `send_to_user(lesson)` capture path, `reflect()`, and the CLI/UI curation
    verbs. It is cheap to over-call (two aggregates when nothing moved) and
    silently wrong to under-call — an agent following `memory-tree-nav` treats
    the tree as current.

    Failures are swallowed: the callers' real job is storing the memory. A
    stale tree degrades recall; a raised exception here would lose the write.
    """
    base = Path(out_dir) if out_dir else Path(repo_path) / DEFAULT_TREE_DIR
    stamp_path = base / STAMP_FILE
    try:
        signature = store_signature(scope)
        if stamp_path.is_file() and stamp_path.read_text().strip() == signature:
            return None
        counts = export_memory_tree(repo_path, out_dir=out_dir, scope=scope)
        if counts.get("refused"):
            # Don't stamp a refusal, or the guard's own verdict becomes the
            # cached answer and the tree never recovers once a healthy
            # process comes along.
            return counts
        _atomic_write(stamp_path, signature)
        return counts
    except Exception:
        log.error("memory_tree_autoexport_failed", exc_info=True)
        return None


def _is_stub(frontmatter: dict) -> bool:
    return "canonical" in frontmatter


def _import_canonical(store, path: Path, frontmatter: dict, body: str,
                      links: set[tuple[str, str]]) -> str:
    """Upsert the canonical row and link it to its own directory node plus
    every `also_filed_under` node. Every (memory_id, node_id) pair actually
    linked -- here or by the later stub pass -- is added to the shared
    `links` set, so the caller can report a de-duplicated link count
    instead of a raw call count."""
    mem = MemoryInput(
        body=body, kind=frontmatter.get("kind", "lesson"),
        title=frontmatter.get("title"), scope=frontmatter.get("scope", "global"),
        tags=frontmatter.get("tags") or [],
        importance=float(frontmatter.get("importance", 0.5)),
        veracity=frontmatter.get("veracity", "unknown"),
        status=frontmatter.get("status", "active"),
        source_trace_id=frontmatter.get("source_trace_id"))
    mid = frontmatter["id"]
    store.import_memory(mid, mem)
    node_dir = path.parent.name
    if node_dir != UNFILED_DIR:
        store.link_authoritative_topic(mid, node_dir)
        links.add((mid, node_dir))
    for other in frontmatter.get("also_filed_under") or []:
        store.link_authoritative_topic(mid, other)
        links.add((mid, other))
    return node_dir


def import_memory_tree(repo_path: str, *, in_dir: Optional[str] = None) -> dict:
    """Load a git-shared markdown memory tree back into the local store.
    Idempotent: canonical files upsert by id, links are re-linked rather
    than duplicated. Returns `{"imported": N, "linked": N,
    "skipped_unfiled": N}`."""
    from lib.memory import get_store

    base = Path(in_dir) if in_dir else Path(repo_path) / DEFAULT_TREE_DIR
    if not base.is_dir():
        return {"imported": 0, "linked": 0, "skipped_unfiled": 0}

    store = get_store()
    stubs: list[Path] = []
    imported = 0
    skipped_unfiled = 0
    links: set[tuple[str, str]] = set()
    for path in sorted(base.rglob("*.md")):
        frontmatter, body = _parse_memory_file(path)
        if _is_stub(frontmatter):
            stubs.append(path)
            continue
        node_dir = _import_canonical(store, path, frontmatter, body, links)
        imported += 1
        if node_dir == UNFILED_DIR:
            skipped_unfiled += 1

    for path in stubs:
        frontmatter, _ = _parse_memory_file(path)
        node_dir = path.parent.name
        if node_dir == UNFILED_DIR:
            continue
        store.link_authoritative_topic(frontmatter["id"], node_dir)
        links.add((frontmatter["id"], node_dir))

    linked = len(links)
    log.write("memory_tree_imported", imported=imported, linked=linked,
              skipped_unfiled=skipped_unfiled)
    return {"imported": imported, "linked": linked,
            "skipped_unfiled": skipped_unfiled}


__all__ = [
    "DEFAULT_TREE_DIR", "UNFILED_DIR",
    "slugify", "node_path",
    "export_memory_tree", "import_memory_tree",
]
