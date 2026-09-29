"""`SqliteMemoryStore`, composed from the per-responsibility mixins."""

from __future__ import annotations

from lib.memory.store.exemplars import ExemplarsMixin
from lib.memory.store.injections import InjectionsMixin
from lib.memory.store.reads import ReadsMixin
from lib.memory.store.recall import RecallMixin
from lib.memory.store.stats import StatsMixin
from lib.memory.store.topics import TopicsMixin
from lib.memory.store.writes import WritesMixin


class SqliteMemoryStore(WritesMixin, ReadsMixin, TopicsMixin, RecallMixin,
                        ExemplarsMixin, InjectionsMixin, StatsMixin):
    """The default `MemoryStore`. An `EmbeddingProvider` may be injected
    for the dense leg + rerank; None means lexical-only recall."""

    def __init__(self, embedder=None):
        self._embedder = embedder
