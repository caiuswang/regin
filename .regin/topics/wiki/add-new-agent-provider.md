# Adding a new agent provider

This topic is the playbook for teaching regin about a **new coding-agent CLI** — the way the codebase supports Moonshot's Kimi Code and OpenAI's Codex alongside Claude. The whole integration funnels through one seam: the `AgentProvider` adapter in `lib/providers/`. Capture, hook install, transcript parsing, and skill deployment resolve every vendor-specific path and payload shape through that adapter, so the shared pipeline carries no per-vendor `isinstance` chains or hard-coded paths.

## The contract (`lib/providers/base.py`)

`AgentProvider` is the base adapter. A new provider subclasses it and fills in:

- **Identity + capabilities** — `provider_id`, `display_name`, and a frozen `ProviderCapabilities(skills, hooks, sessions, transcript_usage)` matrix surfaced to the CLI/UI.
- **Format flags** that let the shared pipeline branch without inspecting the concrete class: `hook_config_format` (`json` vs Kimi's `toml`), `hook_output_format` (`claude`'s full envelope vs Kimi's tiny `permissionDecision`-only surface), `transcript_format`, and `synthesizes_session_end_from_stop` (true for Codex, whose CLI never emits `SessionEnd`).
- **Path methods** — `global_skills_dir`, `project_skills_subpath`, `plans_dir`, `traces_dir`, `hook_settings_path`, `hook_manager_config_path`, `hook_payload_log_path`, `transcript_projects_dir`, and the synthetic skill-trace paths (`skill_invoke_path` / `skill_launch_path` / `skill_content_relpath` / `skill_id_from_read_path`).
- **Payload-shape hooks** — `resolve_transcript_path`, `parse_transcript`, `normalize_tool_response`, `tool_failure_error_text`, `tool_failure_is_user_rejection`, plus the permission-request trio (`permission_request_events` / `build_permission_request_info` / `serialize_permission_decision`). The base-class defaults assume Claude's payload shape, so a Claude-compatible agent overrides almost nothing.

## Registration (`lib/providers/registry.py`)

`_PROVIDER_BUILDERS` maps each `provider_id` to its class (`claude` → `ClaudeProvider`, `codex` → `CodexProvider`, `kimi` → `KimiProvider`, `generic` → `GenericProvider`); adding an entry is the one edit that registers a provider. The registry resolves the **active** provider (`active_provider_id` / `get_active_provider`), the **enabled** set (`enabled_provider_ids`, which always includes the active provider plus any whose settings entry has `enabled: true`), and — the load-bearing one — the **per-payload** provider via `resolve_provider(payload)`. Its resolution order is: explicit `agent_type` / `provider_id` tag (`_provider_id_from_tag`) → model sniff (`provider_id_from_model`, e.g. `claude-*` → claude, contains `kimi` → kimi, `gpt-`/`o1`… → codex) → `settings.active_provider` → `generic` fallback on any exception. `canonical_agent_kind` centralizes the stored-`sessions.agent_type` → kind mapping so UI surfaces don't re-implement substring chains. `HookPayload.resolved_provider` (a `cached_property` in `hook_manager/core.py`) calls straight into `resolve_provider`, which is how a single hook process serves a mixed fleet.

## How the shared pipeline consumes it

These are the real call sites a new provider must satisfy, each resolved off `payload.resolved_provider` so no handler names a vendor:

- `hook_manager/runner.py` reads `resolved_provider.hook_output_format` before printing a hook response, so a Kimi session never gets Claude-only JSON (`suppressOutput`, `systemMessage`) printed raw into its UI.
- `hook_manager/handlers/turn_trace/entry.py` calls `provider.resolve_transcript_path(payload)` then `provider.parse_transcript(...)`, and gates the Claude-only enrichment (session-title span, tail-read of the latest model) on `transcript_format == 'claude'`.
- `hook_manager/handlers/post_tool_trace.py` calls `resolved_provider.normalize_tool_response(...)`, which maps Kimi's single `{output, isError}` envelope onto the `stdout` / `file.content` keys the per-tool span builders read.
- `hook_manager/handlers/post_tool_failure.py` calls `tool_failure_is_user_rejection` (to stay silent on a rejected permission prompt that the transcript already captures as a deny span) and `tool_failure_error_text` (to pull a display string out of Kimi's structured `{code, message, retryable}` error).
- `hook_manager/handlers/session_lifecycle.py` reads `synthesizes_session_end_from_stop` (via `build_provider`) to decide whether a per-turn `Stop` should synthesize a session-end marker — true only for Codex.
- `hook_manager/core.py`'s `_apply_tool_field_aliases` (run inside `_normalize_payload`) fills canonical `tool_response` / `tool_use_id` from Kimi's `tool_output` / `tool_call_id`, and `build_permission_request_info` is invoked on the resolved provider when the payload is built, so the rest of regin stays vendor-agnostic.

## Transcript parser (`lib/trace/kimi_transcript.py`)

If the new CLI's session file isn't Claude's message-per-line JSONL, write a reader that returns the **same** `TranscriptUsage` / `TurnUsage` dataclasses from `lib/trace/transcript_models.py`, and call it from the provider's `parse_transcript`. Kimi's `read_usage_kimi` is the worked example: it scans an event-sourced `wire.jsonl` (`turn.prompt`, `context.append_loop_event` steps keyed by `step.begin` / `content.part` / `tool.call` / `tool.result` / `step.end`, `usage.record`, `permission.record_approval_result`) and collapses each step into a per-turn `TurnUsage` — including denied-call deny spans — so every downstream span/usage poster works unchanged.

## Hook install (`lib/providers/kimi_hooks.py` + `web/blueprints/hooks.py`)

Claude and Codex store hooks in a `settings.json` map; Kimi reads them from a `config.toml` `[[hooks]]` array. `kimi_hooks.py` manages a delimited, byte-exact-reversible managed block (labelled `>>> regin hook_manager` markers), preserving the user's hand-written config and reading installed state back with stdlib `tomllib`. The hooks blueprint branches on `hook_config_format == 'toml'` (`_is_toml_provider`) to pick the reader/writer, and on `hook_output_format` for the response shape. GitNexus's `Api_hook_manager_install → _provider` flow (and its siblings `Api_hooks_status` / `Api_toggle_handler` / `Api_reorder_handlers`) confirms every hooks-blueprint route resolves the provider through `web/blueprints/hooks.py:_provider` before installing or reading status.

## Settings, API, UI, schema

- **Settings** (`lib/settings.py`): the `active_provider` literal (`claude` | `codex` | `generic` | `kimi`), the `providers: dict[str, ProviderConfig]` map (path overrides + `enabled` / handler tuning), and the `experimental_providers` gate that controls which providers the UI exposes.
- **API**: `/api/providers` (`web/blueprints/meta.py`, built from `provider_capability_rows` / `active_provider_skill_paths`), and provider-aware skill deploy in `web/blueprints/skills.py` (which fans out across `enabled_provider_skill_paths`).
- **UI**: `frontend/src/components/SettingsProviders.vue` plus the `useProviderPaths.js` composable render per-provider deploy labels (e.g. `~/.kimi-code/skills`).
- **Schema**: `alembic/versions/0004_pattern_deployment_provider.py` adds the nullable `provider` column to `pattern_deployments` and folds it into the unique constraint `(pattern_slug, scope, project_id, provider)`. The same column and constraint live in `db/schema.sql` — keep both in sync, since `regin init` builds the DB from the SQL file, not Alembic.

## Checklist for a new agent

1. Subclass `AgentProvider`; set id/display/capabilities + format flags; override only the path/payload methods that differ from Claude's defaults.
2. Register the class in `_PROVIDER_BUILDERS`; teach `provider_id_from_model` / `canonical_agent_kind` the vendor's model/agent-type strings.
3. If the transcript shape differs, add a reader returning the shared `TranscriptUsage` / `TurnUsage` dataclasses and wire `parse_transcript` / `resolve_transcript_path`.
4. If hook storage differs, add an install module and branch the hooks blueprint on `hook_config_format`; set `hook_output_format` so the runner emits the agent's response surface.
5. Add a `ProviderConfig` shape + UI exposure; gate behind `experimental_providers` until parity lands.
6. Prove parity with the provider/skill/hooks/transcript suite: `tests/core/test_providers.py`, `tests/skills/test_provider_parity.py`, `tests/hooks/test_kimi_hooks_toml.py`, `tests/trace/test_kimi_transcript.py`.

Reference implementations: **Claude** (`lib/providers/claude/`) is the baseline the contract defaults model; **Kimi** (`lib/providers/kimi/`) is the fully featured worked example (TOML hooks, custom transcript, tool-response normalization); **Codex** (`lib/providers/codex/`) is contract-complete with `synthesizes_session_end_from_stop`; **Generic** (`lib/providers/generic/`) is the safe fallback `resolve_provider` returns on any failure.