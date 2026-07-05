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
  isRejectedToolSpan,
  memoryRecallOneLiner,
  EDIT_TOOL_NAMES,
  categoryOf,
  SPAN_CATEGORIES,
  spanMatchesSearch,
  fmtDuration,
} from './traceFormatters.js'

// The filter sheet renders the same 10 buckets as the desktop Terminal
// (definitions live in traceFormatters.js — no duplicated maps).
export const CATEGORIES = SPAN_CATEGORIES

// ── Row kind (v6 message-first hierarchy) ────────────────────

export function rowKind(span) {
  const n = span?.name || ''
  if (n === 'prompt' || n === 'assistant_response') return 'msg'
  if (isQaSpan(span)) return 'qa'
  // Workflow phase markers are structure, not a tappable row — the view
  // renders them as a centered divider band, not a LiveTailRow button.
  if (n === 'workflow.phase') return 'phase'
  return 'act'
}

// Centered phase-band label ("PHASE 3 · verify"). index is 0-based on the span.
export function phaseBandLabel(span) {
  const a = span?.attributes || {}
  const idx = (a.index == null ? 0 : a.index) + 1
  return `Phase ${idx}${a.title ? ` · ${a.title}` : ''}`
}

// Ask-user-question / permission spans get their own delicate 2-line row
// (v8) instead of the generic one-line activity row. `permission.denied`
// belongs here too: the pending permreq- placeholder is retired when the
// denial lands, so this span is the ONLY surviving record of the outcome —
// leaving it out meant a denial simply vanished from the card.
export function isQaSpan(span) {
  const n = span?.name || ''
  return n === 'tool.AskUserQuestion' || n === 'permission.request'
    || n === 'permission.denied'
    || (span?.span_id || '').startsWith('permreq-')
}

// Pure projection for the qa mini row: glyph + eyebrow, the question (or
// requested command), and the outcome line. Full detail lives in the sheet.
function askRowOutcome(a, chosen, pending) {
  if (a.denied) {
    const label = a.deny_kind === 'chat' ? 'answered in chat instead' : 'denied'
    return { mark: '✗', answer: label }
  }
  if (chosen) return { mark: '✓', answer: chosen }
  return { mark: '…', answer: pending ? 'waiting for your answer' : 'answered' }
}

function askRowModel(a, pending) {
  const qs = a.questions || []
  const q = qs[0] || {}
  const ans = a.denied ? '' : (a.answers || {})[q.question]
  const chosen = Array.isArray(ans) ? ans.join(', ') : (ans || '')
  return {
    glyph: '?',
    eyebrow: `Ask user${qs.length > 1 ? ` · +${qs.length - 1} more` : ''}`,
    badge: a.denied ? (a.deny_kind === 'chat' ? 'chat instead' : 'denied') : '',
    denied: !!a.denied,
    main: q.question || 'Question for you',
    mono: false,
    ...askRowOutcome(a, chosen, pending),
  }
}

function permOutcome(denied, pending, a) {
  if (denied) return { mark: '✗', answer: a.reason || 'denied' }
  if (pending) return { mark: '…', answer: 'waiting for permission' }
  return { mark: '✓', answer: 'granted' }
}

function permMain(a, tool) {
  if (a.command_preview) return { main: `$ ${a.command_preview}`, mono: true }
  if (a.requested_permission) return { main: a.requested_permission, mono: true }
  const q = a.questions?.[0]?.question
  return q ? { main: q, mono: false } : { main: `tool.${tool}`, mono: true }
}

function permRowModel(span, a, pending) {
  // The backend never writes decision fields onto permission.request — the
  // denial arrives as a separate permission.denied span (permission_events
  // `handle_denied`), so the span NAME is the authoritative denied signal.
  const denied = span?.name === 'permission.denied'
    || a.decision === 'denied' || !!a.denied
  const tool = toolDisplayName(a.tool_name || 'tool')
  return {
    glyph: '⚠',
    eyebrow: `Permission · ${tool}`,
    badge: denied ? 'denied' : '',
    denied,
    ...permMain(a, tool),
    ...permOutcome(denied, pending, a),
  }
}

