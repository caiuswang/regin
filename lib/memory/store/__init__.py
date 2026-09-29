"""SQLite implementation of the `MemoryStore` port.

Single source of truth for reading and writing the memory DB. Unlike the
append-only `session_spans` store, memory is *mutable by design*: rows are
updated, superseded, retired, and deleted in place — that lifecycle is the
whole point of curation.

Recall mirrors `pattern_router`'s hybrid shape: an FTS5/BM25 lexical leg,
an optional brute-force-cosine dense leg over `memory_embeddings`, RRF
fusion, and an optional cross-encoder rerank — each outer stage degrading
gracefully when the injected `EmbeddingProvider` is absent or its
dependencies are missing. Embeddings are written by `reflect()` (working-
tier rows are raw on purpose), so a store that has never reflected simply
recalls FTS-only.
"""

from lib.memory.engine import MemorySessionLocal
from lib.memory.store.helpers import (
    ORPHAN_BLURB,
    ORPHAN_LABEL,
    ORPHAN_NODE_ID,
    _now,
    title_from_body,
)
from lib.memory.store.sqlite import SqliteMemoryStore

__all__ = ["SqliteMemoryStore",
           "ORPHAN_NODE_ID", "ORPHAN_LABEL", "ORPHAN_BLURB"]
