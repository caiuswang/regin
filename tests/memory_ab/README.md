# Memory A/B harness — regin-native vs Hindsight

Compares regin's native memory against Hindsight on four dimensions:
**recall precision**, **capture round-trip**, **per-prompt overhead**, and
**dedup & lifecycle**. Both systems ingest one *identical* corpus so a
measured gap is a system gap, not a content gap.

## Fairness model

- `corpus.jsonl` is the single source of truth. Both adapters ingest
  `spec.body_with_ref(entry)` — the **identical** body text plus a
  `[ref:AB-<id>]` sentinel. Queries never contain the sentinel, so it is
  inert for retrieval but lets any recalled hit map back to its corpus id
  regardless of which system's ids came back. Scoring is system-agnostic.
- Metadata maps best-effort (schemas aren't 1:1). Fields with no Hindsight
  equivalent (`kind`, `veracity`, `importance`) are **regin-only signal** —
  if regin wins partly because of them, that's a real finding, noted as such.
- Hindsight is **lossy on ingest**: `retain` runs each item through a
  fact-extraction LLM that paraphrases the body and strips inline text — the
  `[ref:AB-<id>]` sentinel does **not** survive. So the Hindsight adapter
  resolves corpus ids from the surviving `document_id` / `tags`, not the body.
  regin stores verbatim, so its sentinel survives and resolves directly.
- `regin` ingests into a throwaway isolated DB (`results/.tmp/regin_ab.db`,
  deleted on teardown) so only the corpus exists — the parallel to a fresh
  Hindsight bank (`claude_code_abtest`). Neither touches production memory.
- The scorer's fairness gate refuses to compare two systems unless their
  ingested corpus-id sets are identical.

### Field mapping

| canonical | → regin `MemoryInput` | → Hindsight `retain` |
|---|---|---|
| `body` (+ref) | `body` (identical) | content (identical) |
| `title` | `title` | prepend / metadata |
| `tags` | `tags` | tags |
| `kind` | `kind` | — (regin-only) |
| `scope` | `scope` | bank |
| `importance` / `veracity` | native | — (regin-only) |

## Files

| file | role |
|---|---|
| `corpus.jsonl` | 14 facts (incl. near-neighbor lures + a supersede pair) |
| `probes.jsonl` | 16 queries: positive, discrimination (expect+must_not), pure-negative |
| `spec.py` | loaders, ref sentinel, common result-dump shape |
| `adapters/regin.py` | scriptable: isolate → ingest → query → lifecycle → dump |
| `adapters/hindsight.py` | scriptable over Hindsight's REST API (`localhost:8888`) |
| `overhead.py` | step 5: per-prompt injection-block token cost (both regimes) |
| `dedup.py` | step 6: cross-system double-injection tax (shared corpus) |
| `scorer.py` | reads dumps + overhead + dedup → `results/scorecard.md` |

## Metrics

- **recall@1 / recall@3 / MRR** — over single-fact probes.
- **lure beats target** — discrimination probes (expect AND must_not):
  fraction where the near-neighbor lure outranks the right answer.
  Corpus-size-independent (unlike a top-k false-positive count, which is
  meaningless on a 14-item corpus where top-5 is 36% of everything).
- **negative rank-1 leak** — pure-negative probes (no right answer):
  fraction whose rank-1 hit is a lure. Measures calibration on
  unanswerable queries; a system with a confidence floor scores lower.
- **capture ok / round-trip** — ingest success, and each fact recallable
  by its own query immediately after write.
- **lifecycle** — supersede retires the old memory, the replacement body
  surfaces, the old body does not. PASS/FAIL.

## Run

```bash
# regin side (real dense+rerank path)
.venv/bin/python -m tests.memory_ab.adapters.regin --mode auto
# (--mode fts skips the model load but is near-flat noise on a tiny corpus)

# Hindsight side (needs the container up + a funded extraction LLM)
.venv/bin/python -m tests.memory_ab.adapters.hindsight

# per-prompt injection overhead (step 5) + cross-system dedup (step 6)
.venv/bin/python -m tests.memory_ab.overhead
.venv/bin/python -m tests.memory_ab.dedup

# score (folds in overhead.json + dedup.json if present)
.venv/bin/python -m tests.memory_ab.scorer
```

Each adapter is self-isolating: regin uses a throwaway DB, Hindsight clears
the `claude_code_abtest` bank before and after a run (pass `--keep` to leave
the corpus in place for inspection).

## Status: all four dimensions built

- **Recall precision** — `scorer.py` over the step-4 dumps (recall@1/@3, MRR,
  lure-discrimination, negative-leak).
- **Capture round-trip** — ingest success + each fact recallable by its own
  probe (in the adapters / scorer).
- **Per-prompt overhead** — `overhead.py` reconstructs each system's actual
  injected block and token-counts it (controlled + production regimes).
- **Dedup & lifecycle** — supersede correctness in the adapters; `dedup.py`
  measures the cross-system double-injection tax.

Everything folds into `results/scorecard.md`. The whole harness reruns from
the four commands above (Hindsight side needs the container up + funded
extraction LLM).
