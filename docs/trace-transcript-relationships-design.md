# Design proposal — transcript relationships regin omits, and the minimal fix

Synthesis of Report A (code-side) + Report B (transcript-side), plus own spot-checks.
Every code claim below is `file:line`-cited and was grep/Read-verified this session
(not inferred from the reports alone).

Bottom line up front: **regin already captures the single most valuable ground-truth
edge and then never uses it.** The hook envelope's `prompt_id` (Claude Code 2.1.195+)
is written to every tool span as `attributes.source_prompt_id`
(`hook_manager/handlers/post_tool_trace.py:762-770`) and is consumed by **nothing** —
grep for `source_prompt_id` across `lib/ web/ frontend/src` returns only the writer,
the drift-allowlist, and tests. That one fact drives the whole recommendation: the
highest-leverage move is not to ingest something new, it is to *start consuming a fact
already on the row* so a heuristic can be retired. Almost everything else triages to
IGNORE.

Consuming it, though, needs a join partner, and that partner doesn't exist yet.
`source_prompt_id` is a *value* stamped on tool spans; the only spans that could carry
the same value — the `prompt-<uuid>` anchors — are keyed by the transcript entry's own
`uuid`, not by that value. The transcript's `promptId` field (the value's origin) is
read by **nothing** in `transcript_usage.py`, and does not equal the anchor's own
`uuid` (0/26 sampled prompt entries). So this design's flagship move is really two
captures wired into one join — consume `source_prompt_id` on tool spans (already
captured) *and* newly capture + stamp `promptId` onto the anchor it names — not the
single-sided consumption the first draft assumed.

---

## 1. GAP LIST (transcript fact → current regin handling → consequence)

Legend for "current handling": **read** = parsed during scan, **captured** = persisted
to a span/column, **consumed** = actually used to derive a relationship.

