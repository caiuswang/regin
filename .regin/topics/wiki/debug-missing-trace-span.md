# Debug a missing trace span (hooks-log triage, ingest-errors, seen-uuid cache lockout, repair endpoint)

A Claude Code / Codex / generic-provider session shows fewer rows in regin's `session_spans` table than the on-disk JSONL transcript holds. This topic walks an agent end-to-end through diagnosis and recovery using only shell + the regin venv. The `query({query: "repair session spans"})` gitnexus process centres on the `turn_trace` module (`hook_manager/handlers/turn_trace/`) plus `lib/trace/repair.py`; those two modules are the whole surface.

## When this topic applies

Reach for this topic whenever a user reports any of:

* The whole conversation is empty in `session_spans` for that `trace_id` after the first prompt.
* Conversation rows are present up to some point, then text turns stop showing.
* Text turns (`assistant_response` / `assistant.thinking`) are present but synthetic tool spans (server-side `srvtool-*`, permission denies `askdeny-*` / `tooldeny-*`, `tool_use_error` envelopes `toolerr-*`) are absent — or the reverse.
* Harness attachments (`harness.skill_listing`, `harness.task_reminder`, `harness.local_command`, `harness.tools_delta`) are absent while text turns are present.
* Slash-command artefacts (`cmd-*`, `sys-*`) or queued-command (`prompt-*`) spans are absent.

Anything keyed off the per-session seen-uuid cache (`hook_manager/handlers/turn_trace/cache.py`) can drop out independently.

## Diagnostic procedure

Run the steps in order. Substitute `<trace_id>` (the provider session UUID). Steps 1–4 are triage; run the repair (Step 5) only once Step 4 confirms a cache lockout. `<data_dir>` defaults to `~/.local/share/regin` (see `lib/settings.py`); in a dev checkout it may be overridden to the repo's `db/`, so resolve the DB and state-dir paths against the running config if the defaults are empty.

### Step 1 — Check the hook activity log

The single biggest cause of a missing span is that the relevant hook never fired (or failed before reaching the emit pipeline). The hook dispatcher in `hook_manager/runner.py::handle` binds an activity-log channel under feature `hooks` (`get_activity_logger("hooks")`, `runner.py:125`) and writes one of three event names per handler invocation:

* `handler_dispatched` (`runner.py:137`) — handler returned normally; carries `handler` + `elapsed_ms`.
* `handler_failed` (`runner.py:142`, via `.error(exc_info=True)`) — handler raised; carries `error_type` and a traceback.
* `handler_slow` (`runner.py:147`, via `.warn`) — handler exceeded `_SLOW_HANDLER_MS` (env `HOOK_MANAGER_SLOW_MS`, default 500 ms, `runner.py:27`); carries `elapsed_ms` + `threshold_ms`.

The activity log lives under `settings.log_dir` (default `<data_dir>/logs/regin.log`); the `regin logs` CLI in `cli/commands/logs.py` is the canonical view.

```
.venv/bin/python cli/regin.py logs tail --feature hooks -n 200
.venv/bin/python cli/regin.py logs grep 'handler_failed|handler_slow' --feature hooks
```

Interpretation:

* **No `handler_dispatched` rows for `turn_trace` around the missing-turn timestamp** → the hook simply didn't fire. Inspect the active provider's hook config (`get_active_provider().hook_settings_path()` — Claude at `lib/providers/claude/__init__.py:175`) and its hook-manager wiring. Repair can still recover the data after the hook chain is fixed, because the transcript on disk still carries the raw turns.
* **`handler_failed` rows for `turn_trace`** → read `error_type` + traceback. A repeated `OSError` on the cache directory or a JSON parse error on the transcript path blocks ingest; fix the root cause before repair, otherwise repair re-fails the same way.
* **`handler_slow` rows for `turn_trace`** → the handler finishes but past the slow threshold. Usually benign, but a chronically slow `turn_trace` can race with subsequent hook fires; note it for context.

The runner also mirrors the same failure/slow signals as JSON lines to `<provider.traces_dir()>/hook-errors.jsonl` (`hook_manager/runner.py::_log_slow` at line 39, `_log_error` at line 59) — handy when the activity log is unavailable, though `regin logs` is the primary surface.