export function qaRowModel(span) {
  const a = span?.attributes || {}
  const pending = span?.status_code === 'PENDING'
  return span?.name === 'tool.AskUserQuestion'
    ? askRowModel(a, pending)
    : permRowModel(span, a, pending)
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
  'file.edit', 'plan.edit', 'memory.recall', 'permission.request',
  'permission.denied', 'workflow.phase',
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
    // Paired single-asterisk italic; inner edges must be non-space so
    // arithmetic ("3 * 4") and globs ("*.py and *.md") survive intact.
    .replace(/\*(\S(?:[^*\n]*\S)?)\*/g, '$1')
    .replace(/^```.*$/gm, '')                  // fence lines
    .replace(/`([^`\n]+)`/g, '$1')             // inline code, unwrapped
    .replace(/^#{1,6}\s+/gm, '')               // heading markers
    .replace(/^>\s?/gm, '')                    // blockquote markers
    // No blanket [#*`>] strip: literal `*`/`>`/`#` in prose ("3 * 4",
    // "a > b", "*.py", "C#") are content, not syntax.
    .replace(/\s+/g, ' ')
    .trim()
}

function humanToolMain(span, a) {
  // `tool.failure` is a literal span name — the failed tool's identity
  // rides attributes.tool_name (post_tool_failure), same as desktop's
  // fullLabel/spanLabel special-case.
  const t = toolDisplayName(span.name === 'tool.failure'
    ? (a.tool_name || 'tool') : span.name.slice(5))
  const outcome = toolOutcomeMain(span, a, t)
  if (outcome) return outcome
  if (t === 'Bash') {
    return { pre: '$', text: a.command_preview || a.description || 'shell', mono: true }
  }
  const verb = toolVerbMain(t, a)
  if (verb) return verb
  const det = terminalSpanDetail(span)
  return { text: t + (det ? ` · ${det}` : '') }
}

function toolVerbMain(t, a) {
  const file = a.file_path ? a.file_path.split('/').pop() : ''
  if (!TOOL_VERB[t] || !(file || a.pattern || a.query || a.url)) return null
  const what = a.pattern
    ? a.pattern + (file ? ` in ${file}` : '')
    : (file || a.query || a.url)
  return { text: `${TOOL_VERB[t]} ${what}` }
}

// Sentence-case a shared one-liner ("recalled 2 memories" → "Recalled …").
function capitalize(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text
}

// Failed / rejected / denied tool rows must SAY so — the success verb
// ("Wrote config.py" for a blocked write) or a bare command line with only
// an amber dot is a color-only cue that reads as success.
function toolOutcomeMain(span, a, t) {
  const what = a.command_preview || (a.file_path ? a.file_path.split('/').pop() : '')
  // A command preview keeps the code font even on the failure path — that's
  // exactly where the literal command matters most.
  const mono = !!a.command_preview
  // A stuck/aborted tool is served as status ERROR with `is_interrupt` (merge
  // demotion for lost ingest, or the user-interrupt capture). It keeps its
  // original tool name, so the interrupt marker — not the tool's success verb
  // — must lead the row. `interrupt_source: 'user'` distinguishes a human abort.
  if (a.is_interrupt) {
    const byUser = a.interrupt_source === 'user' ? ' by user' : ''
    return { pre: '⏹', text: `${t} interrupted${byUser}${what ? ` · ${what}` : ''}`, mono }
  }
  if (span.name === 'tool.failure') {
    return { pre: '✗', text: `${t} failed${what ? ` · ${what}` : ''}`, mono }
  }
  // Same predicates that drive the hot-dot/signal tiering — the outcome
  // text and the tier must never disagree about rejected/denied.
  if (isRejectedToolSpan(span)) {
    return { pre: '✗', text: `${t} blocked${a.reject_reason ? ` — ${a.reject_reason}` : ''}` }
  }
  if (isDeniedToolSpan(span)) {
    const chat = a.deny_kind === 'chat' ? ' (answered in chat)' : ''
    return { pre: '✗', text: `${t} denied${chat}${what ? ` · ${what}` : ''}`, mono }
  }
  return null
}

// Task-event row phrasing: checklist glyph replaces the dot, subject as the
// line (prefixed "started · " while in progress), struck when completed.
function taskEventMain(a) {
  const st = a.status
  let glyph = '☐'
  let taskCls = ''
  if (st === 'in_progress') { glyph = '◔'; taskCls = 'doing' }
  else if (st === 'completed') { glyph = '☑'; taskCls = 'done' }
  const subject = a.subject || ''
  return {
    text: st === 'in_progress' ? `started · ${subject}` : subject,
    dim: true,
    taskGlyph: glyph,
    taskCls,
    struck: st === 'completed',
  }
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
  // Agent launch/done + task events are the four differentiated row kinds
  // : violet agent affordance, quiet checklist glyphs. `agent` tints the
  // dot/pre violet; `taskGlyph`/`struck` swap the dot for a checklist mark.
  'tool.Agent': a => ({
    pre: `Agent · ${a.subagent_type || a.agent_type || 'agent'}`,
    text: a.description ? `— ${a.description}` : '',
    agent: true,
  }),
  'subagent.stop': a => ({
    pre: `◆ ${a.agent_type || 'agent'} finished`,
    text: a.result_preview || '',
    agent: true,
    agentDone: true,
  }),
  'tool.TaskCreate': a => taskEventMain(a),
  'tool.TaskUpdate': a => taskEventMain(a),
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

// ── Per-agent span scoping ───────────────────────────────────
// A span is agent-internal iff attributes.agent_id is set. The three marker
// names are the compact representation of each subagent in the MAIN timeline
// (they also carry agent_id, but belong to main's view, never a scope).
const SCOPE_MARKERS = new Set(['tool.Agent', 'subagent.start', 'subagent.stop'])

// scopeId null → main scope: spans WITHOUT agent_id, plus the markers.
// scopeId set → that agent's spans only (agent_id === scopeId), markers out.
export function inScope(span, scopeId) {
  const aid = span?.attributes?.agent_id
  if (scopeId) return aid === scopeId && !SCOPE_MARKERS.has(span?.name)
  return !aid || SCOPE_MARKERS.has(span?.name)
}

export function partitionScope(spans, scopeId) {
  return (spans || []).filter(s => inScope(s, scopeId))
}

// One status phrasing for every scoped-agent surface (scope bar, scoped NOW
// zone, the agents sheet's status column). Server statuses beyond the plain
// running/finished pair:
//   waiting     — alive but blocked on a human (pending ask/permission);
//   interrupted — the launch was denied/killed (tooldeny ERROR marker), so a
//                 subagent.stop will never come;
//   stale       — no deny marker, but the agent went silent (killed
//                 terminal etc. — real activity always advances last_seen).
// `compact` shortens for narrow slots (the agents sheet's time column at
// 375px — the full phrase squeezes the description to ~10ch); the scope
// bar / NOW zone keep the verbose form.
export function agentStatusLabel(agent, elapsed, { compact = false } = {}) {
  if (agent.status === 'interrupted') return 'interrupted'
  if (agent.status === 'waiting') return compact ? 'waiting' : 'waiting for input'
  if (agent.status === 'stale') {
    return compact
      ? `stale · ${agent.lastSeenClock}`
      : `stale · last seen ${agent.lastSeenClock}`
  }
  if (agent.running) return `running · ${elapsed}`
  // Real subagent.stop markers are point events (duration_ms 0); the
  // duration derives from segment span times and can still be unknown —
  // never render a dangling "finished · ".
  const dur = fmtDuration(agent.durationMs)
  return dur ? `finished · ${dur}` : 'finished'
}

// done/total across the session's FINAL task snapshot (meta.task_list.final),
// never re-derived from the loaded tail. Null when the session used no tasks.
export function taskSummaryOf(finalTasks) {
  if (!Array.isArray(finalTasks) || !finalTasks.length) return null
  let done = 0
  let inProgress = 0
  let open = 0
  for (const t of finalTasks) {
    if (t.status === 'completed') done += 1
    else if (t.status === 'in_progress') inProgress += 1
    else open += 1
  }
  return { total: finalTasks.length, done, inProgress, open }
}