### G1 — `prompt_id` / `promptId` prompt-grouping edge (tool→prompt ground truth)
- **Transcript evidence (B, corrected):** `promptId` groups a prompt submission, but
  only **user** entries carry it — 0/351 sampled assistant entries have it. It also
  does **not** equal the prompt entry's own `uuid` (0/26 sampled prompt entries), so
  the anchor id `prompt-<uuid>` and the value `promptId` are different things. The
  hook envelope mirror `prompt_id` rides every PostToolUse payload (B: 2.1.195+; A does
  not cover the transcript field) and, newly confirmed, every hook-event payload
  including `UserPromptSubmit` (`lib/trace/payload_validation.py:72-77`) — and its
  *value* is the same UUID as the transcript's `promptId` on the entry that opened the
  prompt (verified on session `c053154b-6638-412a-9b42-a4834091f4c9`: a `tool.Edit`
  span's `source_prompt_id` == the tool_result entry's `promptId` == the initiating
  prompt entry's `promptId`).
- **Current handling (verified):** hook `prompt_id` is **captured** as
  `attributes.source_prompt_id` on `tool.*` spans (`post_tool_trace.py:764-770`) but
  **never consumed** (grep: zero readers in `lib/ web/ frontend/src`). The transcript
  `promptId` field is **not even read** — `grep promptId lib/trace/transcript_usage.py`
  is empty; the scanner instead reconstructs the prompt anchor by walking `parentUuid`
  in `_resolve_anchor` (`transcript_usage.py:1309-1340`), the walk that carries the
  `/review` off-by-one and the `<task-notification>` special-case.
- **Consequence:** the tool→issuing-turn relationship is derived **up to 3×** from the
  parentUuid chain (write-time `tool_use_to_turn`, ingest UPDATE `ingest.py:358-421`,
  serve-time ladder `projection.py:123-158`) — Report A §4 "computed three times" — while
  a direct, CLI-authoritative grouping key sits unused on the row. The
  `_TURN_LOOKAHEAD_SECONDS = 1.0` race window (`projection.py:34-45`) and the ladder's
  `resp-→think-→prompt` rung exist to paper over the walk's ambiguity at turn edges.

### G2 — `compact_boundary.logicalParentUuid` + `preservedSegment` (compaction lineage)
- **Transcript evidence (B §D):** `type=system, subtype=compact_boundary` is a **fresh
  root** (`parentUuid: null`); the *only* backward link is `logicalParentUuid` → tail of
  the preserved segment, plus `compactMetadata.{trigger,preTokens,postTokens,
  preservedSegment{head,anchor,tail},preservedMessages}`.
- **Current handling (verified):** regin marks compaction from **hooks only** —
  `compact_lifecycle.py:23,31` emit `compact.pre`/`compact.post` spans from
  PreCompact/PostCompact. The transcript `compact_boundary` node, `logicalParentUuid`,
  and `preservedSegment` are **not read at all** (grep: only projection *name constants*
  `_COMPACT_BOUNDARY_NAMES` at `projection.py:66`; no producer reads the transcript
  field).
- **Consequence:** for any session where the PreCompact/PostCompact hook did **not**
  fire (historical transcripts, hookless replay, auto-compaction on some CLI paths) the
  compaction boundary is invisible and post-compact prompts root at a null-parented
  boundary with no marker. Within a hooked session it is covered by the parallel hook
  mechanism, so the gap is *narrow* (hookless/historical only).

### G3 — `retractedMessageUuids` / `model_refusal_fallback` (silent model-swap, observability only)
- **Transcript evidence (B §D):** `system/model_refusal_fallback` carries
  `retractedMessageUuids: [...]`, `originalModel`, `fallbackModel`, `apiRefusalCategory`
  — the CLI's own record that it walked a response back after a refusal and silently
  retried on a fallback model.
- **Current handling (verified):** **not read.** grep `refusal|retract` in `lib/trace/
  hook_manager/` returns nothing (the `fallback_model` hits at
  `subagent_lifecycle.py:297`/`span_posters.py:630` are an unrelated
  "usage.model default" fallback, not refusal handling).
- **Consequence — corrected.** The first draft assumed a retracted subtree is an
  off-`parentUuid`-chain subtree that could trip `rewind_detect` and double-count
  tokens. Neither holds: a retracted message's uuid never appears as an entry in the
  transcript file at all — verified: grepping for one across a real
  `model_refusal_fallback` session (`0e76a3c5-...`) gets a single hit, the
  `retractedMessageUuids` array itself. The retry gets its own new `message.id`. An
  absent entry cannot form an off-chain subtree, so there is no transcript-side
  double-count and no `rewind_detect` false-positive risk to guard against — the
  retraction is invisible to both mechanisms by construction, not narrowly avoided by
  `rewind_detect`'s two-signal guard as previously claimed. G3's only remaining value
  is **observability**: today a model swap after a refusal leaves zero trace; recording
  it lets the trace explain a resolved-model or token-shape anomaly instead of leaving
  it unexplained.

### G4 — nested subagent parentage via `meta.json.toolUseId` / `spawnDepth`
- **Transcript evidence (B §D.5):** `subagents/` is a **flat** namespace even for depth
  2/3 agents; the true agent→agent parent is found by resolving a child's
  `meta.json.toolUseId` against the `tool_use` blocks of **sibling** agent transcripts.
  `spawnDepth` gives the tree level.
- **Current handling (verified):** the **workflow** ingester reads `meta.json`
  (`workflow_ingest.py:1187`) for `agentType`/label but **not** `toolUseId`/`spawnDepth`
  for parentage. The general subagent path keys spans by `agent_id` from the filename
  (A §1 `claude_subagents.py`) and reparents every `agent_id`-tagged span under the
  latest `subagent.start` for *that* agent (`projection.py:251-300`). `spawnDepth` is
  referenced only in a `repair.py:324` docstring.
- **Consequence:** a depth-2 agent's `subagent.start` is parented under the **main
  agent / prompt**, not under the depth-1 agent that spawned it — the nested-spawn tree
  is flattened one level. Correct within workflows (explicit run tree), wrong for
  ad-hoc `Agent`-spawns-`Agent` outside a workflow.

### G5 — cross-session `session_id` (snake_case) resume lineage
- **Transcript evidence — mechanism now settled:** from the first real turn onward,
  entries carry a snake_case `session_id` pointing at a **different, earlier**
  transcript. Verified: `03e946fd-...` opens with `SessionStart:clear`, and that stray
  `session_id` points at `7d593c11-...`, whose tail (`02:09:21Z`) precedes
  `03e946fd`'s first real prompt (`02:15:41Z`) by ~6 min. This is Claude Code's own
  `/clear`-continuation marker, not an ambiguous resume signal — B's original "a lead,
  not settled fact" is resolved on the mechanism, though the field is still narrower
  than a general `--resume`/`--continue` chain (confirmed only for `/clear`).
- **Current handling (verified):** **not read** for lineage (the `session_id`
  divergence hits in `workflow_ingest.py:1642,1688` are subagent reparenting, unrelated).
- **Consequence:** `--resume`/`--continue` chains are not linked; each transcript is an
  island. Low user-visible impact today.

### G6 — `attributionSkill` (turn → gating skill)
- **Transcript evidence (B §D):** every `assistant` entry carries `attributionSkill`
  (e.g. `"goal-verified-treenav"`) naming the skill whose rules gate that turn.
- **Current handling (verified):** **not read** (grep `attributionSkill|attribution_skill`
  empty across `lib/ hook_manager/ web/`).
- **Consequence:** no relationship loss — purely an unused *attribute*. Would enrich the
  turn card ("this turn ran under skill X") but is not an edge.

### G7 — `diagnostics.cache_miss_reason` (prompt-cache collapse explainer)
- **Transcript evidence (B §E.9):** assistant messages carry
  `diagnostics.cache_miss_reason = {type: tools_changed|messages_changed|
  previous_message_not_found, cache_missed_input_tokens}` — directly explains a cache
  collapse.
- **Current handling (verified):** **not read** (the `diagnostics` hits are all
  `settings.py` maintainer-diagnostics, unrelated).
- **Consequence:** no relationship loss; an *attribute* that would make the token panel
  self-explaining. Ties to the known "cache-surge diagnosis" investigations (memory
  `feedback_cache_surge_diagnosis_method`).

### G8 — `toolUseResult` materialized shape (richer than the `tool_result` block)
- **Transcript evidence (B §D):** top-level `toolUseResult` carries tool-specific
  structured output — Edit/Write `structuredPatch`/`userModified`, Bash
  `backgroundTaskId`/`interrupted`, ScheduleWakeup `scheduledFor`/`wasClamped`,
  completed sync `Agent` full `{toolStats,totalTokens,...}`.
- **Current handling (verified):** partially covered by **hooks** — `background_task_id`
  is captured from the Bash payload (`post_tool_trace.py:248`); error/`is_error` and
  captured tool_input are patched onto the turn's `tool_calls` from the `tool_result`
  block (A §1, `transcript_usage.py:853-886`). The richer `toolUseResult` sibling object
  is **not** read.
- **Consequence:** no *relationship* loss (the tool↔result edge is already the
  `tool_use_id` pairing). Only attribute richness (diff stats, clamp flags) is left on
  the floor. Out of scope for a relationship audit.

### G9 — `queue-operation` steer/notification FIFO (never persisted)
- **Transcript evidence (B §D):** `enqueue`/`dequeue`/`remove` with no id — paired by
  file order + content.
- **Current handling (verified):** **read transiently, never persisted** — A §1
  `queued_prompts.py:43-90` replays them for the live poll only.
- **Consequence:** intended; the dequeued prompt that actually fired is already
  registered into `real_prompt_uuids` (A §1, `transcript_usage.py:1036-1075`). No edge
  lost. Keep as-is.

### G10 — `file-history-snapshot` messageId chains
- **Transcript evidence (B §D):** outer `messageId` (filed-under) → inner
  `snapshot.messageId` (anchor baseline) chains backing `/rewind` file restore.
- **Current handling (verified):** **read and consumed** — raw rows handed to
  `lib/trace/file_history.py` for rewind file-rollback enrichment (A §1,
  `transcript_usage.py:770-771`). Not a gap. Listed for completeness.

---

## 2. TRIAGE

| Gap | Verdict | One-line rationale |
|---|---|---|
| **G1** prompt_id grouping | **CAPTURE (consume + join)** | `source_prompt_id` is already on tool rows; the missing half is a join partner. Stamping the transcript's `promptId` onto the `prompt-<uuid>` anchor gives both sides a shared value to match on, letting us *simplify* the ladder/lookahead and stop deriving tool→turn 3×. Highest leverage; the flagship move, rewired not dropped. |
| **G2** compaction lineage | **IGNORE (narrow) / small CAPTURE** | Covered by hook markers in live sessions; only hookless/historical replay is blind. Capture only if historical-replay fidelity is a real requirement. |
| **G3** refusal/retracted | **CAPTURE (observability only)** | Cheap read via the existing traced-system-event mechanism; no rewind or token-count value — retractions never appear as transcript entries, so there is nothing to suppress or double-count. Pure marker span recording that a model-swap happened. |
| **G4** nested subagent parent | **CAPTURE (targeted)** | Genuine one-level flattening bug for ad-hoc nested `Agent` spawns; fixable by reading a field already in `meta.json`. |
| **G5** cross-session resume | **IGNORE** | Mechanism now understood (`/clear`-continuation marker, not speculative); still single-session-scoped and out of this audit's relationship scope — belongs in a separate session-lineage feature. |
| **G6** attributionSkill | **IGNORE** | Attribute, not edge. Nice-to-have turn label; add opportunistically, not now. |
| **G7** cache_miss_reason | **IGNORE (opportunistic attribute)** | Attribute, not edge. Add to the turn-usage attrs if the token panel work is touched. |
| **G8** toolUseResult richness | **IGNORE** | No edge lost; attribute enrichment only. |
| **G9** queue-operation | **IGNORE (working as intended)** | Transient by design; the fired prompt is already captured. |
| **G10** file-history chains | **IGNORE (already consumed)** | Not a gap. |

**Capture list is deliberately short: G1 (the big one), G3, G4.** Everything else is
IGNORE-with-reason.

---

## 3. DESIGN PROPOSAL (minimal, coherent)

Design principle honored throughout: **append-only writes, all reconciliation at read
time in `merge.py`/`projection.py`; capture transcript ground truth to *delete* a
heuristic, never to add a parallel one; heal legacy rows at read time, never migrate.**

### Move 1 (G1) — Consume `source_prompt_id`; make it a promoted column; shrink the ladder

The edge already lands on the row (`post_tool_trace.py:770`); it is just stored in the
`attributes` JSON blob and never read. Two sub-steps:

**1a. Promote `source_prompt_id` to a real column on `session_spans`.** regin already
promotes join keys (`turn_uuid`, `tool_use_id`, `agent_id`) out of `attributes`
precisely so serve-time passes can filter cheaply (A §3). This is the same pattern.
- **SQLModel:** add `source_prompt_id: str | None` to the `SessionSpan` model in
  `lib/orm/models/trace.py` (alongside `turn_uuid`/`tool_use_id`/`agent_id`).
- **Schema drift (MANDATORY, per CLAUDE.md):** add the column to **`db/schema.sql`**
  `session_spans` DDL **and** ship an Alembic-style migration `ALTER TABLE session_spans
  ADD COLUMN source_prompt_id TEXT`. Fresh installs build from `schema.sql`, existing
  DBs get the migration — miss either and installs diverge.
- **Writer change: none in `post_tool_trace.py` — promote at insert time, mirroring
  `agent_id`.** `post_span` (`lib/hook_plugin.py:494-524`) takes no per-column kwargs
  (only attributes/parent_id/timing/status/span_id), so the column cannot be passed
  from the hook side. The codebase's actual promoted-column pattern is `agent_id`: the
  hook keeps writing it into `attrs`, and `_insert_span_row`
  (`lib/trace/trace_service/ingest.py:1031-1077`) derives the column from
  `attrs.get('agent_id')` at insert time (`ingest.py:1048`) while the value **stays in
  the attributes JSON too**, with `COALESCE(excluded.agent_id, session_spans.agent_id)`
  on re-ingest (`ingest.py:1066`). Do the same: leave `attrs['source_prompt_id']`
  (`post_tool_trace.py:764-770`) untouched, and add `source_prompt_id =
  attrs.get('source_prompt_id')` plus the column to the INSERT / ON CONFLICT in
  `_insert_span_row` — keep the value in attributes, NOT "instead of". (The
  `turn_uuid`/`tool_use_id` columns follow a different route — the lazy attribution
  UPDATE at `ingest.py:404-416`, with a `json_extract(attributes, …)` legacy fallback —
  not needed here, since the value is already present at insert time.) Rows inserted
  before this change carry the value only in `attributes`; readers use the
  column-then-attribute fallback, same shape as `_turn_uuid_of`
  (`projection.py:100-103`).

**1b. Rewire the join: stamp `promptId` onto the prompt anchor, then match
`source_prompt_id` against it *by value* — not by re-deriving the anchor's span id.**

The first draft of this move computed `prompt-<source_prompt_id[:13]>` and looked for
that exact span id. That is a **guaranteed no-op**: `source_prompt_id`'s value is the
transcript's `promptId`, and `prompt-<uuid>` anchors are keyed by the *prompt entry's
own* `uuid` — not by `promptId` (0/26 sampled prompt entries have `promptId == uuid`).
Verified empirically against 6 real sessions: 0/6 matched. The fix is a value-join,
wired at three points:

*(i) Capture `promptId` in the existing transcript scan and stamp it on the anchor.*
`_record_prompt_entry` (`lib/trace/transcript_usage.py:824-851`) already captures each
real-prompt user entry's `uuid` → text/timestamp/image-parts (the `prompt_texts`/
`prompt_timestamps`/`prompt_image_parts` fields declared at `transcript_usage.py:
676-680`). Add a parallel `prompt_ids: dict[str, str]` field to `_TranscriptScan` and
populate it there from `entry_n.get('prompt_id')` — Claude Code's `promptId`,
confirmed to survive `_normalize_dict_keys`'s camelCase→snake_case pass; nothing reads
it today (`grep promptId lib/trace/transcript_usage.py` is empty). Thread it through
`finalize()` (`transcript_usage.py:1461-1487`) the same way `prompt_texts`/
`prompt_timestamps` are filtered down to the anchored subset in `_anchor_side_tables`
(`transcript_usage.py:1489-1504`), and add a matching `prompt_ids: dict[str, str]`
field to `TranscriptUsage` (`lib/trace/transcript_models.py:232-259`, alongside
`prompt_texts`).