### Step 2 — Check the ingest-errors log

Every time `lib.hook_plugin.post_event` (`lib/hook_plugin.py:369`) fails to deliver a span to the web server, it appends to `<provider.traces_dir()>/ingest-errors.jsonl` (`lib/hook_plugin.py:35` resolves the path; `_log_ingest_error` at `lib/hook_plugin.py:175` writes the row). `post_event` retries transient failures up to `REGIN_INGEST_RETRIES` (default 3, `lib/hook_plugin.py:402`) with exponential backoff and records `attempt`, `max_attempts`, and `gave_up` per attempt — `gave_up: true` marks a permanent loss on the final attempt; `gave_up: false` is a retryable transient. The HTTP surface for the same log is `/api/ingest-errors` (`web/blueprints/trace/ingest_errors.py:41`), which also aggregates `by_endpoint` / `by_error_type` / `by_gave_up`.

```
tail -n 200 ~/.claude/traces/ingest-errors.jsonl
curl -s 'http://127.0.0.1:8321/api/ingest-errors?limit=100' -H "Authorization: Bearer $TOKEN"
```

A cluster of `gave_up: true` rows with `endpoint: session_spans` aligned to the missing-turn timestamp is the classic root cause: the post failed, the seen-uuid cache caught the uuid, the live handler now skips it. Step 5's repair recovers from this; nothing else does.

### Step 3 — Confirm the JSONL transcript still holds the missing turns

The active provider writes one JSONL per session under its `transcript_projects_dir()`:

* Claude default: `~/.claude/projects/<munged_cwd>/<trace_id>.jsonl` (`lib/providers/claude/__init__.py:184`).
* Codex default: `~/.codex/sessions/<...>/<trace_id>.jsonl` (`lib/providers/codex/__init__.py:138`).
* Generic default: `~/.agent/projects/<...>/<trace_id>.jsonl` (`lib/providers/generic/__init__.py:72`).

```
find ~/.claude/projects -name '<trace_id>.jsonl' -print -quit
# or for Codex:
find ~/.codex/sessions -name '<trace_id>.jsonl' -print -quit
```

Check that an assistant entry exists for the missing turn:

```
grep -c '"type":"assistant"' '<jsonl>'
```

With no record of the missing turn in the JSONL, this topic does not apply — the gap is upstream of regin. Report the transcript gap and stop.

### Step 4 — Compare the seen-uuid cache against `session_spans`

The seen-uuid cache lives at `<data_dir>/turn_trace_state/<trace_id>.txt` — or `$REGIN_TURN_TRACE_STATE_DIR/<trace_id>.txt` when that env var is set (`hook_manager/handlers/turn_trace/cache.py::_state_dir` at line 26, `_cache_path` at line 35). One transcript-entry uuid per line. The ingested spans live in `<data_dir>/regin.db::session_spans`:

```
wc -l ~/.local/share/regin/turn_trace_state/<trace_id>.txt
sqlite3 ~/.local/share/regin/regin.db \
  "SELECT name, count(*) FROM session_spans WHERE trace_id='<trace_id>' GROUP BY name ORDER BY name"
```

Rough invariant in a healthy session: cache lines ≈ turns + attachments + system_events + local_commands. A cache materially larger than the count of expected spans — especially when Step 2 turned up ingest errors at matching times — is the lockout signature.

For a finer cut, list cached uuids whose expected `resp-*` span is absent. The cache stores 36-char uuids; deterministic span_ids embed only the first 13 chars, so compare on the prefix:

```
comm -23 \
  <(sort ~/.local/share/regin/turn_trace_state/<trace_id>.txt) \
  <(sqlite3 ~/.local/share/regin/regin.db \
     "SELECT span_id FROM session_spans WHERE trace_id='<trace_id>' AND span_id LIKE 'resp-%'" \
   | sed 's/^resp-//' | sort)
```

Any 36-char uuid whose 13-char prefix doesn't appear as a `resp-*` / `think-*` / `att-*` / `cmd-*` / `sys-*` / `prompt-*` / `skill-init-*` span_id is a lockout candidate.

