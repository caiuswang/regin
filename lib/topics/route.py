"""Topic lookup, routing, and rendering used by CLI/web/agent consumers.

Queries the approved graph by alias or keyword, then returns ordered
refs + wiki content ready for an agent to consume.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.topics.core import (
    ROLE_ORDER,
    TopicGraphError,
    normalize,
    slugify,
    topic_dir,
)
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.scan import validate


def topic_summary(repo_path: str | Path) -> dict[str, Any]:
    graph = load_authoritative_graph(repo_path)
    result = validate(repo_path)
    topics = []
    for topic_id, topic in sorted(graph.get("topics", {}).items()):
        refs = topic.get("refs", [])
        broken = [
            ref.get("path") for ref in refs
            if isinstance(ref, dict) and ref.get("path") and not (Path(repo_path) / ref["path"]).exists()
        ]
        topics.append({
            "id": topic_id,
            "label": topic.get("label", topic_id),
            "status": topic.get("status", "active"),
            "intent": topic.get("intent", ""),
            "aliases": topic.get("aliases", []),
            "ref_count": len(refs),
            "edge_count": len(topic.get("edges", [])),
            "broken_refs": broken,
        })
    return {
        "graph": graph,
        "topics": topics,
        "validation": {"ok": result.ok, "errors": result.errors, "warnings": result.warnings},
    }


def topic_detail(repo_path: str | Path, topic_id: str) -> dict[str, Any]:
    graph = load_authoritative_graph(repo_path)
    topic = graph.get("topics", {}).get(topic_id)
    if not topic:
        raise TopicGraphError(f"topic not found: {topic_id}")
    related = []
    for edge in topic.get("edges", []):
        target = edge.get("target")
        if target in graph.get("topics", {}):
            related.append({"id": target, "type": edge.get("type", "related"), "label": graph["topics"][target].get("label", target)})
    return topic | {"id": topic_id, "related": related, "wiki_content": topic_wiki_content(repo_path, topic_id, topic, graph.get("topics", {}))}


def topic_wiki_content(repo_path: str | Path, topic_id: str, topic: dict[str, Any], topics: dict[str, Any]) -> str:
    repo = Path(repo_path)
    wiki_path = topic_dir(repo) / "wiki" / f"{slugify(topic_id)}.md"
    if wiki_path.exists():
        return wiki_path.read_text()
    from lib.topics.wiki import render_topic_page

    return render_topic_page(topic_id, topic, topics)


def _ref_paths(topic: dict[str, Any]) -> list[str]:
    return [
        ref.get("path", "") for ref in topic.get("refs", [])
        if isinstance(ref, dict)
    ]


def _alias_exact_match(needle: str, topic_id: str, topic: dict[str, Any]) -> bool:
    """Exact match against topic_id, label, or any alias (normalized)."""
    values = [topic_id, topic.get("label", ""), *topic.get("aliases", [])]
    return needle in {normalize(v) for v in values if v}


def _ref_exact_match(needle: str, topic_id: str, topic: dict[str, Any]) -> bool:
    """Exact match against any ref path (normalized)."""
    return needle in {normalize(p) for p in _ref_paths(topic) if p}


def _ref_substring_match(needle: str, topic_id: str, topic: dict[str, Any]) -> bool:
    """Substring match against any ref path (normalized)."""
    return any(needle in normalize(p) for p in _ref_paths(topic) if p)


def _identity_substring_match(needle: str, topic_id: str, topic: dict[str, Any]) -> bool:
    """Substring match against topic_id + label + intent + aliases."""
    haystack = " ".join([
        topic_id, topic.get("label", ""), topic.get("intent", ""),
        *topic.get("aliases", []),
    ])
    return needle in normalize(haystack)


# Ordered list of single-match strategies, each paired with a human label
# for the route explanation. The first topic that any strategy matches wins;
# strategies are applied in priority order, so an alias-exact match on a
# different topic still beats a ref-substring match on this one. Each
# predicate runs only when `needle` is truthy.
_MATCH_STRATEGIES = (
    (_alias_exact_match, "alias / label (exact)"),
    (_ref_exact_match, "ref path (exact)"),
    (_ref_substring_match, "ref path (substring)"),
    (_identity_substring_match, "label / intent (substring)"),
)


# Function words carry no topical signal, yet a prose prompt is full of
# them — left in, the fuzzy fallback "matches" every topic on `we/that/to/…`
# and routes on noise (see `_fuzzy_best`). 2-char tokens are folded in here
# so genuine short keywords (`ui`, `db`, `js`, `go`) still survive the
# `len >= 2` filter.
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do",
    "for", "from", "has", "have", "he", "her", "him", "his", "how", "if",
    "in", "into", "is", "it", "its", "me", "my", "no", "not", "of", "on",
    "or", "our", "out", "she", "so", "some", "still", "than", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "those", "to",
    "up", "us", "use", "via", "was", "we", "were", "what", "when", "which",
    "who", "will", "with", "would", "you", "your",
})

# A fuzzy route needs at least this many distinct meaningful keyword hits.
# One coincidental word (a prompt's "apply" landing on the "apply/stop"
# stage in a topic label) is not evidence of topical intent — below this
# the fallback declines and the prompt routes nowhere.
_FUZZY_MIN_HITS = 2


def _meaningful_keywords(needle: str) -> list[str]:
    """The ≥2-char, non-stopword tokens of `needle`, de-duplicated in order —
    the tokens that actually carry topical signal for the fuzzy fallback."""
    return list(dict.fromkeys(
        w for w in needle.split() if len(w) >= 2 and w not in _STOPWORDS))


def _keyword_hits_for_topic(
    keywords: list[str], topic_id: str, topic: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Return (identity_hits, ref_hits) — the keywords that appear in this
    topic's identity text and in its ref paths, respectively."""
    identity = normalize(" ".join([
        topic_id, topic.get("label", ""), topic.get("intent", ""),
        *topic.get("aliases", []),
    ]))
    refs_text = normalize(" ".join(_ref_paths(topic)))
    id_hits = [kw for kw in keywords if kw in identity]
    ref_hits = [kw for kw in keywords if kw in refs_text]
    return id_hits, ref_hits