Stamp it at emission: `_post_prompt_anchor_spans` (`hook_manager/handlers/turn_trace/
span_posters.py:468-512`) — called from `entry.py:163-165` with `usage.prompt_texts`/
`usage.prompt_timestamps`/`usage.prompt_image_parts` — takes a fourth `prompt_ids`
argument; `_emit_one_prompt_anchor` (`span_posters.py:515-537`) and `_anchor_attrs`
(`span_posters.py:556-575`) add `attrs['prompt_id'] = prompt_id` when present. The
`prompt-<uuid>` span id itself is unchanged — this only adds an attribute.

*Hook-side placeholder, optional, not required for correctness:* the live
`promptlive-<hash>` PENDING placeholder (`hook_manager/handlers/prompt_trace.py:
143-165`, `_emit_placeholder`) could stamp the same value immediately from
`payload.raw.get('prompt_id')` — confirmed reachable, since `prompt_id` rides every
hook-event payload, not just PostToolUse (`lib/trace/payload_validation.py:72-77`,
`_HOOK_COMMON_KEYS`), and `HookPayload.raw` is the full normalized envelope
(`hook_manager/core.py:91,118-132`). Skippable for v1: the placeholder is deleted the
moment the transcript-scan anchor lands (module docstring, `prompt_trace.py:9-16`), so
it never survives long enough to be a serve-time join target.