Also rule out the **two-copy-of-regin trap**: the hook script the active provider invokes is a thin shim that POSTs to a regin web server (default `http://127.0.0.1:8321`). When two checkouts of regin coexist (e.g. working directory + worktree), the hook running out of one checkout can post to the web server running from another — they share the same SQLite DB and `turn_trace_state/` dir but execute different `span_posters.py`. Verify the listening server's cwd matches the checkout you're investigating:

```
lsof -i :8321
lsof -p <pid> | grep cwd     # macOS
ls -l /proc/<pid>/cwd        # Linux
```

If the listening server is from the wrong checkout, restart the right one before running repair — otherwise the fix looks successful against the wrong codebase but leaves the DB you're reading untouched.

### Step 5 — Run the repair

Two equivalent entry points. **Prefer the direct-Python path** — it surfaces tracebacks and bypasses the auth interceptor (`web/app.py::_install_auth_gate`, line 233).

**Direct Python (recommended)** — run from the regin checkout root so the venv resolves:

```
cd /Users/taowang/regin
.venv/bin/python -c "
from lib.trace.repair import repair_session_spans
import json
print(json.dumps(repair_session_spans('<trace_id>'), indent=2))
"
```

This still requires the regin web server running on `http://127.0.0.1:8321`, because `lib.trace.repair.repair_session_spans` (`lib/trace/repair.py:264`) re-runs the live emit pipeline and that pipeline posts spans via `lib.hook_plugin.post_span` (`lib/hook_plugin.py:494`) to the public ingest endpoint `trace.api_ingest_session_span` (allowlisted in `web/app.py:199` `PUBLIC_API_ENDPOINTS`).

**HTTP via curl** — the endpoint `api_session_repair_spans` (`web/blueprints/trace/sessions.py:1174`) is not in `PUBLIC_API_ENDPOINTS`, so an Authorization header is required:

```
TOKEN=$(curl -s -X POST http://127.0.0.1:8321/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"<u>","password":"<p>"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8321/api/sessions/<trace_id>/repair-spans
```

### Step 6 — Read the response

```
{
  "ok": true,
  "trace_id": "...",
  "transcript_path": "/Users/.../<trace_id>.jsonl",
  "cached_uuids_before": <N>,
  "cached_uuids_after": <M>,
  "uuids_unlocked": <N - M>,
  "spans_recovered": <K>
}
```

Interpretation:

* `spans_recovered > 0` → the cache-without-span lockout fired and the missing spans are back. Done.
* `uuids_unlocked > 0` with `spans_recovered == 0` → the cache was trimmed, but the re-emit was rejected by the ingest. Return to Step 2 (`ingest-errors.jsonl` / `/api/ingest-errors`) — the failure cause now lives there.
* `uuids_unlocked == 0` and the user still reports missing turns → this isn't the seen-uuid lockout. Investigate the transcript parser (`lib/trace/transcript_usage.py::read_usage`) or the projection grafter (`lib/trace/projection.py::_graft_orphans`, line 331).
* `ok: false` with `error: 'no transcript found for ...'` (HTTP 404 on the curl path) → the JSONL isn't where the active provider expects. Re-check Step 3 against `lib.providers.get_active_provider().transcript_projects_dir()`.

### Step 7 — Verify recovery via SQL

Re-run the span-count query from Step 4 and diff:

```
sqlite3 ~/.local/share/regin/regin.db \
  "SELECT name, count(*) FROM session_spans WHERE trace_id='<trace_id>' GROUP BY name ORDER BY name"
```

The new row counts for `assistant_response`, `assistant.thinking`, `harness.local_command`, `harness.skill_listing`, `harness.task_reminder`, `harness.tools_delta`, and the synthetic `srvtool-*` / `askdeny-*` / `tooldeny-*` / `toolerr-*` / `sys-*` / `prompt-*` spans should account for `spans_recovered`.

`repair_session_spans` heals the rendered projection itself: after the re-emit it calls `lib.trace.trace_service.materialize_session` (best-effort — a materialize failure logs `span_repair_materialize_failed` and does not fail the repair). A manual materialize is only needed when that step failed, or when a session view is open and you want to force a re-render:

```
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8321/api/sessions/<trace_id>/materialize
```

