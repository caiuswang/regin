# Usage Attribution

How regin distributes a turn's API-billed tokens across spans (assistant
text, thinking, individual tool calls, prompt images) so the per-session
rollup answers "where did my tokens go?" — not just "how many did I
spend?".

If you're looking for the raw per-turn `usage.*` numbers (what Anthropic
billed), read [`TURN_USAGE.md`](./TURN_USAGE.md) first; this doc starts
where that one stops, at the attribution layer.

## What attribution buys you

The Anthropic API reports **one** `usage` block per assistant turn —
billed totals for input, output, cache_read, and cache_creation. It does
not say "tool X cost N output tokens" or "the screenshot you attached
cost M input tokens." Attribution synthesises those breakdowns locally
so the WebUI can render:

- The "Tokens by tool" chip rollup in the session-trace view.
- The `~N tok` annotation next to each user-prompt image.
- Per-tool input/output/image columns on tool spans.

When attribution is good, the rollup chips sum to the session totals.
When it's bad, the difference shows up as `untagged_input_tokens` /
`untagged_output_tokens` (see "Known divergences" below).

## Pipeline

| Step | Where | Output |
|---|---|---|
| Parse transcript JSONL | [`lib/trace/transcript_usage.py`](../../lib/trace/transcript_usage.py) | `TurnUsage` per API call (token counts, text/thinking blocks, `tool_calls[]` with per-block estimates) |
| Redistribute per-turn output residual | same file, at finalize | `tool_calls[*].output_token_estimate` adjusted so non-server tool estimates absorb the residual |
| Emit spans + tool_attribution event | [`hook_manager/handlers/turn_trace/`](../../hook_manager/handlers/turn_trace/) | `assistant_response` / `assistant.thinking` spans + per-tool input/output token columns |
| Estimate prompt-image cost | [`hook_manager/handlers/prompt_trace.py`](../../hook_manager/handlers/prompt_trace.py) | `image_tokens_estimate` attribute on the prompt span |
| Roll up per-session | [`lib/trace/trace_service/queries.py`](../../lib/trace/trace_service/queries.py) (`fetch_tool_token_rollup`) | One row per tool name + `attributed_/untagged_` totals |

Token estimators live in [`lib/tokens/token_estimator.py`](../../lib/tokens/token_estimator.py):
`estimate_text_tokens` (cl100k_base for prose/JSON),
`estimate_tool_use_tokens` (name + JSON-encoded input), and
`estimate_image_tokens` (`(w × h) / 750`, capped at 1600 per image).

## The per-block model

For each turn, the API-billed `output_tokens` covers everything the
model emitted:

```
API.output_tokens = text_block_cost
                  + thinking_block_cost
                  + Σ tool_use_block_cost
                  + per-block framing overhead
```

regin estimates the first three locally and treats the framing overhead
as residual. The estimators have known accuracy:

- **Text**: cl100k_base on the joined text blocks. ~3–5% off Claude's
  real tokenizer but unbiased on prose.
- **Thinking** (visible): same encoder on the joined thinking text.
- **Thinking** (redacted): not estimable locally — only the signature
  and block count are preserved in the transcript. `turn_trace`'s
  fallback computes `max(0, API.output − Σ tool_use_est)` and assigns
  it to the `assistant.thinking` span, so the residual lands there.
- **Tool_use blocks**: `estimate_tool_use_tokens(name, json.dumps(input))`.
  Systematically *undershoots* because it ignores per-block framing
  (block delimiters, `tool_use_id`, role markers). Empirically ~20–30%
  under the API-billed share on tool-heavy turns.

## Per-turn output residual redistribution

To keep the tool_use undershoot from surfacing as a session-wide
"untagged" remainder, the transcript parser computes:

```
residual = API.output - text_est - thinking_est - Σ main_tool_use_est
```

When `residual > 0`, each non-server tool_use estimate is scaled
proportionally so the per-turn sum hits the API output exactly. Rounding
crumbs land on the last tool_use to keep the sum exact.

**Skipped when:** the turn has no text, no visible thinking_text, AND
`thinking_blocks > 0` (i.e. redacted-thinking-only turns). For those,
`turn_trace`'s fallback already absorbs the residual into the
thinking span; redistributing would zero out the thinking attribution
and misattribute the cost to tools.