*(ii) Serve-time rung 0: join by value, not by re-deriving an id.* In `projection.py`,
add a sibling of `_build_prompt_by_turn` (`projection.py:106-120`) —
`_build_prompt_by_source_id(out)` — that scans `name == 'prompt'` spans for
`attributes.prompt_id` and builds `{prompt_id_value: span_id}` for the window. In
`_ladder_orphans_by_turn` (`projection.py:123-158`), before the existing
`resp-`/`think-`/turn_uuid cascade, check the orphan's `source_prompt_id` (the 1a
column, falling back to `attributes.source_prompt_id` for pre-migration rows) against
this dict and parent there directly on a hit. This is CLI ground truth, so it beats
the coarser prompt-level attribution (`prompt_by_turn`) and the chronological
fallback — but the turn-level `resp-`/`think-` anchors, being the finer-grained
parent, are still tried first and win over the join when they exist for the turn
(see "Honest scope of the win" below).

**Read-side prerequisite — without it rung 0 is a silent no-op:** `_fetch_spans`
(`projection.py:48-62`) is the sole read path feeding `_graft_orphans` /
`_ladder_orphans_by_turn`, and it uses an **explicit SELECT column list** (`id,
trace_id, span_id, parent_id, name, kind, start_time, end_time, duration_ms,
attributes, status_code, status_message, output_tokens, input_tokens, image_tokens,
cost_usd, tool_use_id, turn_uuid, source`) — a new column is invisible to the
projection until it is added there. Move 1b must add `source_prompt_id` to that SELECT
list, or `s.get('source_prompt_id')` returns None on every span and rung 0 never
fires. The `attributes` fallback masks nothing here: the dict key would exist via
`attributes` only, so make the rung's accessor a `_source_prompt_id_of(span)` helper
with the same column-then-attribute shape as `_turn_uuid_of` (`projection.py:100-103`)
and add the column to the SELECT in the same commit. No other reader needs the column
for this design: rung 0 is the only consumer, and it runs entirely on `_fetch_spans`
output.

