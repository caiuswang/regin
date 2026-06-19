"""Shared spec for the memory A/B harness: corpus/probe loaders, the
per-fact ref sentinel that makes scoring system-agnostic, and the common
result-dump shape both adapters emit and the scorer reads.

Fairness rule enforced here: every system ingests `body_with_ref(entry)` —
the identical body text plus a `[ref:AB-<id>]` marker. Queries never contain
the marker, so it is inert for retrieval but lets any recalled hit be mapped
back to its corpus id regardless of which system's ids came back.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS_PATH = HERE / "corpus.jsonl"
PROBES_PATH = HERE / "probes.jsonl"
RESULTS_DIR = HERE / "results"

_REF_RE = re.compile(r"\[ref:AB-([a-z0-9]+)\]")


def load_jsonl(path: Path) -> list[dict]:
    """Parse a JSONL file; blank lines skipped."""
    out: list[dict] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_corpus() -> list[dict]:
    return load_jsonl(CORPUS_PATH)


def load_probes() -> list[dict]:
    return load_jsonl(PROBES_PATH)


def body_with_ref(entry: dict, *, body: str | None = None) -> str:
    """The exact text a system must ingest for `entry`: its body plus the
    ref sentinel. Pass `body` to stamp the same ref onto a replacement body
    (used by the supersede lifecycle test)."""
    text = entry["body"] if body is None else body
    return f"{text} [ref:AB-{entry['id']}]"


def resolve_corpus_id(text: str | None) -> str | None:
    """Map a recalled hit's text back to a corpus id via its ref sentinel."""
    if not text:
        return None
    m = _REF_RE.search(text)
    return m.group(1) if m else None


def lifecycle_classify(text: str) -> tuple[bool, bool]:
    """Classify one recalled body for the auth-TTL supersede test as
    (is_new, is_old). The replacement says "15 minutes", the original
    "5 minutes". "5 minute" is a substring of "15 minute", so old is only
    asserted when the new value is absent — evaluated per-hit so a lingering
    old memory is caught even when a new one also surfaces."""
    low = (text or "").lower()
    is_new = "15 minute" in low
    is_old = ("5 minute" in low) and not is_new
    return is_new, is_old


def dump_path(system: str) -> Path:
    return RESULTS_DIR / f"{system}.json"


def write_dump(payload: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = dump_path(payload["system"])
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_dump(system: str) -> dict | None:
    path = dump_path(system)
    if not path.exists():
        return None
    return json.loads(path.read_text())


# --- common result-dump shape -------------------------------------------
# A dump is a dict:
#   {
#     "system": "regin" | "hindsight",
#     "run_id": str,
#     "mode": str,                 # recall mode/notes (free-form per system)
#     "top_k": int,
#     "corpus_ids": [str],         # what was ingested
#     "ingest": [                  # capture round-trip evidence
#       {"corpus_id", "ok": bool, "latency_ms": float, "system_id": str|None}
#     ],
#     "queries": [
#       {"probe_id", "query", "expect_ids": [str], "must_not_ids": [str],
#        "hits": [{"corpus_id": str|None, "rank": int, "score": float}]}
#     ],
#     "lifecycle": {               # supersede correctness
#       "old_corpus_id": str, "old_retired": bool,
#       "new_surfaced": bool, "old_surfaced": bool
#     } | None,
#   }
# `corpus_id` on a hit is None when the hit could not be mapped (a foreign
# memory the system surfaced) — the scorer treats those as noise.
