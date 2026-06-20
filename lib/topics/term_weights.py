"""Query-log term weighting for the keyword fuzzy router.

The always-on `wordfreq` prior in `route.py` down-weights words common in
*general English*. This module adds the repo-adaptive second layer: down-weight
words common in *this repo's own past routed prompts*. `memory`/`topics`/
`current` are rare in English (the prior keeps them high) yet ubiquitous here,
so they carry little routing signal for this graph — the prior can't see that;
this can.

The document frequency of each token over the routed prompts
(`topic_injections.query` ∪ recall `injection_events.query`) is cached to
`.regin/topics/query_df.json`, rebuilt on the reflect sweep (and via
`regin topics rebuild-query-df`), and read at route time as a bounded
multiplier on the prior. Self-evolving: as the prompt log grows, the weights
sharpen; no hand-kept list.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.topics.core import normalize, topic_dir

log = get_activity_logger("topics")

_CACHE_FILENAME = "query_df.json"


def _cache_path(repo_path: str | Path) -> Path:
    return topic_dir(Path(repo_path)) / _CACHE_FILENAME


def _tokens(text: str) -> set[str]:
    """Distinct ≥2-char normalized tokens of one query — the build-time
    counterpart of route's keyword extraction, kept dependency-light (no
    wordfreq) so a rebuild stays cheap. Filler tokens are counted but never
    looked up at route time, so the extra keys are harmless."""
    return {w for w in normalize(text).split() if len(w) >= 2}


def _routed_queries() -> list[str]:
    """Every distinct non-empty user prompt the recall hook has routed — both
    topic banners and memory injections fire on the same prompts, so their
    union is the prompt corpus. Read via the ORM (no raw sqlite). Imported
    lazily so the topics package doesn't pull in the memory stack at import."""
    from sqlmodel import select

    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import InjectionEvent, TopicInjection

    seen: set[str] = set()
    with MemorySessionLocal() as session:
        for model in (TopicInjection, InjectionEvent):
            for query in session.exec(select(model.query)).all():
                if query and query.strip():
                    seen.add(query.strip())
    return list(seen)


def rebuild_query_df(repo_path: str | Path) -> int:
    """Recompute per-token document frequency over routed prompts and cache it
    to `.regin/topics/query_df.json`. Returns the prompt count. Cheap and
    idempotent; wired into the reflect sweep and the CLI."""
    queries = _routed_queries()
    df: dict[str, int] = {}
    for query in queries:
        for tok in _tokens(query):
            df[tok] = df.get(tok, 0) + 1
    path = _cache_path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"n": len(queries), "df": df}))
    log.write("query_df_rebuilt", queries=len(queries), tokens=len(df))
    return len(queries)


@lru_cache(maxsize=8)
def _load_cached(path_str: str, _mtime: float) -> tuple[int, dict[str, int]]:
    """Parse the cache file. The `_mtime` arg is the cache key, not read — a
    rebuild changes the file's mtime and so invalidates this entry for free."""
    data = json.loads(Path(path_str).read_text())
    return int(data.get("n", 0)), dict(data.get("df", {}))


def load_query_df(repo_path: str | Path) -> tuple[int, dict[str, int]]:
    """`(n, df)` for this repo, or `(0, {})` when no cache exists yet — in
    which case `repo_factor` is a no-op and routing stays pure wordfreq."""
    path = _cache_path(repo_path)
    if not path.is_file():
        return 0, {}
    return _load_cached(str(path), path.stat().st_mtime)


def repo_factor(word: str, n: int, df: dict[str, int]) -> float:
    """Bounded [`floor`, 1.0] multiplier shrinking words that saturate this
    repo's prompt corpus. `log((n+1)/(d+1)) / log(n+1)` is 1.0 for an unseen
    word and approaches `floor` for one in every prompt; the `log(n+1)`
    denominator self-attenuates at low `n` (mild shrink), so the head bias is
    caught while the sparse tail is barely touched. Returns 1.0 (no effect)
    until the corpus reaches `topic_route_querylog_min_queries`."""
    if n < settings.agent_memory.topic_route_querylog_min_queries:
        return 1.0
    floor = settings.agent_memory.topic_route_querylog_floor
    factor = math.log((n + 1) / (df.get(word, 0) + 1)) / math.log(n + 1)
    return max(floor, min(1.0, factor))


__all__ = ["rebuild_query_df", "load_query_df", "repo_factor"]
