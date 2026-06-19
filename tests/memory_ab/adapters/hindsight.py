"""Hindsight adapter for the memory A/B harness.

Drives Hindsight's local REST API (the same backend the MCP server wraps,
http://localhost:8888) so the whole run is scriptable and rerunnable rather
than a hand-driven MCP runbook. Ingests the shared corpus into the isolated
`claude_code_abtest` bank, runs the probe queries, exercises the supersede
lifecycle, and writes the common result dump to results/hindsight.json.

Hindsight is lossy where regin is verbatim: `retain` runs each item through a
fact-extraction LLM that paraphrases and strips inline text — including our
`[ref:AB-<id>]` sentinel. So corpus ids are resolved from the surviving
`document_id` / `tags`, not the body text.

    .venv/bin/python -m tests.memory_ab.adapters.hindsight

Requires the Hindsight container up with a funded extraction LLM (a 402 from
the provider makes every write fail — see README).
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

from tests.memory_ab import spec

BANK = "claude_code_abtest"
BASE = f"http://localhost:8888/v1/default/banks/{BANK}"
_LIFECYCLE_QUERY = "what is the auth token cache TTL right now?"


def _request(method: str, path: str, payload: dict | None,
             timeout: float) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode()
    return json.loads(body) if body else {}


def _doc_id(corpus_id: str) -> str:
    return f"AB-{corpus_id}"


def _resolve(hit: dict) -> str | None:
    """corpus id from the surviving document_id / tags (the body sentinel is
    stripped by extraction)."""
    did = hit.get("document_id") or ""
    if did.startswith("AB-"):
        return did[3:]
    for tag in hit.get("tags") or []:
        if tag.startswith("AB-"):
            return tag[3:]
    return None


def _clear() -> None:
    """Delete every document in the bank so a run starts pristine."""
    listing = _request("GET", "/documents?limit=200", None, 15)
    items = listing.get("items", []) if isinstance(listing, dict) else []
    for doc in items:
        did = doc.get("document_id") or doc.get("id")
        if did:
            try:
                _request("DELETE", f"/documents/{did}", None, 15)
            except urllib.error.HTTPError:
                pass


def _retain(corpus_id: str, content: str, *, replace: bool = False) -> bool:
    item = {"content": content, "context": "general",
            "document_id": _doc_id(corpus_id), "tags": [_doc_id(corpus_id)]}
    if replace:
        item["update_mode"] = "replace"
    resp = _request("POST", "/memories", {"async": False, "items": [item]}, 120)
    return bool(resp.get("success"))


def _ingest(corpus: list[dict]) -> list[dict]:
    records: list[dict] = []
    for entry in corpus:
        t0 = time.perf_counter()
        try:
            ok = _retain(entry["id"], spec.body_with_ref(entry))
        except urllib.error.HTTPError:
            ok = False
        dt = (time.perf_counter() - t0) * 1000.0
        records.append({"corpus_id": entry["id"], "ok": ok,
                        "latency_ms": round(dt, 2),
                        "system_id": _doc_id(entry["id"]) if ok else None})
    return records


def _hits_for(query: str, *, top_k: int) -> list[dict]:
    resp = _request("POST", "/memories/recall",
                    {"query": query, "max_tokens": 3072}, 40)
    results = resp.get("results") or []
    out: list[dict] = []
    for rank, hit in enumerate(results[:top_k], start=1):
        out.append({"corpus_id": _resolve(hit), "rank": rank, "score": None,
                    "text": (hit.get("text") or "")[:160]})
    return out


def _query(probes: list[dict], *, top_k: int) -> list[dict]:
    results: list[dict] = []
    for probe in probes:
        results.append({
            "probe_id": probe["id"], "query": probe["query"],
            "expect_ids": probe.get("expect_ids", []),
            "must_not_ids": probe.get("must_not_ids", []),
            "hits": _hits_for(probe["query"], top_k=top_k),
        })
    return results


def _lifecycle(corpus: list[dict], *, top_k: int) -> dict | None:
    entry = next((e for e in corpus if e.get("lifecycle") == "supersede"), None)
    if entry is None:
        return None
    _retain(entry["id"],
            spec.body_with_ref(entry, body=entry["supersede_body"]),
            replace=True)
    hits = _hits_for(_LIFECYCLE_QUERY, top_k=top_k)
    flags = [spec.lifecycle_classify(h["text"])
             for h in hits if h["corpus_id"] == entry["id"]]
    new_surfaced = any(f[0] for f in flags)
    old_surfaced = any(f[1] for f in flags)
    return {"old_corpus_id": entry["id"], "old_retired": not old_surfaced,
            "new_surfaced": new_surfaced, "old_surfaced": old_surfaced}


def run(*, top_k: int, keep: bool) -> str:
    corpus = spec.load_corpus()
    probes = spec.load_probes()
    _clear()
    ingest = _ingest(corpus)
    queries = _query(probes, top_k=top_k)
    lifecycle = _lifecycle(corpus, top_k=top_k)
    if not keep:
        _clear()
    payload = {
        "system": "hindsight", "run_id": time.strftime("%Y%m%dT%H%M%S"),
        "mode": "rest-recall", "top_k": top_k,
        "corpus_ids": [e["id"] for e in corpus],
        "ingest": ingest, "queries": queries, "lifecycle": lifecycle,
    }
    return str(spec.write_dump(payload))


def main() -> None:
    ap = argparse.ArgumentParser(description="Hindsight memory A/B adapter")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--keep", action="store_true",
                    help="leave the corpus in the bank after the run")
    args = ap.parse_args()
    path = run(top_k=args.top_k, keep=args.keep)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