*(iii) Legacy rows fall through, no migration.* A `prompt-<uuid>` anchor emitted
before this change carries no `prompt_id` attribute, so it never enters
`_build_prompt_by_source_id`'s dict; any tool span whose `source_prompt_id` finds no
match there falls straight through to the unchanged `resp-`/`think-`/turn_uuid ladder
and chronological fallback. Same precedent already documented in the file:
"load-bearing for migration ... heals here at read time with zero re-emission"
(`projection.py:131-133`).

- **Honest scope of the win:** `prompt_id` groups by *prompt submission*, and one
  prompt can contain multiple assistant turns; tools still nest under the finer-
  grained *turn* (`resp-`/`think-`) when `turn_uuid` is present, so Move 1b does
  **not** delete those ladder rungs. What it *does* buy: (i) a correct parent for tool
  spans whose `turn_uuid` never got attributed (session ended mid-attribution) —
  today those fall through to the fuzzy chronological `_graft_orphans_under_prompt`;
  `source_prompt_id` now gives them a ground-truth prompt parent instead; (ii) a
  tiebreaker that lets us **narrow or delete `_TURN_LOOKAHEAD_SECONDS`**
  (`projection.py:34-45`) once measurement shows the lookahead rarely disagrees with
  the `source_prompt_id`-derived parent on 2.1.195+ sessions. Recommend: land 1a+1b,
  measure, then consider deleting the window in a follow-up if it goes to zero.

