# Trace span capture (builders, payload schemas, new system subtypes, provider quirks)

Every visible row in the session-trace UI is a *span*. This topic is the **write side**: how a live Claude Code (or Kimi) session's hook payloads and transcript entries become spans. The read side — reconciling, reparenting, and aggregating those spans — is [[trace-merge-reconcile]] and [[session-trace-design]].

## Where spans come from

Two producers feed the same append-only `session_spans` store through `lib/hook_plugin.post_span`:

1. **Live hook handlers** — one per Claude Code hook event, under `hook_manager/handlers/`:
   - `pre_tool_trace.py` (PreToolUse) emits a PENDING `tool.<Name>` span so an in-flight tool is visible before it returns. Scoped to slow/blocking tools (`Bash`, `Agent`, web tools, any `mcp__*`, `AskUserQuestion`, `ExitPlanMode`); instant tools are skipped because a pending card would only flicker and double ingest volume. The serve-time merge retires the pending twin by `tool_use_id` once the resolved span arrives.
   - `post_tool_trace.py` (PostToolUse) emits the resolved `tool.<Name>` span — the heart of the capture layer.
   - `prompt_trace.py` (UserPromptSubmit) emits the `prompt` span that opens a turn, plus `task.notification`.
   - `plan_trace.py` emits `plan.exit`, `plan.write`, and `plan.update` spans tying a session to a plan file.
   - `trace_payload.py` is the diagnostics tee: it appends every raw payload to a size-capped JSONL and runs the validate/drift pipeline. Gated by `settings.diagnostics_enabled` (fail-closed, default OFF), so it imposes no cost on users who opt out.

2. **Transcript re-scan** (`hook_manager/handlers/turn_trace/`), driven off Stop/turn boundaries, recovers spans hooks never see by reading the JSONL transcript: assistant `response`/`thinking` spans, `queued_command` prompts (typed while the agent was mid-turn, so UserPromptSubmit never fired), `rewind`, and the `harness.*` / `hook.stop_summary` system events. `span_posters.py` holds the emitters, `entry.py` orchestrates the scan, `deny_detection.py` recovers denied tools.

## The tool-attr builders

`post_tool_trace._TOOL_BUILDERS` is a `{ToolName: builder}` registry. Each builder reads `tool_input` + `tool_response` and writes a small set of **flat, whitelisted** attrs onto the span (`_build_bash_attrs`, `_build_agent_attrs`, `_build_edit_attrs`, …) — not a raw payload dump, because the conversation labellers read those flat keys, not the nested input. Unregistered `mcp__*` tools fall through to `_build_mcp_attrs`, which keeps a truncated input/result round-trip. **To make a new tool render richly:** write a builder and register it here. `_INPUT_ONLY_BUILDERS` is a parallel set deriving attrs from `tool_input` alone, so a PENDING span can carry the same keys the resolved card will show (`apply_pending_input_attrs`).

New `harness.*` system subtypes are added the same way, one layer up: register an emitter in `span_posters._SYSTEM_EVENT_EMITTERS` (or add an attachment emitter like `_post_skill_listing_span` / `_post_tools_delta_span`) keyed on the transcript subtype.

## Invariants & gotchas

- **Idempotent span_ids.** Pending twins use `tool_pending_id(tool_use_id)`; system events use `sys-<uuid[:13]>` / `att-<uuid[:13]>`; the initial skill listing collapses onto one stable `skill-init-<trace>` row. Re-scanning a transcript must never duplicate a span.
- **`tool_use_id`** (the `toolu_…` id) is persisted on every tool span so the token-cost backfill can match the span to the transcript's tool_use block; without it, billing can't attribute the call.
- **`source_prompt_id`, not `prompt_id`.** Recent Claude Code stamps a per-submission `prompt_id` on every payload; it is stored as `source_prompt_id` because `merge.py` already uses `prompt_id` for an unrelated internal ordinal.
- **Subagent tagging.** A tool call inside a subagent carries `agent_id` (+ optional `agent_type`); both are persisted so the projection re-parents the span under its `subagent.start` instead of the prompt. Workflow-tool subagents are skipped wholesale (`is_workflow_subagent`) — their activity belongs to their own `wf_` session, not the launching conversation.

## Payload schemas & drift

`lib/trace/payload_schemas/{claude,kimi}/` holds one JSON Schema per tool (`<Tool>.schema.json`), per hook event (`_hooks/`), plus `_mcp_wildcard.schema.json` for any `mcp__*`. `payload_validation.py` validates live payloads against a baseline schema (optionally merged with a user overlay) and its recursive walker reports **unknown keys** — camelCase deduped to snake_case via `_to_snake`, so Codex/Kimi payloads don't spam drift. Findings persist through `payload_drift_store.py` into `payload_schema_drift` for WebUI review; each schema tracks an `x-claude-versions` array so a reviewer can tell a server-side payload change apart from a CLI upgrade. Schemas are data the validator consults, not code — see `payload_schemas/README.md`.

## Provider quirks

Non-Claude payloads are normalized before the builders run: `post_tool_trace._emit_span` calls `resolved_provider.normalize_tool_response` (Kimi wraps every result in `{output, isError}`; Claude/Codex pass through unchanged). The transcript primitives in `transcript_parsers.py` are likewise schema-tolerant — `_usage_tokens` and `_normalize_dict_keys` accept both Anthropic (`input_tokens`) and OpenAI-style (`prompt_tokens`) keys, and `_extract_text_blocks` / `_scan_thinking_blocks` split visible text from redacted extended-thinking. Standing up a whole new provider is [[add-new-agent-provider]]; this topic is the span-shaping seam that adapter plugs into.