def _keyword_score_for_topic(
    keywords: list[str], topic_id: str, topic: dict[str, Any],
) -> tuple[int, int]:
    """Return (identity_hits, refs_hits) counts for one topic against keywords."""
    id_hits, ref_hits = _keyword_hits_for_topic(keywords, topic_id, topic)
    return len(id_hits), len(ref_hits)


def _fuzzy_best(
    needle: str, topics: dict[str, Any],
) -> tuple[str | None, list[str]]:
    """Fallback for queries like "trace skill reads view loading": pick the
    topic with the highest (identity_hits, ref_hits) score over the needle's
    *meaningful* keywords, requiring ≥2 keywords overall and ≥`_FUZZY_MIN_HITS`
    distinct keyword hits on the winner. Returns `(topic_id, matched_keywords)`
    or `(None, [])` — so the caller can explain the route, not just assert it."""
    keywords = _meaningful_keywords(needle)
    if len(keywords) < 2:
        return None, []
    best_id: str | None = None
    best_score = (0, 0)
    best_hits: list[str] = []
    for topic_id, topic in topics.items():
        id_hits, ref_hits = _keyword_hits_for_topic(keywords, topic_id, topic)
        score = (len(id_hits), len(ref_hits))
        if score > best_score:
            best_score, best_id = score, topic_id
            best_hits = list(dict.fromkeys([*id_hits, *ref_hits]))
    if len(best_hits) < _FUZZY_MIN_HITS:
        return None, []
    return best_id, best_hits


def best_topic_for_text(repo_path: str | Path, text: str, *,
                        min_ref_hits: int = 1) -> str | None:
    """High-precision match for *long* text (a memory body, not a query).

    The `match_topic` strategies test ``needle in haystack`` — built for
    short keyword queries, so a full memory body never satisfies them and
    always drops to the fuzzy multi-keyword fallback, which over-links. For
    long text the trustworthy signal is the reverse: does the text *contain*
    a topic's ref path (a specific file path is a strong "this is about that
    topic" marker). Returns the topic id whose ref paths appear most often
    in `text`, requiring at least `min_ref_hits`, else None."""
    graph = load_authoritative_graph(repo_path)
    needle = normalize(text)
    if not needle:
        return None
    best_id: str | None = None
    best_hits = 0
    for topic_id, topic in graph.get("topics", {}).items():
        hits = sum(1 for p in _ref_paths(topic)
                   if p and normalize(p) in needle)
        if hits > best_hits:
            best_hits, best_id = hits, topic_id
    return best_id if best_hits >= min_ref_hits else None


def _route_match(
    repo_path: str | Path, query: str,
) -> tuple[str | None, dict[str, Any]]:
    """The keyword route plus *why* it fired: `(topic_id, explanation)` where
    explanation is `{strategy, keywords}`. A precise strategy matched the whole
    query phrase, so it carries no per-keyword breakdown (`keywords: []`); the
    fuzzy fallback lists the meaningful keywords that drove it. `(None, ...)`
    when nothing matched — the honest answer for prose with no topical word."""
    none = {"strategy": None, "keywords": []}
    graph = load_authoritative_graph(repo_path)
    needle = normalize(query)
    if not needle:
        return None, none
    topics = graph.get("topics", {})

    for strategy, label in _MATCH_STRATEGIES:
        for topic_id, topic in topics.items():
            if strategy(needle, topic_id, topic):
                return topic_id, {"strategy": label, "keywords": []}

    best_id, hits = _fuzzy_best(needle, topics)
    if best_id is not None:
        return best_id, {"strategy": "fuzzy keyword overlap", "keywords": hits}
    return None, none


