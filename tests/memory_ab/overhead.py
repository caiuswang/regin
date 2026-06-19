"""Step 5 — per-prompt injection overhead: regin's `<recalled_experience>`
vs Hindsight's `<hindsight_memories>`.

Reconstructs each system's *actual* injected block for the probe prompts and
token-counts it with one shared tokenizer (tiktoken o200k_base) so the numbers
are comparable. Two regimes:

  controlled  — both recall from the identical 14-fact A/B corpus (fair,
                content-matched: isolates block format + how many entries
                each pulls).
  production  — regin's real memory DB vs Hindsight's real `claude_code`
                bank (the tax you actually pay per prompt today; the big
                bank injects far more than the tiny corpus).

    .venv/bin/python -m tests.memory_ab.overhead

Writes results/overhead.json. Read-only against production stores; the
controlled regime uses regin's throwaway DB and the isolated Hindsight bank.
"""

from __future__ import annotations

import json
import statistics
import urllib.request

import lib.memory as memory
from hook_manager.handlers import memory_recall as mr
from lib.settings import settings
from tests.memory_ab import spec
from tests.memory_ab.adapters import hindsight as hs
from tests.memory_ab.adapters import regin as rg

# Observed preamble of the live <hindsight_memories> block (fixed per-prompt
# overhead). Mirrors the plugin's recallPromptPreamble.
_HS_PREAMBLE = ("Relevant memories from past conversations (prioritize recent "
                "when conflicting). Only use memories that are directly useful "
                "to continue this conversation; ignore the rest:")


def _enc():
    import tiktoken
    return tiktoken.get_encoding("o200k_base")


def _ntok(enc, text: str) -> int:
    return len(enc.encode(text)) if text else 0


def _regin_block(query: str) -> tuple[str, int]:
    """Reconstruct regin's injected block + entry count for one prompt."""
    cfg = settings.agent_memory
    hits = memory.recall(query, top_k=cfg.inject_top_k, mode="auto",
                         include_tests=True, reinforce=False)
    if not hits:
        return "", 0
    block = mr._build_block(hits, cfg.inject_max_chars)
    entries = block.count("\n- ")
    return block, entries


def _hs_recall(bank: str, query: str) -> list[dict]:
    url = f"http://localhost:8888/v1/default/banks/{bank}/memories/recall"
    payload = {"query": query, "max_tokens": 1024, "budget": "mid"}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return (json.loads(resp.read()).get("results") or [])


def _hs_block(bank: str, query: str) -> tuple[str, int]:
    """Reconstruct Hindsight's injected block + entry count (the plugin's
    format_memories rendering) for one prompt."""
    results = _hs_recall(bank, query)
    if not results:
        return "", 0
    lines = []
    for r in results:
        t = f" [{r.get('type','')}]" if r.get("type") else ""
        d = f" ({r.get('mentioned_at','')})" if r.get("mentioned_at") else ""
        lines.append(f"- {r.get('text','')}{t}{d}")
    body = "\n\n".join(lines)
    block = (f"<hindsight_memories>\n{_HS_PREAMBLE}\nCurrent time - now\n\n"
             f"{body}\n</hindsight_memories>")
    return block, len(results)


def _measure(enc, blocks: list[tuple[str, int]]) -> dict:
    toks = [_ntok(enc, b) for b, _ in blocks]
    ents = [n for _, n in blocks]
    return {
        "mean_tokens": round(statistics.mean(toks), 1),
        "median_tokens": round(statistics.median(toks), 1),
        "max_tokens": max(toks),
        "mean_entries": round(statistics.mean(ents), 1),
        "prompts": len(toks),
    }


def _regin_regime(enc, probes, *, db_path, label) -> dict:
    if label == "controlled":
        rg._isolate_store(db_path)
        rg._ingest(spec.load_corpus())
    else:  # production — point at the real memory DB, read-only
        settings.agent_memory.db_path = ""
        memory.reset_store()
    blocks = [_regin_block(p["query"]) for p in probes]
    return _measure(enc, blocks)


def _hs_regime(enc, probes, *, bank, ingest) -> dict:
    if ingest:
        hs._clear()
        hs._ingest(spec.load_corpus())
    blocks = [_hs_block(bank, p["query"]) for p in probes]
    if ingest:
        hs._clear()
    return _measure(enc, blocks)


def run() -> dict:
    enc = _enc()
    probes = spec.load_probes()
    db = spec.RESULTS_DIR / ".tmp" / "regin_overhead.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    rg._cleanup_db(db)
    out = {
        "tokenizer": "o200k_base",
        "controlled": {
            "regin": _regin_regime(enc, probes, db_path=db, label="controlled"),
            "hindsight": _hs_regime(enc, probes, bank=hs.BANK, ingest=True),
        },
        "production": {
            "regin": _regin_regime(enc, probes, db_path="", label="production"),
            "hindsight": _hs_regime(enc, probes, bank="claude_code",
                                    ingest=False),
        },
    }
    rg.dispose_memory_engine()
    rg._cleanup_db(db)
    spec.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (spec.RESULTS_DIR / "overhead.json").write_text(json.dumps(out, indent=2))
    return out


def _print(out: dict) -> None:
    for regime in ("controlled", "production"):
        r = out[regime]["regin"]
        h = out[regime]["hindsight"]
        ratio = h["mean_tokens"] / max(r["mean_tokens"], 1e-9)
        print(f"\n[{regime}]  (tokenizer {out['tokenizer']})")
        print(f"  regin     : {r['mean_tokens']:7.1f} tok/prompt "
              f"(median {r['median_tokens']}, {r['mean_entries']} entries)")
        print(f"  hindsight : {h['mean_tokens']:7.1f} tok/prompt "
              f"(median {h['median_tokens']}, {h['mean_entries']} entries)")
        print(f"  hindsight injects ~{ratio:.1f}x regin's tokens per prompt")


def main() -> None:
    out = run()
    _print(out)
    print(f"\nwrote {spec.RESULTS_DIR / 'overhead.json'}")


if __name__ == "__main__":
    main()