### Move 2 (G3) — Refusal/model-swap marker span (append-only, additive, observability only)

Extend the **existing** traced-system-event mechanism instead of adding a new one.
`transcript_usage.py` already special-cases three `system` subtypes — `turn_duration`,
`stop_hook_summary`, `away_summary` — behind a single frozenset gate,
`_TRACED_SYSTEM_SUBTYPES` (`transcript_usage.py:126-130`). Any subtype in that set is
captured as a generic `TranscriptSystemEvent` by `_emit_system_event`
(`transcript_usage.py:990-1016`), which stores the entry's full normalized payload
verbatim (`payload=entry_n`) plus a `turn_uuid` resolved by walking `parentUuid` back
through intervening `system` entries — so no new parsing is needed to reach
`retractedMessageUuids`/`originalModel`/`fallbackModel`/`apiRefusalCategory`; they ride
`ev.payload` for free once the subtype is in the set. Wiring a new traced subtype
touches exactly 3 sites:
1. Add `'model_refusal_fallback'` to `_TRACED_SYSTEM_SUBTYPES`.
2. Write an emitter (`_emit_model_refusal_span`, a new sibling of
   `_emit_stop_summary_span`/`_emit_away_summary_span`, `span_posters.py:63-118`) that
   posts a `harness.model_refusal` span with `attributes.{original_model,
   fallback_model, api_refusal_category, retracted_message_uuids}`, id
   `sys-<uuid[:13]>` — the same shape as the existing `hook.stop_summary`/
   `harness.recap` spans.
