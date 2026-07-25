/**
 * Does this span render as a card in the conversation feed?
 *
 * ConversationSpanCard is a v-if/v-else-if chain with NO catch-all branch, so
 * a span whose name matches nothing produces an empty row. That was papered
 * over in CSS (`.event-spine-row:not(:has(...))` in style.css) and separately
 * mis-counted in the turn header, which claimed "259 events" over 61 rendered
 * rows because it counted spans the chain silently drops.
 *
 * This is the single source of truth for that question. It MUST mirror the
 * branches in ConversationSpanCard.vue — `conversation-event-count.spec.js`
 * asserts the two agree by comparing the header count against the rows the
 * DOM actually shows, so a new branch added here or there fails a test rather
 * than drifting quietly.
 */
const EXACT_NAMES = new Set([
  'task.notification',
  'assistant.thinking',
  'assistant_response',
  'tool.failure',
  'tool.ToolSearch',
  'rule.check',
  'memory.recall',
  'harness.local_command',
  'harness.recap',
  'workflow.phase',
  'subagent.start',
  'workflow.agent_result',
  'tool.Workflow',
  'skill.read',
  'skill.invoke',
  'file.edit',
  'plan.edit',
])

export function rendersInConversation(span) {
  const name = span?.name
  if (!name) return false
  const attrs = span.attributes || {}

  // Every attribute-conditional branch in the chain (Bash with output,
  // Edit/Write with a diff, AskUserQuestion with questions) falls through to
  // the generic inline row when its condition fails, so for "does anything
  // render" the tool prefixes settle it on their own.
  if (name.startsWith('tool.') || name.startsWith('subagent.')) return true
  if (attrs.server_side && attrs.response_text) return true
  return EXACT_NAMES.has(name)
}
