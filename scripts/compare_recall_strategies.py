"""Compare two recall front-ends on real session prompts.

Strategy A (current): recall on the raw prompt — exactly what the
`UserPromptSubmit` hook does today.
Strategy B (new): expand the prompt via an LLM (`lib.memory.expand`), then
recall on the expansion.

Both go through the same warm `regin serve` dense+rerank endpoint the hook
uses, so the only variable is the query text. For each real prompt we
print the expanded query and the two surfaced memory sets, then summarize
how often expansion changed the result and at what rerank score.

Usage:
    .venv/bin/python scripts/compare_recall_strategies.py [N] [--model M]
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import urllib.request

from lib.memory.expand import expand_query
from lib.settings import settings

SERVER = "http://127.0.0.1:8321"
TRACE_DB = "db/regin.db"


class HaikuLLM:
    """A fast LLMProvider for expansion — the realistic production choice
    for a latency-sensitive hook (opus per-prompt would block the prompt
    for tens of seconds)."""

    def __init__(self, model: str):
        self._model = model

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> "str | None":
        try:
            proc = subprocess.run(
                ["claude", "--print", "--model", self._model],
                input=prompt.encode(), capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.decode("utf-8", errors="replace")


def real_prompts(n: int) -> list[str]:
    db = sqlite3.connect(TRACE_DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "select attributes from session_spans where name='prompt' "
        "order by start_time desc limit 1500").fetchall()
    seen: set[str] = set()
    out: list[str] = []
    for r in rows:
        a = json.loads(r["attributes"])
        t = (a.get("text") or "").strip()
        if a.get("slash_command") or t.startswith("/"):
            continue
        if "<task-notification>" in t or "<command-" in t:
            continue
        if not (40 <= len(t) <= 600):
            continue
        key = t[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= n:
            break
    return out


def recall(query: str) -> list[dict]:
    """Hit the same server endpoint the hook uses; return flat hit rows."""
    body = json.dumps({
        "query": query[:2000],
        "top_k": settings.agent_memory.inject_top_k,
        "scope": None,
        "mode": "auto",
        "min_overlap": settings.agent_memory.inject_min_overlap,
    }).encode()
    req = urllib.request.Request(
        SERVER + "/api/memory/recall", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    return data.get("hits", [])


def _gate(hits: list[dict]) -> list[dict]:
    """Apply the same reranked-confidence gate the hook applies."""
    floor = settings.agent_memory.recall_min_score
    kept = []
    for h in hits:
        if (h.get("score_kind") or "rerank") == "rerank":
            if float(h.get("score") or 0) >= floor:
                kept.append(h)
        else:
            kept.append(h)
    return kept


def _ids(hits: list[dict]) -> set[str]:
    return {h["id"][:8] for h in hits}


def _fmt(hits: list[dict]) -> str:
    if not hits:
        return "      (none)"
    return "\n".join(
        f"      [{h['id'][:8]}] {float(h.get('score') or 0):.3f} "
        f"{(h.get('title') or h.get('body') or '')[:70]}" for h in hits)


_CACHE = "/tmp/recall_expansions.json"
_UNGATED = "--ungated" in sys.argv


def _expand_cached(p: str, llm: HaikuLLM) -> str:
    import os
    cache = {}
    if os.path.exists(_CACHE):
        with open(_CACHE) as f:
            cache = json.load(f)
    if p not in cache:
        cache[p] = expand_query(p, llm)
        with open(_CACHE, "w") as f:
            json.dump(cache, f)
    return cache[p]


def _compare_one(i: int, p: str, llm: HaikuLLM) -> dict:
    """Run both strategies on one prompt, print the side-by-side, return
    the per-prompt stats the summary aggregates."""
    exp = _expand_cached(p, llm)
    gate = (lambda h: h) if _UNGATED else _gate
    a, b = gate(recall(p)), gate(recall(exp))
    ids_a, ids_b = _ids(a), _ids(b)
    print(f"\n=== [{i}] {'SAME' if ids_a == ids_b else 'CHANGED'} ===")
    print(f"  RAW    : {p[:120]}")
    print(f"  EXPAND : {exp[:200]}")
    print(f"  A (raw recall):\n{_fmt(a)}")
    print(f"  B (expanded recall):\n{_fmt(b)}")
    if ids_a != ids_b:
        print(f"  B added: {sorted(ids_b - ids_a)}  "
              f"B dropped: {sorted(ids_a - ids_b)}")
    return {"same": ids_a == ids_b, "a_empty": not ids_a,
            "rescued": (not ids_a) and bool(ids_b),
            "added": len(ids_b - ids_a)}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(args[0]) if args else 12
    model = ("claude-haiku-4-5-20251001" if "--model" not in sys.argv
             else sys.argv[sys.argv.index("--model") + 1])
    llm = HaikuLLM(model)

    prompts = real_prompts(n)
    stats = [_compare_one(i, p, llm) for i, p in enumerate(prompts)]
    _summary(stats)


def _summary(stats: list[dict]) -> None:
    def total(key: str) -> int:
        return sum(s[key] for s in stats)
    print("\n" + "=" * 60)
    print(f"prompts             : {len(stats)}")
    print(f"same result         : {total('same')}")
    print(f"changed result      : {len(stats) - total('same')}")
    print(f"raw recalled NOTHING: {total('a_empty')}")
    print(f"  of those, expand rescued: {total('rescued')}")
    print(f"total memories expand added (net): {total('added')}")


if __name__ == "__main__":
    main()
