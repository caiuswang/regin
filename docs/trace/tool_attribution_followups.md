# Per-tool token attribution — known gaps

The feature attributes per-tool token cost in `session_spans` (columns
`input_tokens`, `output_tokens`, `image_tokens`, `cost_usd`, plus
`tool_use_id`/`turn_uuid` for joins). It ships working for native and
MCP tools but has eight known limitations worth tracking. Listed by
likelihood of biting first.

## 1. The "untagged" remainder math

Originally: `attributed` and `session.output_tokens` were in different
units (estimated content tokens vs billed totals), and the tool_use
estimate's per-block framing undershoot showed up as a session-wide
untagged remainder.

`lib/trace/transcript_usage.py` now redistributes the per-turn output
residual proportionally across non-server tool_use estimates so the
per-turn block sum equals the API-billed `output_tokens` exactly. See
[`USAGE_ATTRIBUTION.md`](./USAGE_ATTRIBUTION.md#per-turn-output-residual-redistribution)
for the algorithm and the categories where it does/doesn't apply.

Remaining gaps (live as their own items below): server-side tool input
double-counting (item 7), text-only-turn tokenizer drift, cost-rate
cache awareness (item 4).

## 2. Pre-PR session_spans can't be backfilled

Per-tool attribution keys off `tool_use_id`, which `post_tool_trace.py`
only began stashing in span attributes partway through. Spans created
before that have nothing to match transcript `tool_use` blocks against,
so they carry no per-tool token estimates.

There's no reliable join key for these old spans. Fuzzy matching by
`(trace_id, tool_name)` nearest-start-time risks cross-linking the
wrong span when a tool fires several times in close succession (e.g. a
flurry of `Read`s), so the result would be silently wrong. The lost
coverage is accepted rather than guessed at.

## 3. Cross-session tool-usage dashboard

Per the design plan, the per-session view ships first. The natural
follow-up is `/trace/tool-usage` answering "across the last N days,
which tools cost most?" — a sortable table grouped by tool name with
date-range and MCP-only filters. Implementation pattern: mirror
`/api/mcp-calls` in `web/blueprints/trace/mcp_calls.py` for the query, copy
`MCPCallsView.vue` for the surface.

## 4. Cost ignores cache hits

`cost_usd` is computed as `(input_tokens × rate.input + output_tokens
× rate.output) / 1M` using the model's *uncached* input rate. But most
tool_results end up in the cache prefix of the next turn and bill at
the `cache_read` rate (~10× cheaper on Opus). The overcharge is
material on long sessions where the same files are re-read.

Fix: at attribution time, look at `turn[N+1].cache_read_input_tokens`
vs the prior cumulative content size to detect whether this turn's
result was cached, and apply `rate.cache_read` instead. Heuristic, but
much closer to billing reality than the current uniform rate.

## 5. `reasoning_tokens` column exists but isn't populated

`db/schema.sql` defines `turn_usage.reasoning_tokens` (intent: separate
extended-thinking output from regular `output_tokens`), but
`lib/trace/transcript_usage.py` doesn't extract it. Two possible sources:

- `message.usage.reasoning_output_tokens` if Anthropic emits it (check
  recent transcripts — field name and presence depends on model).
- Tokenize each `type: thinking` content block separately and roll up.

Roll up to `sessions.reasoning_tokens` and surface in the UI so users
can see thinking cost, not just tool cost.

## 6. Image-token attribution

`lib/tokens/token_estimator.estimate_image_tokens` reads PNG/JPEG
dimensions from base64 headers and applies Anthropic's `(w × h) / 750`
formula capped at 1600. Two ingest paths use it:

- **Tool-result images** — `tool_result.content` blocks of `type:
  image` populate `session_spans.image_tokens` on the matching tool
  span (browser screenshots, image-generation MCPs, etc.).
- **User-prompt images** — `hook_manager/handlers/prompt_trace.py`
  estimates each attached image and stores the sum as
  `image_tokens_estimate` on the prompt span.

Open: validate the local approximation against an API `count_tokens`
call for one example, especially for non-PNG/JPEG formats where the
header reader falls back to the 1600 cap.

## 7. `server_tool_use` is unattributed

`message.usage.server_tool_use.{web_search_requests,
web_fetch_requests}` counts Anthropic-server-side tools. These are
billed *per request*, not per token, so they're a different
attribution path than client-side tools.

Fix: add per-request counters on `turn_usage`, multiply by the
documented per-request price, surface as a separate row in the rollup
("web_search · 14 requests · $0.14"). Pricing per request lives in
Anthropic's docs, not models.dev — would need to hardcode or extend
`lib/tokens/pricing.py` to handle the request-pricing schema.

## 8. Tokenizer drift not measured

`lib/tokens/token_estimator.estimate_text_tokens` uses tiktoken's
`cl100k_base` — the GPT-4 encoder family, close to Claude but not
identical. Likely 3–5% off on prose and JSON. Quantify before
optimising:

- Pick a sample of recent turns where attribution covers every tool.
- Compare `SUM(span.output_tokens for tool spans) +
  tokenize(non-tool assistant content)` against the turn's API-reported
  `usage.output_tokens`.
- If drift is small and unbiased: leave it. If it's biased by tool
  type (e.g. always over-counts JSON-heavy tools): swap to a real
  Claude tokenizer. Single switch-point at the top of
  `lib/tokens/token_estimator.py`.
