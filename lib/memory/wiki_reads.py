"""Derive the ``'read'`` wiki-recall signal from the session trace.

A topic counts as *read* by a session when that session did either of two
things, unioned per distinct session:

* opened the wiki file directly — a ``tool.Read`` span whose ``file_path`` is
  ``.regin/topics/wiki/<id>.md``; or
* consulted the topic through navigation — a genuine (``reinforce`` not False)
  ``index_fetch`` on the topic. This is the primary consultation path: the agent
  walks the taxonomy and pulls a leaf without ever literally ``Read``-ing the
  ``.md``, so a read signal keyed on ``tool.Read`` alone stays starved (only the
  weaker ``exposure`` counter moves, and ranking ignores it).

Recompute, not increment: the count is derived fresh from the append-only span
log each run, so the sync is idempotent (see ``store.replace_wiki_read_counts``).
"""

from __future__ import annotations

import json

_WIKI_SEG = "/.regin/topics/wiki/"
_INDEX_FETCH_SPAN = "tool.mcp__memory__index_fetch"


def _topic_id_from_path(file_path: str) -> str | None:
    """`.../.regin/topics/wiki/<id>.md` -> `<id>`, else None. Rejects nested
    paths and the generated `index.md`, which is not a per-topic wiki."""
    idx = file_path.rfind(_WIKI_SEG)
    if idx < 0:
        return None
    rest = file_path[idx + len(_WIKI_SEG):]
    if "/" in rest or not rest.endswith(".md"):
        return None
    topic_id = rest[:-len(".md")]
    return topic_id if topic_id and topic_id != "index" else None


def _wiki_topic_from_attributes(attributes: str) -> str | None:
    """The wiki topic id a `tool.Read` span's `file_path` points at, or None
    when the span isn't a per-topic wiki read (parses the JSON blob defensively;
    the LIKE prefilter also matches spans that merely quote a wiki path in their
    read *content*, which key on a non-wiki file_path and drop out here)."""
    try:
        file_path = (json.loads(attributes or "{}") or {}).get("file_path")
    except (ValueError, TypeError):
        return None
    return _topic_id_from_path(file_path) if file_path else None


def _index_fetch_pull(attributes: str) -> tuple[str | None, bool]:
    """`(node_id, reinforce)` for an `index_fetch` span. `node_id` is None when
    the span carries no resolvable topic. `reinforce` defaults True and is False
    only when the caller passed `reinforce=False` — an AUDIT / eval sweep that
    must not count as consultation (mirrors `recall`'s reinforce gate). The two
    spans a single fetch emits (a `tool_input` dict and an `mcp_input` JSON
    string) both resolve here; distinct-session dedup collapses them."""
    try:
        blob = json.loads(attributes or "{}") or {}
    except (ValueError, TypeError):
        return None, False
    inp = blob.get("tool_input")
    if not isinstance(inp, dict):
        raw = blob.get("mcp_input")
        try:
            inp = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            inp = {}
    node_id = (inp or {}).get("node_id")
    if not node_id:
        return None, False
    return node_id, bool((inp or {}).get("reinforce", True))


def _existing_wiki_topics() -> set[str]:
    """Topic ids with a curated wiki file on disk. The index_fetch read path
    credits a fetch only when it surfaced a real wiki — mirroring index_fetch's
    own `if wiki_exists` exposure guard — so a bucket or un-accepted topic fetch
    (which has no `.md`) creates no read row."""
    from lib.settings import settings
    from lib.topics.wiki import wiki_dir

    wdir = wiki_dir(settings.project_root)
    if not wdir.exists():
        return set()
    return {p.stem for p in wdir.glob("*.md") if p.stem != "index"}


def _bump(sessions_by_topic: dict[str, set], last_read: dict[str, str],
          topic_id: str, trace_id: str, start_time: str | None) -> None:
    sessions_by_topic.setdefault(topic_id, set()).add(trace_id)
    if start_time and (topic_id not in last_read
                       or start_time > last_read[topic_id]):
        last_read[topic_id] = start_time


