"""Compare raw-prompt recall vs. agent-extension recall, on real traces.

The proposal under test: instead of recalling on the raw user prompt (the
current hook) or an LLM rewrite (`compare_recall_strategies.py`), let the
*main agent* restate the task in its own words first, then recall on that.

The agent's restatement already exists in every captured trace: the first
`assistant_response` after a prompt is the agent extending the prompt with
full session context. So this replays the strategy on real data with zero
LLM cost and zero added latency — exactly the two costs that sank the
cheap-LLM expansion. Query C = raw prompt + that first response.

Usage: .venv/bin/python scripts/compare_agent_extension_recall.py [N] [--ungated]
"""

from __future__ import annotations

import json
import sqlite3
import sys

from scripts.compare_recall_strategies import _fmt, _gate, _ids, recall

TRACE_DB = "db/regin.db"
_UNGATED = "--ungated" in sys.argv


def _usable_prompt(a: dict, t: str, seen: set[str]) -> bool:
    if a.get("slash_command") or t.startswith("/"):
        return False
    if "<task-notification>" in t or "<command-" in t:
        return False
    return (40 <= len(t) <= 600) and t[:60] not in seen


def prompt_extension_pairs(n: int) -> list[tuple[str, str]]:
    """Real (prompt, agent-extension) pairs: each prompt paired with the
    first assistant_response that followed it in the same trace."""
    db = sqlite3.connect(TRACE_DB)
    db.row_factory = sqlite3.Row
    prompts = db.execute(
        "select trace_id, start_time, attributes from session_spans "
        "where name='prompt' order by start_time desc limit 1500").fetchall()
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for pr in prompts:
        a = json.loads(pr["attributes"])
        t = (a.get("text") or "").strip()
        if not _usable_prompt(a, t, seen):
            continue
        resp = db.execute(
            "select attributes from session_spans where name='assistant_response' "
            "and trace_id=? and start_time>? order by start_time limit 1",
            (pr["trace_id"], pr["start_time"])).fetchone()
        if not resp:
            continue
        ext = (json.loads(resp["attributes"]).get("text") or "").strip()
        if len(ext) < 20:
            continue
        seen.add(t[:60])
        out.append((t, ext))
        if len(out) >= n:
            break
    return out


def _compare(i: int, raw: str, ext: str) -> dict:
    query_c = f"{raw}\n\n{ext}"[:2000]
    gate = (lambda h: h) if _UNGATED else _gate
    a, c = gate(recall(raw)), gate(recall(query_c))
    ids_a, ids_c = _ids(a), _ids(c)
    print(f"\n=== [{i}] {'SAME' if ids_a == ids_c else 'CHANGED'} ===")
    print(f"  RAW       : {raw[:120]}")
    print(f"  AGENT EXT : {ext[:200]}")
    print(f"  A (raw recall):\n{_fmt(a)}")
    print(f"  C (agent-extension recall):\n{_fmt(c)}")
    if ids_a != ids_c:
        print(f"  C added: {sorted(ids_c - ids_a)}  "
              f"C dropped: {sorted(ids_a - ids_c)}")
    return {"same": ids_a == ids_c, "a_empty": not ids_a,
            "rescued": (not ids_a) and bool(ids_c),
            "added": len(ids_c - ids_a)}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(args[0]) if args else 12
    pairs = prompt_extension_pairs(n)
    stats = [_compare(i, raw, ext) for i, (raw, ext) in enumerate(pairs)]

    def total(key: str) -> int:
        return sum(s[key] for s in stats)
    print("\n" + "=" * 60)
    print(f"prompts             : {len(stats)}")
    print(f"same result         : {total('same')}")
    print(f"changed result      : {len(stats) - total('same')}")
    print(f"raw recalled NOTHING: {total('a_empty')}")
    print(f"  of those, ext rescued: {total('rescued')}")
    print(f"total memories ext added (net): {total('added')}")


if __name__ == "__main__":
    main()
