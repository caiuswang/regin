// Which of a span's raw attributes still belong in a generic key/value table,
// once the dedicated sections above it (prose, Q&A, rule list, deny panel, MCP
// round-trip) have already rendered their own keys.
//
// Extracted from SpanDetailPanel so the presentation reader resolves
// attributes through the same function: the rail and the reader can never
// disagree about what a span carries.
import { mcpParts } from './traceFormatters.js'
import { isAskSpan } from './liveRows.js'

// A send_to_user MCP call: its `user_message` is full markdown prose that gets
// a rendered surface of its own, so it never belongs in the opaque table.
export function isSendToUserSpan(span) {
  return /send[_-]to[_-]user/i.test(span?.name || '')
    || /send[_-]to[_-]user/i.test(span?.attributes?.tool_name || '')
}

// An MCP tool call (e.g. memory recall) rendered as a params/result
// round-trip rather than as opaque table rows. send_to_user is excluded — it
// has its own affordance.
export function mcpCallFor(span) {
  if (!span || isSendToUserSpan(span)) return null
  const a = span.attributes || {}
  if (!a.mcp && !mcpParts(span.name)) return null
  if (a.mcp_input == null && a.mcp_result == null) return null
  return {
    input: a.mcp_input,
    inputDropped: a.mcp_input_truncated_bytes,
    result: a.mcp_result,
    resultDropped: a.mcp_result_truncated_bytes,
  }
}

const MCP_HIDDEN_KEYS = ['mcp', 'mcp_input', 'mcp_result',
  'mcp_input_truncated_bytes', 'mcp_result_truncated_bytes', 'tool_input_keys']
const ASK_HIDDEN_KEYS = ['questions', 'answers', 'annotations', 'denied',
  'denial_reason', 'denial_reason_truncated_bytes', 'deny_kind']
const DENY_HIDDEN_KEYS = ['denied', 'denial_reason',
  'denial_reason_truncated_bytes', 'deny_kind']
const RULE_HIDDEN_KEYS = ['applicable_rules', 'engine_tags',
  'applicable_rule_count', 'violating_rule_count', 'total_rules', 'status',
  'relative_path']

function hiddenKeysFor(span) {
  const attrs = span.attributes || {}
  if (isSendToUserSpan(span)) return ['user_message']
  if (mcpCallFor(span)) return MCP_HIDDEN_KEYS
  if (span.name === 'assistant_response' || span.name === 'prompt') {
    return ['text', 'estimated_start_time']
  }
  if (span.name === 'assistant.thinking') return ['estimated_start_time']
  if (isAskSpan(span)) return ASK_HIDDEN_KEYS
  if (attrs.denied) return DENY_HIDDEN_KEYS
  if (span.name === 'rule.check') return RULE_HIDDEN_KEYS
  return []
}

// `extraHidden` lets a caller that rendered MORE of the span than the rail
// does (the reader renders thinking text and diffs as content) drop those keys
// too, without changing what the rail shows.
export function visibleAttributeKeys(span, extraHidden = []) {
  if (!span || !span.attributes) return []
  const hidden = new Set([...hiddenKeysFor(span), ...extraHidden])
  return Object.keys(span.attributes).filter(k => !hidden.has(k))
}