def match_topic(repo_path: str | Path, query: str) -> dict[str, Any] | None:
    topic_id, _ = _route_match(repo_path, query)
    return topic_detail(repo_path, topic_id) if topic_id is not None else None


def route_explain(repo_path: str | Path, query: str) -> dict[str, Any]:
    """`{id, strategy, keywords}` — the keyword route plus its basis, for the
    topic-route playground. `id` is None when the query routes nowhere."""
    topic_id, why = _route_match(repo_path, query)
    return {"id": topic_id, **why}


def route_topic(
    repo_path: str | Path,
    query: str,
    *,
    max_wiki_chars: int = 12000,
) -> dict[str, Any]:
    """Resolve a user topic query into approved context."""
    match = match_topic(repo_path, query)
    if match:
        graph = load_authoritative_graph(repo_path)
        related = _first_degree_related(repo_path, graph, match)
        wiki_paths = wiki_paths_for_topic(repo_path, match["id"])
        return {
            "status": "approved",
            "query": query,
            "topic": match,
            "refs": ordered_refs(match.get("refs", [])),
            "wiki": wiki_paths,
            "wiki_pages": read_wiki_pages(repo_path, wiki_paths, max_chars=max_wiki_chars),
            "related": related,
            "unapproved": False,
        }

    return {
        "status": "unmatched",
        "query": query,
        "topic": None,
        "refs": [],
        "wiki": [],
        "related": [],
        "unapproved": False,
    }


def ordered_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(ref: dict[str, Any]) -> tuple[int, str]:
        role = ref.get("role", "implementation")
        order = ROLE_ORDER.index(role) if role in ROLE_ORDER else len(ROLE_ORDER)
        return order, ref.get("path", "")

    return sorted(refs, key=key)


def wiki_paths_for_topic(repo_path: str | Path, topic_id: str) -> list[str]:
    repo = Path(repo_path)
    base = topic_dir(repo) / "wiki"
    paths = [base / f"{slugify(topic_id)}.md", base / "index.md"]
    return [path.relative_to(repo).as_posix() for path in paths if path.exists()]


def read_wiki_pages(repo_path: str | Path, paths: list[str], *, max_chars: int = 12000) -> list[dict[str, Any]]:
    """Read bounded wiki markdown for agent-facing topic routing."""
    repo = Path(repo_path)
    pages: list[dict[str, Any]] = []
    remaining = max(0, max_chars)
    for rel_path in paths:
        if remaining <= 0:
            break
        path = (repo / rel_path).resolve()
        try:
            path.relative_to(repo.resolve())
        except ValueError:
            continue
        if not path.is_file():
            continue
        content = path.read_text()
        truncated = len(content) > remaining
        pages.append({
            "path": rel_path,
            "content": content[:remaining],
            "truncated": truncated,
        })
        remaining -= min(len(content), remaining)
    return pages


def _first_degree_related(repo_path: str | Path, graph: dict[str, Any], topic: dict[str, Any]) -> list[dict[str, Any]]:
    related = []
    for edge in topic.get("edges", []):
        target = edge.get("target")
        target_topic = graph.get("topics", {}).get(target)
        if not target_topic:
            continue
        related.append({
            "id": target,
            "type": edge.get("type", "related"),
            "label": target_topic.get("label", target),
            "refs": ordered_refs(target_topic.get("refs", [])),
            "wiki": wiki_paths_for_topic(repo_path, target),
        })
    return related


def generate_topic_router_skill(repo_path: str | Path) -> str:
    return f"""---
name: topic-router
title: "Topic Router"
procedure: topic-router
category: reference
---

# Topic Router

Use the approved topic graph — git-tracked `.regin/topics/topic.json` merged with the
machine-local `.regin/topics/topic.local.json` overlay — as the source of topic context
for this repo. `regin topics route` reads the merged graph for you.

1. Identify 2-6 concise search keywords from the user's request, changed files, repo area, feature name, or workflow name. Prefer stable nouns and domain terms, not the full goal sentence.
2. Route with those keywords via `regin topics route <keywords>` and use the returned `wiki_pages` content first, then `.regin/topics/wiki/<topic>.md`, then refs ordered as overview, architecture, entrypoint, api, schema, implementation, test.
3. If the first keyword route is weak or unmatched, retry once or twice with a narrower keyword set or an adjacent domain term instead of falling back to the whole sentence.
4. Follow only first-degree related-topic edges unless the user asks for broader graph traversal.
5. If no approved topic matches, inspect proposal runs only as unapproved context. Say that clearly before using it.
6. Do not promote proposals into topics without an explicit user action through CLI or WebUI.
"""
