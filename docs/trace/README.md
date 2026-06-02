# docs/trace/ — session trace & span docs

Unified home for documentation about how regin records, stores, and
displays Claude Code session timelines. Read these when debugging a
bad span, a missing turn, or an unexpected duration.

| File | Scope |
|---|---|
| [`SPAN_DESIGN.md`](./SPAN_DESIGN.md) | Span data model, emission pipeline, projection (`_graft_orphans`, `_widen_envelopes`, `_build_span_tree`), invariants, subagent 3-level nesting, and a debugging cookbook. Start here. |
| [`CAPTURE_MODELS.md`](./CAPTURE_MODELS.md) | Why session traces (two racing sources → read-time merge) and workflow runs (one authoritative source → rebuild on poll) capture into the same tables through deliberately different mechanisms, and the decision that workflow capture does not need the session-trace refactor. |
| [`TURN_USAGE.md`](./TURN_USAGE.md) | Per-API-call token usage — the `turn_usage` table, `context_used`, `ctx %` badge, model-window inference. A sibling concern to spans: same transcript source, different destination table. |
| [`USAGE_ATTRIBUTION.md`](./USAGE_ATTRIBUTION.md) | How a turn's billed tokens are split across spans (assistant text, thinking, individual tools, prompt images), per-turn residual redistribution, and the rollup math behind the "Tokens by tool" chips. |
| [`tool_attribution_followups.md`](./tool_attribution_followups.md) | Known limitations of the per-tool attribution feature with proposed fixes. |
| [`INCREMENTAL_RESCAN.md`](./INCREMENTAL_RESCAN.md) | Why the server-side live rescan parses only appended bytes (resumable scan from a committed offset) instead of re-reading the whole transcript each poll — the design and the invariants (equivalence, pure finalize) the implementation must keep holding. |

Not here:

- `hook_manager/README.md` — hook dispatch architecture (upstream of
  span emission); see `registry.py` for the live handler list
- `tests/trace/integration/README.md` — tmux-driven integration tests
  (exercise the full pipeline against a live `claude` CLI)
- `ARCHITECTURE.md` at the repo root — top-level system map