The materialize endpoint is `api_session_materialize` (`web/blueprints/trace/sessions.py:1146`).

### Step 8 — Confirm repair telemetry

`repair_session_spans` writes one `span_repair_completed` line via `lib.activity_log.get_activity_logger('trace_ingest')` (`lib/trace/repair.py:339`), tagged with `uuids_dropped`, `spans_recovered`, and `transcript_path`. Re-emit failures from the same run show up under the same feature (the emit path logs through `trace_ingest`):

```
.venv/bin/python cli/regin.py logs grep span_repair_completed --feature trace_ingest
.venv/bin/python cli/regin.py logs tail --feature trace_ingest -n 200
```

A `span_repair_completed` row whose `spans_recovered` matches the HTTP response is the canonical "repair succeeded" signal.

## Background: why a span can go missing

regin's transcript ingest runs from a hook handler, not a long-lived process. Every `UserPromptSubmit` / `SessionEnd` / `Stop` / `PostToolUse` event fires `hook_manager/handlers/turn_trace/entry.py::handle` (line 42), which dispatches to `_emit_span` (full path, line 52) or `_emit_assistant_response_only` (the lean PostToolUse fast path, line 50). Both ultimately call `_ingest_transcript_usage` (`entry.py:171`), which re-reads the JSONL via `lib.trace.transcript_usage.read_usage` (or `read_usage_resumable`) and emits per-turn / per-attachment / per-system-event / per-local-command spans.

Without throttling, a PostToolUse-heavy turn would replay every prior row on every tool call, so each session keeps a uuid-based cache on disk at `<data_dir>/turn_trace_state/<trace_id>.txt` (`cache.py::_state_dir`, `_load_seen` at line 39, `_mark_seen` at line 48).

The key invariant: **the cache key is the transcript-entry uuid, not the resulting span_id.** For simple cases the emitted `span_id` derives from that uuid — `resp-<uuid[:13]>`, `att-<uuid[:13]>`, `sys-<uuid[:13]>`, `cmd-<uuid[:13]>`. Synthetic tool spans key off the nested `tool_use_id` instead: `srvtool-<tu[:13]>`, `askdeny-<tu[:13]>`, `tooldeny-<tu[:13]>`, `toolerr-<tu[:13]>` (`lib/trace/repair.py::_tool_call_expected_span_id`, line 33; the branch logic mirrors `hook_manager/handlers/turn_trace/deny_detection.py`). One turn-uuid can therefore map to multiple expected span_ids, and a `resp-*` row can be present while a child `toolerr-*` is still absent.

**The lockout shape**: when `post_span` fails transiently (web server down, payload rejected, race during transcript flush) but the uuid lands in the cache anyway, every subsequent hook fire skips it. The span stays absent until the cache entry is trimmed.

The live handler avoids creating *new* losses with a mark-on-success discipline:

* `lib.hook_plugin.post_span` (`lib/hook_plugin.py:494`) returns `bool`, `True` iff the ingest accepted the span; it builds on `post_event` (`lib/hook_plugin.py:369`), which retries transient failures up to `REGIN_INGEST_RETRIES` (default 3) and writes every failed attempt to `ingest-errors.jsonl` with `gave_up` set on the final attempt.
* Each emitter in `hook_manager/handlers/turn_trace/span_posters.py` collects `new_uuids` only after a successful post and then calls `_mark_seen`:
  * `_post_system_event_spans` (line 127, marks at 145).
  * `_post_attachment_spans` (line 306, marks at 340).
  * `_post_local_command_spans` (line 346, marks at 401).
  * `_post_live_turn_data` (line 1174, marks at 1215); the per-turn helper `_maybe_emit_assistant_span` (line 1081) returns the post's bool so its uuid is cached only on success.

New losses are bounded to a single hook fire — the next event retries the uuid. Historical losses (surfaced as `gave_up: true` runs in `ingest-errors.jsonl`, or accumulated from a stale running web server) are what `repair_session_spans` recovers.

## How the repair works

`lib/trace/repair.py::repair_session_spans` (line 264) is idempotent — re-running on a healed session is a no-op because the cache no longer holds any uuid whose expected spans are missing. The algorithm:

1. `_find_transcript(trace_id)` (`repair.py:171`) scans the active provider's `transcript_projects_dir()` for the matching `<trace_id>.jsonl`. Missing → returns `{ok: false, error: 'no transcript found for ...'}` (HTTP 404).
2. `_load_cache` (`repair.py:221`) reads the seen-uuid file; `_existing_span_ids` (`repair.py:205`) snapshots existing span_ids from `session_spans`.
3. `_expected_span_ids_by_uuid` (`repair.py:131`) walks the transcript via `read_usage` and asks `_turn_expected_span_ids` (line 56), `_attachment_expected_span_ids` (line 86), and `_add_local_command_expected` (line 102) what span_ids each cached uuid should map to. Local-command is subtle: a slash command's caveat uuid and stdout uuid never appear in their own span_id, so all three (command / caveat / stdout) fold into the command-uuid's `cmd-*` expected set. Without this fold, repair would unlock the caveat/stdout uuids on every run.
4. Walk the cache; `_should_reemit` (`repair.py:243`) drops a uuid whose expected set is not yet a subset of `existing`, keeps the rest.
5. Rewrite the cache **before** re-emitting (`_save_cache`, `repair.py:231`) — a failed rewrite would risk double-caching.
6. Re-run the live emit pipeline by building a synthetic `HookPayload(event='UserPromptSubmit', session_id=trace_id, transcript_path=...)` and calling `hook_manager.handlers.turn_trace.handle`. Reusing the production path means bug fixes there benefit recovery too — no duplicate emit logic.
7. Call `materialize_session` (best-effort) so the healed parents + envelopes are durably recorded, then diff the span_id set before vs. after and return `{ok, trace_id, transcript_path, cached_uuids_before, cached_uuids_after, uuids_unlocked, spans_recovered}`, logging `span_repair_completed` via `lib.activity_log` (feature `trace_ingest`).

Replay safety relies on the span ingest path using `INSERT INTO session_spans ... ON CONFLICT(trace_id, span_id) DO UPDATE SET ...` (`lib/trace/trace_service/ingest.py:1049`). Deterministic span_ids (`resp-*`, `think-*`, `srvtool-*`, `askdeny-*`, `tooldeny-*`, `toolerr-*`, `sys-*`, `att-*`, `cmd-*`, `prompt-*`, `skill-init-*`, `sttl-*`) guarantee a replayed transcript only upserts.

## Tests

* `tests/trace/test_trace_repair.py::test_repair_unlocks_turn_with_missing_tool_use_error_child` is the end-to-end regression that motivated the synthetic-tool-span branch (a cached assistant turn whose `resp-*` row exists but whose `toolerr-*` child is absent).
* `hook_manager/tests/test_turn_trace.py` covers the mark-on-success behavior for the emitter categories in `span_posters.py`.

## Related surfaces

* Transcript parser the whole pipeline reads from: `lib.trace.transcript_usage.read_usage`; span vocabulary in `docs/trace/SPAN_DESIGN.md`.
* The projection that turns raw spans into the rendered tree: `lib/trace/projection.py::_graft_orphans` (line 331) and `_graft_orphans_under_prompt` (line 159), which anchor orphan `assistant_response` spans under the nearest prior `prompt` span — recovered responses may show as orphans until the corresponding prompt uuid is also unlocked — plus `_widen_envelopes` (line 374).
* The provider abstraction for the active transcript directory: `lib.providers.get_active_provider().transcript_projects_dir()` — Claude at `lib/providers/claude/__init__.py:184`, Codex at `lib/providers/codex/__init__.py:138`, generic at `lib/providers/generic/__init__.py:72`.
* The auth interceptor that gates the HTTP repair endpoint: `web/app.py::_install_auth_gate` (line 233), with the `PUBLIC_API_ENDPOINTS` allowlist (line 199).
* The ingest-error observability surfaces: file `<provider.traces_dir()>/ingest-errors.jsonl` + HTTP `/api/ingest-errors` (`web/blueprints/trace/ingest_errors.py:41`).
* The activity-log viewer: `cli/commands/logs.py` exposes `regin logs {list,tail,grep,prune,path} --feature <name>`. The two features that matter here are `hooks` (dispatcher) and `trace_ingest` (repair + emit telemetry).