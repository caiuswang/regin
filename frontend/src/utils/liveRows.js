// Row semantics for the /live mobile session-tail card. Pure, stateless
// functions only (same contract as traceFormatters.js).
//
// One-map rule: label / detail / dot-color primitives are IMPORTED from
// traceFormatters.js — this module adds only the live-card projections that
// have no desktop equivalent: the signal/system tier split (v4/v5), the
// human row phrasing (v5), the message-vs-activity row kind (v6), and the
// filter-sheet category buckets.
import {
  terminalSpanDetail,
  toolDisplayName,
  isErrorToolSpan,
  isDeniedToolSpan,
  memoryRecallOneLiner,
  EDIT_TOOL_NAMES,
  categoryOf,
  SPAN_CATEGORIES,
  spanMatchesSearch,
} from './traceFormatters.js'

// The filter sheet renders the same 10 buckets as the desktop Terminal
// (definitions live in traceFormatters.js — no duplicated maps).
export const CATEGORIES = SPAN_CATEGORIES

// ── Row kind (v6 message-first hierarchy) ────────────────────

export function rowKind(span) {
  const n = span?.name || ''
  return (n === 'prompt' || n === 'assistant_response') ? 'msg' : 'act'
}

// Signal spans that keep a full-saturation dot (edits, failures, denials);
// everything else renders its category color as 55%-opacity texture (v7).
export function isHotSpan(span) {
  return EDIT_TOOL_NAMES.has(span?.name)
    || isErrorToolSpan(span)
    || isDeniedToolSpan(span)
}

// ── Signal filter (v4/v5): system spans hide by default ──────

const SIGNAL_EXACT = new Set([
  'prompt', 'assistant_response', 'subagent.start', 'subagent.stop',
  'file.edit', 'plan.edit', 'memory.recall',
])

export function isSignal(span) {
  const n = span?.name || ''
  const a = span?.attributes || {}
  // Failures / denials / rejections never hide (shared helpers, as isHotSpan).
  if (isErrorToolSpan(span) || isDeniedToolSpan(span)) return true
  if (SIGNAL_EXACT.has(n)) return true
  if (n === 'rule.check') return (a.findings || 0) > 0
  if (n.startsWith('pre_tool.')) return false
  if (n.startsWith('tool.') || n.startsWith('skill.')) return true
  return false // unknown ⇒ system: new span types never flood the card
}

// ── Human row phrasing (v5): what happened, not the span type ─

const TOOL_VERB = {
  Read: 'Read', Edit: 'Edited', Write: 'Wrote', MultiEdit: 'Edited',
  NotebookEdit: 'Edited', apply_patch: 'Edited', Grep: 'Searched',
  WebFetch: 'Fetched', WebSearch: 'Searched web',
}

