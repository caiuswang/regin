# PostToolUse payload schemas

One JSON Schema per known tool. Validated at runtime by
`lib/trace/payload_validation.py`; drift is recorded in
`payload_schema_drift` and reviewed in the WebUI.

## Convention

- File name: `<ToolName>.schema.json`. Special case: `_mcp_wildcard.schema.json` matches every `mcp__*` tool.
- Top-level required keys: `tool_name` (const), `tool_input`, `tool_response`.
- `additionalProperties: true` at every level. Unknown-key drift is reported by the validator's recursive walker, not by jsonschema itself.
- Property names use snake_case. The validator dedupes camelCase aliases via `_to_snake` so Codex-sourced payloads don't spam drift findings.

## Version tracking

Each schema carries an `x-claude-versions: []` array listing the Claude Code CLI versions it is known to validate cleanly against.

- The bootstrap CLI populates this with the version it detected at generation time.
- Ratifying a drift finding in the WebUI appends the version that was active when the finding was observed (deduped).
- Empty array = unknown; treat as "applies to all versions we've seen so far".

This lets one schema cover several Claude versions, and lets reviewers tell server-side payload changes (same client version, different `tool_response`) apart from client upgrades (new `tool_input` field after a Claude CLI bump).
