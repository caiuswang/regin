# Adding a new agent provider

regin captures sessions from more than one coding-agent CLI, but the capture pipeline — hook router, span builders, transcript ingest, skill deployer, usage rollup — is written against one canonical payload shape and one set of canonical paths. The **`AgentProvider` adapter** is the seam that lets a new CLI (Kimi-, Codex-, or any vendor-style) flow through that shared pipeline without scattering `if agent == "..."` branches across the codebase. Every per-vendor difference — where files live on disk, how a transcript is encoded, how a tool result is shaped, how hooks are stored — is expressed as an override on a single adapter class.

This topic is the playbook for writing that adapter and wiring it in.

## The contract — `lib/providers/base.py`

`AgentProvider` is the base class every provider subclasses. It carries two kinds of surface:

**Class-level identity & format flags** (read by shared code to branch behavior without an `isinstance` chain):
- `provider_id` / `display_name` — registry key and UI label.
- `capabilities: ProviderCapabilities` — a frozen `{skills, hooks, sessions, transcript_usage}` matrix surfaced to CLI/UI callers.
- `hook_config_format` (`"json"` | `"toml"`) — whether hooks are stored in a `settings.json` `hooks` map (Claude/Codex) or a `config.toml` `[[hooks]]` array (Kimi). The hooks blueprint branches on this to pick the reader/writer.
- `hook_output_format` (`"claude"` | `"kimi"`) — the wire format of a hook handler's stdout response. Claude speaks the full hook-output JSON envelope (`suppressOutput`, `systemMessage`, `hookSpecificOutput`); Kimi parses only `hookSpecificOutput.permissionDecision` and renders any other stdout verbatim, so the runner must emit Kimi's smaller shape (or nothing) for those sessions.
- `transcript_format` (`"claude"` | `"kimi"`) — on-disk transcript schema tag; `turn_trace` uses it to gate Claude-shaped enrichment that has no analogue elsewhere.
- `synthesizes_session_end_from_stop` — `True` only for CLIs that never emit `SessionEnd` in real runs (Codex), so the per-turn `Stop` handler synthesizes the session-end marker.