3. Register it in `_SYSTEM_EVENT_EMITTERS` (`span_posters.py:121-124`).

- **Relationship value: none, by design.** No rewind-suppression, no token
  double-count fix — the corrected evidence (see G3 above) shows neither problem
  exists: retracted uuids never appear as transcript entries, so there is no
  off-chain subtree to suppress and no duplicate entry to double-count tokens from.
  This is a pure observability marker: it lets a session's turn-usage panel or
  timeline show *"this turn's response was refused and silently retried on
  claude-opus-4-8"* instead of a silent, unexplained model/token discontinuity.
- **Schema:** no new column — a span with attributes, using the existing
  `session_spans` shape. No `schema.sql` change.

### Move 3 (G4) — Nested subagent parent via `meta.json.toolUseId`

Where subagent spans are built (`claude_subagents.py`, A §1), read the sibling
`meta.json`'s `toolUseId` and `spawnDepth` (the fields `repair.py:324` already
documents). Resolve `toolUseId` against the `tool_use` blocks of the other agent
transcripts in the same flat `subagents/` dir (B §D.5) to find the **parent agent_id**;
stamp it as `attributes.parent_agent_id` on the subagent's spans.
- **Consume at serve time:** in `_reparent_subagents` (`projection.py:251-300`), when a
  span carries `parent_agent_id`, prefer nesting its `subagent.start` under **that
  agent's** latest `subagent.start` before falling back to the current "under main /
  latest subagent.start" rule. One added preference branch, not a new pass.
- **Schema:** `parent_agent_id` can live in `attributes` (it is not a hot join key like
  `agent_id`); promote to a column only if a serve-time filter proves slow. Start in
  attributes → **no `schema.sql` change**.
- **Legacy:** old subagent rows lack `parent_agent_id` → existing flat behavior. No
  migration.

### What we explicitly DO NOT do

- **No write-time reconciliation / no destructive rewrite.** All three moves either add
  an append-only marker (Move 2), stamp an attribute/column at write time and *consume*
  it at read time (Moves 1, 3), or heal legacy at read time. `session_spans` stays
  append-only.
- **No new relationship table.** No `span_edges`/`prompt_groups` side table — the edges
  ride existing columns/attributes. (A grand "materialize the parentUuid graph verbatim"
  table was considered and rejected: it duplicates what deterministic span-ids already
  encode and would need its own dedup/supersession, violating the simplicity goal.)
- **No G5 cross-session lineage, no G6/G7/G8 attribute enrichment, no G9 change.**
  Attributes (G6/G7) may be added opportunistically when the turn/token panels are next
  touched; they are not part of this audit's deliverable.
- **No deletion of the `resp-`/`think-` ladder rungs.** They still carry turn-level
  granularity that `prompt_id` (prompt-level) cannot replace. Only the
  `_TURN_LOOKAHEAD_SECONDS` window and the fuzzy chronological fallback for
  un-attributed tools are *candidates* for retirement — and only after Move 1b data
  confirms it.
- **No `rewind_detect` changes for G3.** The suppression-set idea from the first draft
  is retracted outright, not deferred: retracted uuids never appear as transcript
  entries, so there is no false-positive class in `rewind_detect` to suppress in the
  first place. Move 2 is a marker span only.

---

## 4. RISK / EFFORT