def compute_wiki_reads() -> dict[str, dict]:
    """Aggregate wiki consultation into
    ``{topic_id: {'count': int, 'last_read': str|None}}``.

    `count` is the number of **distinct sessions** that consulted the wiki —
    either by `tool.Read`-ing the file or by a reinforcing `index_fetch` on the
    topic — not raw spans: a session that opens one wiki several times (paginated
    line ranges, a re-read, or a fetch-then-Read) is one consultation, not many.
    Counting spans would inflate a long wiki (read in chunks) over a short one.
    `last_read` is the most recent consultation across all sessions. The
    `attributes` LIKE prefilter narrows the (large) Read-span set to the handful
    touching the wiki dir before any JSON is parsed."""
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models.trace import SessionSpan

    read_stmt = (select(SessionSpan.trace_id, SessionSpan.attributes,
                        SessionSpan.start_time)
                 .where(SessionSpan.name == "tool.Read")
                 .where(SessionSpan.attributes.like(f"%{_WIKI_SEG}%")))
    fetch_stmt = (select(SessionSpan.trace_id, SessionSpan.attributes,
                         SessionSpan.start_time)
                  .where(SessionSpan.name == _INDEX_FETCH_SPAN))
    existing = _existing_wiki_topics()
    sessions_by_topic: dict[str, set] = {}
    last_read: dict[str, str] = {}
    with SessionLocal() as session:
        for trace_id, attributes, start_time in session.exec(read_stmt).all():
            topic_id = _wiki_topic_from_attributes(attributes)
            if topic_id is not None:
                _bump(sessions_by_topic, last_read, topic_id,
                      trace_id, start_time)
        for trace_id, attributes, start_time in session.exec(fetch_stmt).all():
            node_id, reinforce = _index_fetch_pull(attributes)
            if node_id and reinforce and node_id in existing:
                _bump(sessions_by_topic, last_read, node_id,
                      trace_id, start_time)
    return {topic_id: {"count": len(traces),
                       "last_read": last_read.get(topic_id)}
            for topic_id, traces in sessions_by_topic.items()}


def wiki_recall_rows(repo_path) -> list[dict]:
    """Per-topic wiki recall rows for the UI panel: `exposure` + `read` counts,
    `last_read`, the topic `label`, and whether the wiki file still exists.
    Sorted read-desc then exposure-desc (most-consulted first). Empty when
    memory is off. A row whose `wiki_present` is False is a counter that
    outlived its file — a prune/refresh signal."""
    import lib.memory as memory
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.wiki import wiki_dir

    if not memory.enabled():
        return []
    topics = load_authoritative_graph(str(repo_path)).get("topics") or {}
    wdir = wiki_dir(repo_path)
    by_topic: dict[str, dict] = {}
    for stat in memory.get_store().wiki_recall_stats():
        agg = by_topic.setdefault(stat.topic_id,
                                  {"exposure": 0, "read": 0, "last_read": None})
        if stat.signal == "read":
            agg["read"] = stat.recall_count
            agg["last_read"] = stat.last_recalled
        elif stat.signal == "exposure":
            agg["exposure"] = stat.recall_count
    rows = [{"topic_id": topic_id,
             "label": (topics.get(topic_id) or {}).get("label") or topic_id,
             "wiki_present": (wdir / f"{topic_id}.md").exists(), **agg}
            for topic_id, agg in by_topic.items()]
    rows.sort(key=lambda r: (r["read"], r["exposure"]), reverse=True)
    return rows


def sync_wiki_reads() -> list[dict]:
    """Recompute all ``signal='read'`` wiki counters from the trace and persist
    them. Returns the derived rows, most-read first. Idempotent."""
    import lib.memory as memory

    agg = compute_wiki_reads()
    memory.get_store().replace_wiki_read_counts(agg)
    return [{"topic_id": topic_id, **data} for topic_id, data
            in sorted(agg.items(), key=lambda kv: kv[1]["count"], reverse=True)]
