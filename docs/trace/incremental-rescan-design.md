# Implementation Spec — Incremental Transcript Rescan

Make the server-side live rescan cost **O(new bytes)** per poll instead of
**O(file size)**, by resuming a persistent scan accumulator from the last
committed byte offset rather than re-parsing the whole transcript each time.

Implemented: `read_usage_resumable` in `lib/trace/transcript_usage.py`, wired
into `lib/trace/live_rescan.py`. This doc records the design and the invariants
the implementation must keep holding.

---

## 1. Motivation

Assistant text and thinking have no Claude Code hook — they land only in the
transcript file. `lib/trace/live_rescan.py` closes that gap: while a session is
being viewed, the `/map?shallow` poll fires a fire-and-forget rescan that
re-reads the transcript and ingests turns the hook scan hasn't posted yet.

The rescan currently calls `_ingest_transcript_usage` →
`read_usage` (`lib/trace/transcript_usage.py`), which **stream-parses the entire
transcript** on every poll where the file's mtime changed (`live_rescan.py`,
`_file_changed` gate). For an active session that is O(file size) every poll and
O(file size²) over the session. Emission is already idempotent — the per-session
seen-uuid cache (`hook_manager/handlers/turn_trace/cache.py`) gates the HTTP
posts — so the wasted work is purely the **read + JSON-parse**, repeated over
bytes that haven't changed.

**Goal:** each poll parses only the bytes appended since the previous poll, while
producing a `TranscriptUsage` identical to a full `read_usage` over the same
final file.

## 2. Scope

In scope: the **server-side poll path only** (`live_rescan._do_rescan`). The Flask
server process is long-lived, so an in-memory accumulator can survive between
polls.

Out of scope: the hook handlers (`_emit_span`, `_emit_assistant_response_only` in
`hook_manager/handlers/turn_trace/entry.py`). Each hook fires in a short-lived
subprocess on a discrete event, so it cannot share an in-memory accumulator and
has no per-poll repetition to optimize. These keep calling the full
`_ingest_transcript_usage` / `read_usage` unchanged. The optimization is a new
resumable variant *alongside* the shared function, not a change to it.

## 3. Why a tail-only scan is insufficient

`read_usage` is one stream pass that accumulates cross-entry state in a
`_TranscriptScan`. Three pieces of that state reach arbitrarily far back across
any offset boundary, so the appended bytes must be fed into the **same**
accumulator, not a fresh scan over the tail:

1. **Prompt-anchor resolution** (`_resolve_anchor`) walks the `parentUuid` chain
   from a turn's assistant entry back to its opening prompt, which may sit
   megabytes earlier. It needs the full `entry_parent` graph plus the
   `real_prompt_uuids`, `command_prompt_uuids`, and `task_notification_uuids`
   sets accumulated from the whole file.
2. **In-flight turn builders** — one logical turn spans multiple `assistant`
   entries sharing a `message.id`; a mid-turn flush splits that turn across the
   offset boundary.
3. **`prev_entry_timestamp`** (per-API-call latency) depends on the entry
   immediately preceding the first appended one.

## 4. Design

### 4.1 Per-trace resumable state

`live_rescan` holds, keyed the same way `_last_mtime` is keyed today:

- the live `_TranscriptScan` accumulator (never reset between polls),
- the byte offset of the last consumed newline (the **committed offset**),
- the file's inode (`st_ino`) for replacement detection.

A new driver in `lib/trace/transcript_usage.py` — `read_usage_resumable(path,
state, *, max_text_bytes)` — does:

1. `stat` the file. If `st_ino` changed, or current `size < committed offset`,
   the file was replaced or truncated (compaction, `/clear` forward-copy): drop
   the accumulator and fall back to a full scan from offset 0.
2. `seek(committed offset)` and read to EOF.
3. Split on `\n`. A trailing fragment with **no** terminating newline is a
   partial flush: do not parse it and do not advance past it. Advance the
   committed offset only to the byte after the last complete `\n`.
4. Feed each complete line through the existing `scan.process_entry`.
5. Run a finalize that does not mutate the accumulator (§4.2) and return the
   `TranscriptUsage`.

Append-only JSONL makes "size grew, inode same" the normal path, so the
invalidation checks are a cheap `stat` in the common case.

### 4.2 Finalize must be pure with respect to the accumulator

`finalize` → `_resolve_prompt_anchors` currently **mutates** accumulator state,
which is safe only because today each scan finalizes exactly once. Under
resumption it finalizes on every poll, so the mutation corrupts state across
polls:

- `_resolve_prompt_anchors` does `command_prompt_uuids &= meta_expansion_parents`
  — a destructive intersection. Repeated finalize monotonically shrinks the set,
  so a command whose isMeta-expansion child arrives in a *later* poll was already
  intersected away by an *earlier* finalize and can never anchor its turn.
- `_promote_anchored_commands` mutates `real_prompt_uuids` and `prompt_texts`.

Fix: compute anchor resolution and command promotion against locals/copies,
never writing back to `self`. This is a precondition, not an add-on — and it is
a latent correctness improvement to the existing single-finalize path as well.

This corruption is invisible to a single-poll test; it appears only across
resumes, which §6 covers.

### 4.3 Subagent transcripts

`emit_subagent_responses` (`live_rescan._do_rescan`) re-reads each
`subagents/agent-*.jsonl` the same way and carries the same cost. Each subagent
transcript gets its own resumable state keyed by `agent_id`, applying the
identical pattern.

### 4.4 Lifecycle

The per-trace accumulator maps would otherwise grow like the existing
`_last_mtime` map (never evicted). The `SessionEnd` hook fires in a separate
subprocess and can't reach the server-process state, so there is no clean
session-end signal to evict on. Instead the maps are bounded by an LRU cap on
the most-recently-rescanned traces: a dropped-but-still-live session simply
re-parses once on its next poll (from offset 0) and then resumes incrementally,
so the cap trades a rare one-time full parse for a hard memory bound.

## 5. Invariants and blast radius

- **Equivalence invariant:** for any chunking of a fixed final transcript,
  feeding it through `read_usage_resumable` in N steps yields a `TranscriptUsage`
  equal to one `read_usage` over the whole file.
- **Emission stays seen-cache-gated**, so finalizing every poll and posting the
  full turn list remains safe — only read+parse cost changes.
- **Conservative fallback:** any unhandled edge (replacement, inode reuse,
  partial line, stat error) falls back to a full rescan — i.e. today's behavior.
  No correctness regression is possible if the invalidation checks stay
  conservative.

## 6. Verification

A single-poll rescan test passes trivially and hides every resume bug, so the
load-bearing test is **golden equivalence**: parametrize the chunk boundaries of
a fixed transcript and assert each incremental run equals the full
`read_usage`. Boundaries must deliberately land:

- mid-turn (between two `assistant` entries sharing one `message.id`),
- mid-line (partial JSON flush — assert the line is held, not dropped),
- across a slash-command-anchored turn (the command-promotion path §4.2
  attacks),
- across a `<task-notification>` boundary,
- after an inode change (assert full-rescan fallback).

These live in `tests/trace/test_transcript_usage.py` (accumulator- and
byte-level equivalence) and `tests/trace/test_live_rescan.py` (orchestration +
LRU bound).