**Server-side tools are excluded** from the redistribution math.
Advisor, `web_search`, and `web_fetch` are billed via
`usage.iterations[*]` separately from the main turn's `output_tokens` —
their `output_token_estimate` represents iteration cost, not a share of
the main turn's output. Mixing them would create negative residuals on
turns that delegated to a sub-agent.

## Image tokens

Two paths flow into the `image_tokens` column / `image_tokens_estimate`
attribute:

- **Tool-result images** (browser screenshots, etc.): when a
  `tool_result.content` carries an `image` block, the transcript parser
  calls `estimate_image_only_tokens()` and stores the result on the
  matching tool span's `image_tokens` column.
- **User-prompt images**: when the user attaches an image to their
  prompt, `prompt_trace.py` resolves each image (from the inline base64
  in the transcript or the `~/.claude/image-cache/<session>/<N>.<ext>`
  files), runs `estimate_image_tokens()` per image, and stashes the sum
  as `image_tokens_estimate` on the prompt span attributes.

Both estimates are local approximations. The actual image cost is
billed by Anthropic inside the next turn's `input_tokens` (or
`cache_creation_input_tokens` if the image was eligible for caching).
The estimate is informational — it does not subtract cleanly from any
billed counter.

## Rollup math

`fetch_tool_token_rollup` (in `queries.py`) groups `session_spans` by tool name
with two synthetic buckets:

- `assistant_text` — collapses `assistant_response` spans
- `assistant_thinking` — collapses `assistant.thinking` spans

Then it derives:

```
attributed_input_tokens  = Σ span.input_tokens
attributed_output_tokens = Σ span.output_tokens
untagged_input_tokens    = max(0, session.input_tokens  - attributed_input_tokens)
untagged_output_tokens   = max(0, session.output_tokens - attributed_output_tokens)
```

The `max(0, …)` clamps over-attribution to zero so the chip rollup
doesn't display a confusing negative.

## Known divergences

The attribution layer is honest about three places where its math
intentionally doesn't balance:

### Server-tool double-counting on the input side

The advisor `server_tool_use` span's `input_tokens` carries the
*iterations* input (typically 50k–150k for a deep advisor call). That
total is **not** part of the main turn's `usage.input_tokens` (which
only covers the small main-turn prompt). The rollup sums them anyway,
producing `attributed_input_tokens > session.input_tokens` on sessions
that used advisor. The `max(0, …)` clamp hides it.

Fixing this cleanly requires splitting server-side iteration tokens
into their own bucket — see `tool_attribution_followups.md` item 7.

### Text-only turns (no tools)

If a turn emits only text (no tool_use blocks), the redistribution is
skipped because there's nowhere to scale. `assistant_text` then carries
just `estimate_text_tokens(turn.text)`, which is ~3–5% below the API's
billed output_tokens for that turn. The remainder surfaces as
`untagged_output_tokens`. Acceptable: tokenizer drift, not a structural
gap.

### Cost computation uses uncached input rate

`cost_usd` on tool spans assumes the uncached input rate. Most
tool_result tokens actually bill at the cache_read rate (~10× cheaper).
On long sessions the displayed cost can overstate billing materially.
See `tool_attribution_followups.md` item 4.

## Debugging a divergence

When a session's rollup looks wrong, the fastest path is:

1. Find the transcript at `~/.claude/projects/<cwd-munged>/<trace_id>.jsonl`.
2. Run `read_usage()` against it and dump per-turn `output_tokens` vs
   `text + thinking + Σ tool_use_estimate`. The gap per turn IS the
   untagged contribution from that turn.
3. Categorize: text+tool / text-only / thinking+tool (visible vs
   redacted) / silent / server-tool. The known divergences above name
   which categories absorb cleanly and which leak.
4. If a category that should absorb cleanly is leaking, that's a real
   bug in `transcript_usage.py`. If it's a known-divergence category,
   the leak is expected — file under follow-up.

For backfilling an existing session after changing the attribution
math, write the redistributed `output_token_estimate`s into
`session_spans.output_tokens` keyed by `(trace_id, tool_use_id)`. There
is no checked-in CLI for this — it's a one-shot per change.
