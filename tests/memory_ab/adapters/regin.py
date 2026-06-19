"""regin-native adapter for the memory A/B harness.

Ingests the shared corpus into a *throwaway isolated* memory DB (so only the
corpus exists — a true parallel to Hindsight's fresh bank), runs the probe
queries through the real `lib.memory.recall` path, exercises the supersede
lifecycle, and writes the common result dump to results/regin.json.

Run from the repo root with the project interpreter:

    .venv/bin/python -m tests.memory_ab.adapters.regin --mode auto

`--mode fts` skips the model load (lexical-only); `--keep` leaves the temp DB
on disk for inspection instead of deleting it.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import lib.memory as memory
from lib.memory.engine import dispose_memory_engine
from lib.memory.models import MemoryInput
from lib.settings import settings
from tests.memory_ab import spec

# Lifecycle query: after the supersede, recall this and check the new body
# (15 minutes) surfaces and the old body (5 minutes) does not.
_LIFECYCLE_QUERY = "what is the auth token cache TTL right now?"


def _isolate_store(db_path: Path) -> None:
    """Point the memory engine at a pristine temp DB and drop all caches."""
    settings.agent_memory.db_path = str(db_path)
    memory.reset_store()
    dispose_memory_engine()


def _ingest(corpus: list[dict]) -> tuple[dict[str, str], list[dict]]:
    """Write every corpus entry as a test memory. Returns (corpus_id ->
    system memory id) and the per-entry capture record."""
    id_map: dict[str, str] = {}
    records: list[dict] = []
    for entry in corpus:
        t0 = time.perf_counter()
        mid = memory.remember(
            spec.body_with_ref(entry),
            kind=entry["kind"], title=entry.get("title"),
            scope=entry.get("scope", "global"), tags=entry.get("tags", []),
            importance=entry.get("importance", 0.5),
            veracity=entry.get("veracity", "unknown"), is_test=True)
        dt = (time.perf_counter() - t0) * 1000.0
        id_map[entry["id"]] = mid
        records.append({"corpus_id": entry["id"], "ok": bool(mid),
                        "latency_ms": round(dt, 2), "system_id": mid})
    return id_map, records


def _hits_for(query: str, *, top_k: int, mode: str,
              intent: str | None) -> list[dict]:
    hits = memory.recall(query, top_k=top_k, mode=mode, include_tests=True,
                          reinforce=False, intent=intent)
    out: list[dict] = []
    for rank, hit in enumerate(hits, start=1):
        out.append({
            "corpus_id": spec.resolve_corpus_id(hit.memory.get("body")),
            "rank": rank, "score": round(float(hit.score), 4),
            "score_kind": hit.score_kind,
        })
    return out


def _query(probes: list[dict], *, top_k: int, mode: str) -> list[dict]:
    results: list[dict] = []
    for probe in probes:
        results.append({
            "probe_id": probe["id"], "query": probe["query"],
            "expect_ids": probe.get("expect_ids", []),
            "must_not_ids": probe.get("must_not_ids", []),
            "hits": _hits_for(probe["query"], top_k=top_k, mode=mode,
                              intent=probe.get("intent")),
        })
    return results


def _lifecycle(corpus: list[dict], id_map: dict[str, str], *,
               top_k: int, mode: str) -> dict | None:
    """Supersede the flagged entry, then confirm the old memory is retired
    and the replacement body surfaces in its place."""
    entry = next((e for e in corpus if e.get("lifecycle") == "supersede"), None)
    if entry is None:
        return None
    old_id = id_map[entry["id"]]
    memory.supersede(old_id, MemoryInput(
        body=spec.body_with_ref(entry, body=entry["supersede_body"]),
        kind=entry["kind"], title=entry.get("title"),
        scope=entry.get("scope", "global"), is_test=True))
    old = memory.get(old_id)
    old_retired = old is None or getattr(old, "status", None) == "retired"
    hits = memory.recall(_LIFECYCLE_QUERY, top_k=top_k, mode=mode,
                         include_tests=True, reinforce=False)
    flags = [spec.lifecycle_classify(h.memory.get("body") or "")
             for h in hits if spec.resolve_corpus_id(h.memory.get("body"))
             == entry["id"]]
    return {"old_corpus_id": entry["id"], "old_retired": old_retired,
            "new_surfaced": any(f[0] for f in flags),
            "old_surfaced": any(f[1] for f in flags)}


def run(*, mode: str, top_k: int, keep: bool) -> Path:
    corpus = spec.load_corpus()
    probes = spec.load_probes()
    db_path = spec.RESULTS_DIR / ".tmp" / "regin_ab.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_db(db_path)
    _isolate_store(db_path)
    try:
        id_map, ingest = _ingest(corpus)
        queries = _query(probes, top_k=top_k, mode=mode)
        lifecycle = _lifecycle(corpus, id_map, top_k=top_k, mode=mode)
    finally:
        dispose_memory_engine()
        if not keep:
            _cleanup_db(db_path)
    payload = {
        "system": "regin", "run_id": time.strftime("%Y%m%dT%H%M%S"),
        "mode": mode, "top_k": top_k,
        "corpus_ids": [e["id"] for e in corpus],
        "ingest": ingest, "queries": queries, "lifecycle": lifecycle,
    }
    return spec.write_dump(payload)


def _cleanup_db(db_path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description="regin memory A/B adapter")
    ap.add_argument("--mode", default="auto", choices=["auto", "hybrid", "fts"])
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--keep", action="store_true",
                    help="keep the temp DB for inspection")
    args = ap.parse_args()
    path = run(mode=args.mode, top_k=args.top_k, keep=args.keep)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