**Overridable methods** (default to the Claude behavior; a provider overrides only what differs):
- `hook_events()` — the supported hook event names to install (`None` = use the full spec registry).
- `resolve_transcript_path(payload)` — locate this session's transcript file. Default reads `transcript_path` off the payload (Claude/Codex); Kimi overrides to glob it from the session id.
- `parse_transcript(path, ...)` — parse a transcript into a `TranscriptUsage`. Default reads the Claude/Codex message-per-line JSONL via `lib.trace.transcript_usage.read_usage`; Kimi overrides to `lib.trace.kimi_transcript.read_usage_kimi`. Every parser returns the same dataclass so the span/usage posters stay format-agnostic.
- Permission shaping: `permission_request_events()`, `build_permission_request_info(payload)`, `serialize_permission_decision(info, ...)`, `permission_awaits_human(payload)` — normalize a provider's permission prompt into regin's `PermissionRequestInfo`/`HookResponse`, and decide whether a request genuinely blocks on a *human* (so the inbox/push channels don't get spammed by auto-resolved prompts).
- Payload reshaping: `tool_failure_error_text(raw_error)`, `normalize_tool_response(tool_name, tool_input, tool_response)`, `tool_failure_is_user_rejection(raw_error)` — pull the per-vendor result/error shape onto the Claude-shaped keys the shared span builders read.
- `client_version()` — installed CLI version for payload-schema-drift fingerprinting (`None` until a provider wires a probe).
- `reconcile_subagents(session_id)` — re-nest a subagent's flat tool/turn spans under its subagent trace; a no-op unless the CLI fires sub-tool hooks under the parent session id.
- Path resolvers: `global_skills_dir()`, `project_skills_subpath()`, `skill_invoke_path()`, `skill_launch_path()`, `skill_content_relpath()`, `skill_id_from_read_path()`, `plans_dir()`, `traces_dir()`, `hook_settings_path()`, `hook_manager_config_path()`, `hook_payload_log_path()`, `transcript_projects_dir()` — every filesystem location the pipeline touches, resolved through the adapter instead of hard-coded.

## The registry — `lib/providers/registry.py`

The registry maps `provider_id → builder` in `_PROVIDER_BUILDERS` (`claude`, `codex`, `generic`, `kimi`) and exposes the resolution helpers the rest of regin calls:
- `build_provider(id)` / `get_active_provider()` / `active_provider_id()` — construct an adapter, applying per-provider path overrides pulled from settings.
- `resolve_provider(payload)` — the per-event resolver, in order: an explicit `agent_type`/`provider_id` tag → a `model` sniff (`provider_id_from_model`: `claude-*` → claude, contains `kimi` → kimi, `gpt-`/`o1`–`o5` → codex) → `settings.active_provider` → `generic` on any failure.
- `canonical_agent_kind(agent_type)` — map a stored free-form `sessions.agent_type` to a canonical provider id for UI grouping, reusing the same vendor-prefix table so it can't drift.
- `list_visible_provider_ids()` / `enabled_provider_ids()` / `get_enabled_providers()` — visibility and multi-provider participation, honoring `experimental_providers` and per-provider `enabled`.
- `provider_capability_rows()` / `provider_skill_paths()` — UI rows for the providers/skills surfaces. Everything in `lib/providers/__init__.py`'s `__all__` is the public face.

## Configuration — `lib/settings.py`

Provider config is two pydantic models. `ProviderPathOverrides` holds the path-only fields (`skills_dir`, `plans_dir`, `traces_dir`, `hook_settings_path`, `hook_manager_config_path`, `hook_payload_log_path`, `transcript_projects_dir`) that redirect integration points without touching provider code. `ProviderConfig` extends it with behavioral fields: `enabled: bool`, `disabled_handlers: list[str]`, and `priority_overrides: dict[str, int]` (mirroring the per-provider `hook-manager-config.json` shape). On the settings singleton: `active_provider` (literal `claude`|`codex`|`generic`|`kimi`), `providers: dict[str, ProviderConfig]`, and `experimental_providers: bool` (when false, only `claude` plus the active provider are exposed to UI surfaces). The registry's `_provider_overrides` selects exactly the `ProviderPathOverrides.model_fields` so a new behavioral field can't leak into a provider constructor.

## Payload normalization — `hook_manager/core.py`

Incoming hook payloads are normalized before any handler sees them, so a provider whose CLI names fields differently needs no handler changes. `core.py` aliases `tool_output → tool_response` and `tool_call_id → tool_use_id` (Kimi's names) only when the canonical keys are absent (Claude/Codex payloads pass through untouched), and deep-normalizes `tool_input`/`tool_response`. The `HookPayload`, `PermissionRequestInfo`, and `HookResponse` dataclasses are the shared currency; `SPEC_EVENTS` is the full event-name registry the router accepts.

## Response & lifecycle wiring

`hook_manager/runner.py` reads `resolved_provider.hook_output_format` and, for `"kimi"`, emits Kimi's `permissionDecision`/block-reason shape via `kimi_response_text`/`kimi_block_reason` instead of Claude's JSON envelope. The trace handlers (`turn_trace/entry.py`, `post_tool_trace.py`, `session_lifecycle.py`, `post_tool_failure.py`) route every per-vendor decision back through the adapter — `normalize_tool_response`, `tool_failure_error_text`, `tool_failure_is_user_rejection`, `synthesizes_session_end_from_stop`, `reconcile_subagents` — so the handlers themselves stay provider-agnostic.

## Hook install storage — `lib/providers/kimi_hooks.py`

Claude/Codex install hooks into a JSON `settings.json` map; Kimi reads them from a TOML `[[hooks]]` array in `~/.kimi-code/config.toml`. Rather than depend on a TOML writer, `kimi_hooks.py` manages a clearly delimited, labelled managed block (`# >>> regin <label> ... >>>`) appended to the file, leaving the user's hand-written config byte-for-byte intact and making uninstall an exact reversible operation; state is read back with stdlib `tomllib`. `web/blueprints/hooks.py` branches on `hook_config_format` (`_is_toml_provider`) to choose `kimi_hooks` vs the JSON installer, and asks the provider for its `hook_events()`.

## Web & frontend surfaces

`web/blueprints/hooks.py` (install/uninstall + per-handler toggles), `web/blueprints/meta.py` (provider capability rows), and `web/blueprints/skills.py` (skill-path metadata) expose provider state to the SPA. `frontend/src/components/SettingsProviders.vue` and `frontend/src/composables/useProviderPaths.js` render the per-provider deploy paths and capability matrix without hard-coding Claude paths.

## Persistence

`alembic/versions/0004_pattern_deployment_provider.py` adds the provider column for pattern/skill deployment; the same shape lives in `db/schema.sql` (remember the schema-drift gotcha: `regin init` builds from `db/schema.sql`, not Alembic, so any migration must be folded into both).

## Where to start a new provider

1. Subclass `AgentProvider` in `lib/providers/<id>/__init__.py`; set identity, `capabilities`, and the format flags; override the path resolvers and only the methods that differ from Claude.
2. Register the builder in `lib/providers/registry.py` `_PROVIDER_BUILDERS` and add the id to the `active_provider` literal in `lib/settings.py`.
3. If the CLI uses different payload field names, add aliases in `hook_manager/core.py`; if it stores hooks in TOML, set `hook_config_format = "toml"` and reuse `kimi_hooks`.
4. If its transcript is a different on-disk format, write a parser that returns `TranscriptUsage` and override `parse_transcript`/`resolve_transcript_path`.
5. Verify with the parity/contract tests:

```bash
.venv/bin/python -m pytest tests/core/test_providers.py tests/skills/test_provider_parity.py tests/hooks/test_kimi_hooks_toml.py tests/trace/test_kimi_transcript.py
```

Kimi is the most complete non-Claude example (full lifecycle, TOML hooks, custom transcript, result/error reshaping, subagent reconcile); Codex is a narrower contract-complete stub; Generic is the no-capability fallback `resolve_provider` returns on failure.