// Plain-text projection of markdown-ish text: links keep their text, list
// markers and syntax marks drop, whitespace collapses. Used by message
// rows and the NOW zone's 2-line clamp. Underscore/tilde emphasis is only
// stripped when PAIRED — snake_case identifiers (file_path, tool_use_id …)
// are everyday content here and must survive. `\b_…_\b` is safe for them
// because `_` is a word character: no boundary exists inside snake_case.
export function stripMarkdown(text) {
  return (text || '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')   // [text](url) → text
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, '') // leading list markers
    .replace(/(\*\*|__|~~)([^\n]*?)\1/g, '$2') // paired bold / strike
    .replace(/\b_([^_\n]+)_\b/g, '$1')         // paired italic underscores
    .replace(/[#*`>]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function humanToolMain(span, a) {
  const t = toolDisplayName(span.name.slice(5))
  if (t === 'Bash') {
    return { pre: '$', text: a.command_preview || a.description || 'shell', mono: true }
  }
  const file = a.file_path ? a.file_path.split('/').pop() : ''
  if (TOOL_VERB[t] && (file || a.pattern || a.query || a.url)) {
    const what = a.pattern
      ? a.pattern + (file ? ` in ${file}` : '')
      : (file || a.query || a.url)
    return { text: `${TOOL_VERB[t]} ${what}` }
  }
  const det = terminalSpanDetail(span)
  return { text: t + (det ? ` · ${det}` : '') }
}

// Sentence-case a shared one-liner ("recalled 2 memories" → "Recalled …").
function capitalize(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text
}

// Exact-name phrasing builders (same idiom as traceFormatters'
// _TERMINAL_DETAIL_BUILDERS — keyed dispatch instead of a branch ladder).
// Every signal-tier span name needs a human phrasing here or below —
// raw span-type names must never reach the default view (acceptance 8c).
const MAIN_BUILDERS = {
  prompt: a => ({ pre: 'You', text: stripMarkdown(a.text) }),
  assistant_response: a => ({ text: stripMarkdown(a.text) }),
  'assistant.thinking': a => ({ pre: 'Thinking', text: stripMarkdown(a.thinking_text), dim: true }),
  'subagent.start': a => ({ text: `Agent ${a.agent_type || (a.agent_id || '').slice(0, 8)} started` }),
  'subagent.stop': a => ({ text: a.agent_type ? `Agent ${a.agent_type} finished` : 'Agent finished' }),
  'rule.check': a => ({
    text: `Rule ${a.rule_id || ''}${a.findings
      ? ` · ${a.findings} finding${a.findings > 1 ? 's' : ''}`
      : ' · clean'}`,
    dim: !a.findings,
  }),
  'file.edit': a => ({
    text: a.file_path ? `Edited ${a.file_path.split('/').pop()}` : 'Edited file',
  }),
  'plan.edit': a => ({
    text: a.file_path ? `Plan updated · ${a.file_path.split('/').pop()}` : 'Plan updated',
  }),
  'memory.recall': (_a, span) => ({ text: capitalize(memoryRecallOneLiner(span)) }),
}

// The row's one line of human language: `{ pre?, text, mono?, dim? }`.
// `pre` renders bold ("You", "$"), `mono` switches to the code font,
// `dim` mutes low-signal rows. The dot color carries the category.
export function humanMain(span) {
  const a = span.attributes || {}
  const n = span.name || ''
  if (Object.hasOwn(MAIN_BUILDERS, n)) return MAIN_BUILDERS[n](a, span)
  if (n.startsWith('skill.')) return { text: `Skill ${a.skill_id || a.skill_name || ''}` }
  if (n.startsWith('tool.')) return humanToolMain(span, a)
  // System rows (visible only via the show-system toggle) keep the raw name.
  const det = terminalSpanDetail(span)
  return { text: n + (det ? ` · ${det}` : ''), dim: true }
}

// ── Filter predicate + per-category counts ───────────────────

export function countByCategory(spans) {
  const counts = { all: 0 }
  for (const c of CATEGORIES) counts[c.id] = 0
  for (const s of spans || []) {
    counts.all += 1
    counts[categoryOf(s)] += 1
  }
  return counts
}

// The card's visible-row predicate: signal tier, category chip, search
// (same haystack the desktop Terminal searches — shared spanMatchesSearch).
export function filterSpans(spans, { showSystem, category, query }) {
  const q = (query || '').trim().toLowerCase()
  return (spans || []).filter(s => {
    if (!showSystem && !isSignal(s)) return false
    if (category && category !== 'all' && categoryOf(s) !== category) return false
    return spanMatchesSearch(s, q)
  })
}

// Copy payload for an activity row's sheet: the most useful attr, falling
// back to the span's attrs JSON (spec v7.1).
export function activityCopyPayload(span) {
  const a = span.attributes || {}
  return a.command_preview || a.text || a.file_path
    || JSON.stringify({ name: span.name, span_id: span.span_id, attributes: a }, null, 2)
}

// Reverse find without Array.prototype.findLast (older build targets).
export function findLastSpan(spans, predicate) {
  for (let i = (spans || []).length - 1; i >= 0; i--) {
    if (predicate(spans[i])) return spans[i]
  }
  return null
}