| Item | Effort | Risk | Covered by |
|---|---|---|---|
| **1a** promote `source_prompt_id` → column (SQLModel + `schema.sql` + migration + insert-time derivation in `_insert_span_row`, `ingest.py:1031-1077`; hook writer unchanged) | **S** | Low — additive nullable column derived from attrs at insert like `agent_id`; the two traps are schema drift (fold into `schema.sql` per CLAUDE.md gotcha) and forgetting the ON CONFLICT COALESCE for the new column | `hook_manager/tests/test_post_tool_trace_contract.py:365-375` already asserts `source_prompt_id` capture in attrs; add an `_insert_span_row` round-trip test asserting the column is populated from attrs and survives re-ingest |
| **1b** rewired join: capture+stamp `promptId` on the anchor (`transcript_usage.py`, `transcript_models.py`, `span_posters.py`, `prompt_trace.py` optional) + serve-time value-join rung 0 + `source_prompt_id` added to the `_fetch_spans` SELECT (`projection.py:48-62`) | **M** | Medium — touches 5-6 files across the write and serve paths (transcript scan, dataclass, span emission, ladder, fetch). Mis-parenting risk is contained (explicit value join; a miss falls through to the existing ladder) — the real failure mode is the **silent no-op**: skip the `_fetch_spans` SELECT addition or the attrs fallback and rung 0 never fires while everything still renders via the old ladder | `transcript_usage` golden tests for the new `prompt_ids` field; `projection.py` ladder tests — new fixture asserting a `source_prompt_id` tool parents to the anchor whose `attributes.prompt_id` matches (not the old string-derived id), that a legacy anchor with no `prompt_id` falls through cleanly, and an end-to-end fixture through `_fetch_spans` (not hand-built dicts) so a missing SELECT column fails the test |
| **1b′** narrow/delete `_TURN_LOOKAHEAD_SECONDS` (follow-up) | **S** | Medium until measured — gate on "zero lookahead relabels on 2.1.195+ sessions" before deleting | existing `_relabel_turns_by_lookahead` tests must go green with window→0 |
| **2** refusal/model-swap marker (observability only) | **S** | Low — additive span via the existing 3-site traced-subtype pattern; no `rewind_detect` changes, no suppression logic to get wrong | New `model_refusal_fallback` fixture (real file `0e76a3c5-...`) asserting the `harness.model_refusal` span posts with the right attrs; no `rewind_detect.py` changes needed |
| **3** nested subagent `parent_agent_id` | **M** | Medium — `toolUseId`-against-siblings resolution can misfire if two agents share a `toolUseId` (shouldn't, but guard); serve-time branch is contained | `projection.py` `_reparent_subagents` tests; add a depth-2 fixture (B cites real files `1bfe15b4...` + subagents) |

Suggested landing order: **1a → 1b → 2 → 3**, then measure and consider **1b′**.
1a alone is a safe, self-contained "stop throwing away data we capture" commit.

---

## 5. Things I could NOT verify (flagged for the caller)

- **Exact granularity payoff of Move 1b** (how many spans the lookahead window still
  moves once the `source_prompt_id` ↔ `prompt.attributes.prompt_id` value-join is
  live) is unmeasured — hence 1b′ is gated on a measurement, not asserted. I could not
  run that measurement read-only without materializing projections over a real
  session.
- **Whether any live CLI path emits `compact_boundary` in the transcript WITHOUT firing
  PreCompact/PostCompact** (which would widen G2 from "narrow" to "real") — I confirmed
  the hooks emit `compact.pre/post` (`compact_lifecycle.py:23,31`) and that the
  transcript field is unread, but did not establish a session where the hook is absent
  yet the boundary exists. G2 stays IGNORE-narrow pending that evidence.

### Settled since the first draft (previously listed here as unverified, now resolved)

- **G3's rewind/double-count risk is not "unconfirmed," it's contradicted.** Retracted
  message uuids never appear as transcript entries at all (single grep hit = the
  `retractedMessageUuids` array itself, on a real `model_refusal_fallback` session);
  the retry gets a new `message.id`. An absent entry cannot form an off-`parentUuid`-
  chain subtree, so there is no fork for `rewind_detect` to false-positive on and no
  duplicate entry to double-count tokens from. G3 is now scoped to observability only
  (Move 2) — no insurance value was ever there to lose.
- **G5's `session_id` mechanism is a `/clear`-continuation marker, not an unexplained
  resume signal.** `03e946fd-...` opens with `SessionStart:clear`; its stray
  snake_case `session_id` points at `7d593c11-...`, the predecessor transcript.
  Triage is unchanged (IGNORE — still a single-session-scoped feature outside this
  audit's relationship-edge scope), but the field's meaning is no longer speculative.
