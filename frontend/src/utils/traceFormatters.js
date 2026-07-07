// Pure formatters extracted from SessionConversationView (PR 2.3a). All
// functions in this module are stateless: given the same input they
// produce the same output, with no closure over component state or
// reactive dependencies. SFCs that need them should import from here
// rather than re-declaring locally.
//
// Sibling views (SessionTraceView, SessionTerminalLog, SessionsView,
// MCPCallsView) still have their own near-duplicate copies; folding
// those in is a follow-up since they each have small drift
// (e.g. SessionsView.fmtDate, SessionTraceView.fmtLocalClock).

// ── Time / number / text ─────────────────────────────────────

// Backend span/session timestamps are NAIVE local ISO strings with 6-digit
// microseconds; bare `new Date(iso)` on those is engine-dependent (WebKit
// rejects the long fraction). Every wall-clock formatter parses through
// parseLocalIso, which handles both naive and explicitly-zoned inputs.
import { parseLocalIso } from './sessionActivity.js'

export function fmtTime(iso) {
  const d = parseLocalIso(iso)
  if (!d || Number.isNaN(d.getTime())) return '--:--:--'
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

export function fmtClock(iso) {
  const d = parseLocalIso(iso)
  if (!d || Number.isNaN(d.getTime())) return '--:--:--'
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

// Full local date + time for a stored UTC ISO timestamp (the backend
// emits e.g. "2026-05-25T14:32:45Z"). `toLocaleString` renders it in the
// viewer's own timezone. Empty string for missing/unparseable input so
// callers render nothing rather than "Invalid Date".
export function fmtLocalDateTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString()
}

export function fmtDuration(ms) {
  if (!ms || ms <= 0) return ''
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`
  const minutes = Math.floor(ms / 60000)
  const seconds = Math.floor((ms / 1000) % 60)
  if (minutes < 10 && seconds) return `${minutes}m${String(seconds).padStart(2, '0')}s`
  return `${minutes}m`
}

// Live elapsed readout for an in-flight span, in whole seconds with unit
// rollover: 45 → "45s", 489 → "8m09s", 3900 → "1h05m". Distinct from
// fmtDuration (which ms-formats completed sub-second spans): the input is
// a client-ticked second count anchored to the span's start_time, so a
// long-running pending tool reads as minutes/hours, never "489s".
export function fmtElapsedSeconds(secs) {
  if (!Number.isFinite(secs) || secs < 0) return ''
  if (secs < 60) return `${secs}s`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m${String(secs % 60).padStart(2, '0')}s`
  return `${Math.floor(mins / 60)}h${String(mins % 60).padStart(2, '0')}m`
}

// Relative "time ago" for a timestamp. Coarse buckets (just now / Nm /
// Nh / Nd) up to a week, then a plain Y-M-D date. Empty string for
// missing/unparseable input so callers can render nothing.
export function fmtAgo(iso) {
  if (!iso) return ''
  const then = parseLocalIso(iso)?.getTime() ?? NaN
  if (Number.isNaN(then)) return ''
  const secs = Math.floor((Date.now() - then) / 1000)
  if (secs < 60) return 'just now'
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  const d = new Date(then)
  const mo = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mo}-${day}`
}

export function fmtTokens(n) {
  if (n == null) return ''
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, '') + 'k'
  return String(n)
}

// Shorten a full model id to a scannable family + version label:
//   claude-opus-4-8[1m] -> opus 4.8   ·   claude-haiku-4-5 -> haiku 4.5
// The `[1m]`-style context-window suffix is dropped from the chip (keep
// the full id in a title tooltip at the call site). Falls back to a
// `claude-` / `[...]`-stripped form for any shape we don't recognise.
export function fmtModel(model) {
  if (!model) return ''
  const m = String(model).match(/(opus|sonnet|haiku)-(\d+)-(\d+)/i)
  if (m) return `${m[1].toLowerCase()} ${m[2]}.${m[3]}`
  return String(model).replace(/^claude-/, '').replace(/\[.*\]$/, '')
}

// USD cost. Sub-cent values keep 4 dp (per-tool costs are often fractions
// of a cent); anything larger rounds to 2 dp. Empty string for null.
export function fmtCost(usd) {
  if (usd == null) return ''
  if (usd < 0.01) return '$' + usd.toFixed(4)
  return '$' + usd.toFixed(2)
}

export function fmtBytes(n) {
  if (!n) return ''
  if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1).replace(/\.0$/, '') + ' MB'
  if (n >= 1024) return (n / 1024).toFixed(1).replace(/\.0$/, '') + ' KB'
  return `${n} B`
}

export function truncate(text, max) {
  if (!text) return ''
  if (text.length <= max) return text
  return text.slice(0, max) + '…'
}

// ── Tool / span semantics ────────────────────────────────────

export function toolDisplayName(tool) {
  // MCP tools come as `mcp__server__tool` — show just the tool segment,
  // the server is usually obvious from context and eats row width.
  if (tool && tool.startsWith('mcp__')) {
    const parts = tool.split('__')
    return parts[parts.length - 1] || tool
  }
  return tool
}

// Split an MCP tool name into its server + endpoint. Accepts the bare
// `mcp__server__endpoint` form or a span name with a leading `tool.`.
// The server is a single `__`-delimited segment; the endpoint keeps any
// remaining segments joined (a tool segment could itself contain `__`).
export function mcpParts(toolName) {
  if (!toolName) return null
  const raw = toolName.startsWith('tool.') ? toolName.slice(5) : toolName
  if (!raw.startsWith('mcp__')) return null
  const parts = raw.split('__')
  if (parts.length < 3) return null
  return { server: parts[1], endpoint: parts.slice(2).join('__') }
}

// Compact a noisy MCP server id for display. Plugin-provided servers come
// through as `plugin_<plugin>_<server>` and frequently repeat the name
// (`plugin_playwright_playwright`), which blows out chip/row width. Strip
// the `plugin_` prefix and collapse adjacent duplicate segments so that
// `plugin_playwright_playwright` → `playwright`; `gitnexus` is untouched.
function mcpServerDisplay(server) {
  if (!server) return server
  const segs = server.replace(/^plugin_/, '').split('_')
  const out = []
  for (const seg of segs) {
    if (out[out.length - 1] !== seg) out.push(seg)
  }
  return out.join('_')
}

// Row/chip label for any tool. MCP tools render as `server · endpoint`
// so the reader can tell which MCP server was hit; everything else falls
// back to the plain (server-stripped) tool name.
export function toolDisplayLabel(toolName) {
  const p = mcpParts(toolName)
  return p ? `${mcpServerDisplay(p.server)} · ${p.endpoint}` : toolDisplayName(toolName)
}

export function toolFilePath(span) {
  const a = span?.attributes || {}
  if (a.file_path) return a.file_path
  const ti = a.tool_input
  if (ti?.file_path) return ti.file_path
  if (ti?.path) return ti.path
  if (ti?.notebook_path) return ti.notebook_path
  const preview = ti?.preview
  if (typeof preview !== 'string' || !preview) return null
  try {
    const parsed = JSON.parse(preview)
    return parsed?.file_path || parsed?.path || parsed?.notebook_path || null
  } catch {
    const m = preview.match(/"(?:file_path|path|notebook_path)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"/)
    if (!m) return null
    try {
      return JSON.parse(`"${m[1]}"`)
    } catch {
      return m[1]
    }
  }
}

// One-liner fallback when the rule.check span is rendered through a
// generic label slot (search index, ToC preview). The dedicated row
// template renders status / file / counts as separate spans so they can
// be colour-coded; this string is only used when that custom branch
// can't kick in.
export function ruleCheckOneLiner(span) {
  const a = span.attributes || {}
  const file = a.relative_path ? a.relative_path.split('/').pop() : 'rule check'
  if (a.status === 'no_applicable_rules') return `rule · ${file} — no applicable rules`
  const total = a.applicable_rule_count ?? 0
  const violations = a.violating_rule_count ?? 0
  if (violations > 0) return `rule · ${file} — ${violations} of ${total} violated`
  return `rule · ${file} — ${total} passed`
}

// One-liner for a `memory.recall` span: how many memories were injected
// into the prompt as `<recalled_experience>`. The full block + per-hit
// list render in the dedicated MemoryRecallRow card and the span detail
// panel. Delegated (like ruleCheckOneLiner) so the fullLabel/spanLabel
// twins don't grow another inline branch.
export function memoryRecallOneLiner(span) {
  const a = span.attributes || {}
  const n = a.hit_count ?? (a.hits ? a.hits.length : 0)
  const noun = n === 1 ? 'memory' : 'memories'
  // A `memory.recall` span marked source='skill_experience' is the
  // <skill_experience> block injected for an invoked skill, not generic recall.
  if (a.source === 'skill_experience') {
    return `skill experience${a.skill_id ? ` (${a.skill_id})` : ''} · ${n} ${noun}`
  }
  return `recalled ${n} ${noun}`
}

// A `tool.ScheduleWakeup` call is always turn-final: the agent yields control
// at the end of an autonomous (/loop) turn. `stop` ends the loop — the agent is
// finished; otherwise it schedules a resume after `delay_seconds`, with `reason`
// explaining why (often polling background work), so the row reads "paused", not
// "finished". Shared by fullLabel/toolLabel (conversation) and liveRows
// (live card) so the three surfaces can't drift. Returns
// `{ finished: boolean, main: string }`.
export function scheduleWakeupParts(a) {
  a = a || {}
  if (a.stop === true || a.stop === 'true') {
    return { finished: true, main: 'agent finished — loop stopped' }
  }
  const resume = typeof a.resume_action === 'string' ? a.resume_action.trim() : ''
  // Idle poll-loop: a run of wakeups that each just reschedule (wakeup_links
  // stamps poll_round/poll_total). Collapse to a "waiting… (k/N)" progression;
  // the run's LAST member (round === total) exited into real work, so name it.
  if (a.poll_total > 1) {
    const total = a.poll_total
    const tail = a.poll_round === total && resume ? ` → resumed: ${resume}` : ''
    return { finished: false, poll: true, main: `waiting… (${a.poll_round}/${total})${tail}` }
  }
  const delay = Number(a.delay_seconds)
  const when = Number.isFinite(delay) && delay > 0 ? fmtElapsedSeconds(delay) : ''
  const reason = typeof a.reason === 'string' ? a.reason.trim() : ''
  let main = 'scheduled wakeup'
  if (when && reason) main = `paused ${when} — ${reason}`
  else if (when) main = `paused ${when}`
  else if (reason) main = `paused — ${reason}`
  if (resume) main += ` → resumed: ${resume}`
  return { finished: false, main }
}

// NOTE: `fullLabel` (conversation view) and `spanLabel` (timeline/tree view,
// below) are drifted twins — both turn a span into a one-line label but cover
// different span families (fullLabel: task.notification / assistant_response /
// AskUserQuestion / reject_reason; spanLabel: plan.* / compact.* /
// workflow.phase / subagent.* / harness.local_command). Unifying them is the
// follow-up the module header flags; until then, edit both when the shared
// branches (tool.* / rule.check) change.
export function fullLabel(span) {
  const a = span.attributes || {}
  const name = span.name || ''
  if (name === 'prompt') return a.text || 'prompt'
  if (name === 'task.notification') return a.summary || 'background task'
  if (name === 'assistant_response') return a.text || 'response'
  if (name === 'harness.recap') return a.content || 'recap'
  if (name === 'tool.failure') {
    const tool = toolDisplayLabel(a.tool_name || 'tool')
    const bits = [`failed: ${tool}`]
    if (a.is_interrupt) bits.push('(user interrupt)')
    if (a.error) bits.push(`— ${a.error}`)
    return bits.join(' ')
  }
  if (name.startsWith('tool.')) {
    const tool = toolDisplayLabel(name.slice(5))
    const fp = toolFilePath(span)
    const fileTail = fp ? fp.split('/').pop() : null
    if (a.reject_reason) {
      return fileTail
        ? `${tool}: ${fileTail} — ${a.reject_reason}`
        : `${tool}: ${a.reject_reason}`
    }
    if (a.command_preview) return `${tool}: ${a.command_preview}`
    const agentLaunch = name === 'tool.Agent' && agentLaunchLabel(a, tool)
    if (agentLaunch) return agentLaunch
    if (name === 'tool.TaskCreate' && a.subject) {
      return a.task_id ? `${tool} #${a.task_id}: ${a.subject}` : `${tool}: ${a.subject}`
    }
    if (name === 'tool.TaskUpdate' && a.task_id) {
      return a.status ? `${tool} #${a.task_id} → ${a.status}` : `${tool} #${a.task_id}`
    }
    if (name === 'tool.TaskOutput' && a.task_id) {
      return a.status ? `${tool} #${a.task_id} → ${a.status}` : `${tool} #${a.task_id}`
    }
    if (name === 'tool.ScheduleWakeup') {
      return `${tool}: ${scheduleWakeupParts(a).main}`
    }
    if (name === 'tool.Skill' && a.skill_name) {
      return `${tool}: ${a.skill_name}`
    }
    if (a.questions && a.questions.length) {
      const answers = a.answers || {}
      const joined = a.questions.map(q => {
        const text = q?.question || q?.header || ''
        const ans = answers[q?.question]
        return ans ? `${text} → ${ans}` : text
      }).join('\n')
      return `${tool}:\n${joined}`
    }
    // ToolSearch: prefer `loaded_tools` (authoritative tools the search
    // returned) over `selected_tools` (parsed from the `select:` query).
    // For keyword queries `loaded_tools` is the only place names appear.
    const tsTools = a.loaded_tools && a.loaded_tools.length
      ? a.loaded_tools
      : a.selected_tools
    if (tsTools && tsTools.length) {
      const short = tsTools.map(t => t.split('__').pop()).join(', ')
      return `${tool}: ${short}`
    }
    if (a.query) return `${tool}: ${a.query}`
    if (a.url) return `${tool}: ${a.url}`
    if (a.pattern && fp) return `${tool}: ${a.pattern} in ${fp.split('/').pop()}`
    if (a.pattern) return `${tool}: ${a.pattern}`
    if (fp) return `${tool}: ${fp.split('/').pop()}`
    return tool
  }
  switch (name) {
    case 'skill.read': return `read: ${a.skill_id || ''}`
    case 'skill.invoke': return `invoke: ${a.skill_id || ''}`
    case 'file.edit': return `edit: ${a.file_path ? a.file_path.split('/').pop() : ''}`
    case 'plan.edit': return `plan edit: ${a.file_path ? a.file_path.split('/').pop() : ''}`
    case 'rule.check': return ruleCheckOneLiner(span)
    case 'memory.recall': return memoryRecallOneLiner(span)
    case 'subagent.start': return `subagent: ${a.agent_type || ''}`
    case 'subagent.stop': return 'subagent done'
  }
  return name
}

// Subagent identity tag: the explicit agent_type, else a short agent_id.
function subagentTag(a) {
  return a.agent_type || (a.agent_id ? a.agent_id.slice(0, 8) : '')
}

// One-line goal for a `tool.Agent` launch row: the launch `description` is
// the run's purpose. Most launches are folded into their subagent row
// (useAgentLaunchMerge); this covers the unpaired ones — chiefly the PENDING
// twin emitted at PreToolUse before `subagent.start` lands. Shared by
// fullLabel/toolLabel so the drifted twins can't diverge on this branch.
function agentLaunchLabel(a, tool) {
  if (!a.description) return ''
  return a.subagent_type ? `${tool} (${a.subagent_type}): ${a.description}` : `${tool}: ${a.description}`
}

// `compact.post` row label. Appends the serve-time reclaim delta
// (`attributes.reclaimed_tokens`, stamped by queries.py
// _attach_compaction_reclaim) as a `· freed ~N` suffix. The backend omits
// the attribute when a bracket turn is missing or the delta is non-positive,
// so the suffix only shows when the number is meaningful. Extracted so the
// already-large `spanLabel` switch doesn't grow another branch.
function compactPostLabel(a) {
  const tr = a.trigger ? ` (${a.trigger})` : ''
  const freed = a.reclaimed_tokens ? ` · freed ~${fmtTokens(a.reclaimed_tokens)}` : ''
  return `context compacted${tr}${freed}`
}

// Row label for a `/rewind` marker. Counts come from the shallow map's
// preserved attributes (`abandoned_prompt_count`, `rolled_back_count`).
function rewindLabel(a) {
  const p = a.abandoned_prompt_count || 0
  const f = a.rolled_back_count || 0
  const parts = []
  if (p) parts.push(`${p} prompt${p === 1 ? '' : 's'}`)
  if (f) parts.push(`${f} file${f === 1 ? '' : 's'}`)
  return parts.length ? `↩ rewound — ${parts.join(', ')}` : '↩ rewound'
}

// Row label for a `tool.*` span (or any tool-shaped attribute bag `a` with a
// `fallback` tool name). Private to `spanLabel`. Mirrors the tool.* branch of
// `fullLabel` but keyed off `a.tool_name`/`fallback` rather than the span name.
function toolLabel(a, fallback) {
  const tool = toolDisplayLabel(a.tool_name || fallback)
  if (a.command_preview) return `${tool}: ${a.command_preview}`
  const agentLaunch = (a.tool_name === 'Agent' || fallback === 'Agent') && agentLaunchLabel(a, tool)
  if (agentLaunch) return agentLaunch
  // Task tools: subject is the entire signal. Without this branch the
  // Timeline view renders 53 bare "TaskCreate"/"TaskUpdate" rows
  // indistinguishable from each other.
  if ((a.tool_name === 'TaskCreate' || fallback === 'TaskCreate') && a.subject) {
    return a.task_id ? `${tool} #${a.task_id}: ${a.subject}` : `${tool}: ${a.subject}`
  }
  if ((a.tool_name === 'TaskUpdate' || fallback === 'TaskUpdate') && a.task_id) {
    return a.status ? `${tool} #${a.task_id} → ${a.status}` : `${tool} #${a.task_id}`
  }
  if ((a.tool_name === 'TaskOutput' || fallback === 'TaskOutput') && a.task_id) {
    return a.status ? `${tool} #${a.task_id} → ${a.status}` : `${tool} #${a.task_id}`
  }
  if (a.tool_name === 'ScheduleWakeup' || fallback === 'ScheduleWakeup') {
    return `${tool}: ${scheduleWakeupParts(a).main}`
  }
  if ((a.tool_name === 'Skill' || fallback === 'Skill') && a.skill_name) {
    return `${tool}: ${a.skill_name}`
  }
  const tsTools = a.loaded_tools && a.loaded_tools.length
    ? a.loaded_tools
    : a.selected_tools
  if (tsTools && tsTools.length) {
    return `${tool}: ${tsTools.map(t => t.split('__').pop()).join(', ')}`
  }
  if (a.query) return `${tool}: ${a.query}`
  if (a.url) return `${tool}: ${a.url}`
  if (a.pattern && a.file_path) return `${tool}: ${a.pattern} in ${a.file_path.split('/').pop()}`
  if (a.pattern) return `${tool}: ${a.pattern}`
  if (a.file_path) return `${tool}: ${a.file_path.split('/').pop()}`
  return tool
}

// Timeline/tree row label for any span. Drifted twin of `fullLabel` (see note
// there). Extracted from SessionTraceView. The `rule.check` branch delegates to
// `ruleCheckOneLiner` so the two label paths can't drift on that span.
export function spanLabel(span) {
  const a = span.attributes || {}
  switch (span.name) {
    case 'skill.read': return `read: ${a.skill_id || ''}`
    case 'skill.invoke': return `invoke: ${a.skill_id || ''}`
    case 'file.edit': return `edit: ${a.file_path ? a.file_path.split('/').pop() : ''}`
    case 'plan.edit': return `plan edit: ${a.file_path ? a.file_path.split('/').pop() : ''}`
    case 'rule.check': return ruleCheckOneLiner(span)
    case 'memory.recall': return memoryRecallOneLiner(span)
    case 'plan.session': return `plan session: ${a.plan_filename || ''}`
    case 'plan.draft': return `plan draft: ${a.plan_filename || ''}`
    case 'plan.review': return `plan review: ${a.plan_filename || ''}`
    case 'plan.decision': return `plan decision: ${a.decision || ''}`
    case 'plan.enter': return `plan: ${a.plan_filename || ''}`
    case 'plan.exit': return 'plan exit'
    case 'compact.pre': {
      const tr = a.trigger ? ` (${a.trigger})` : ''
      const ci = a.custom_instructions ? `: ${a.custom_instructions.slice(0, 60)}` : ''
      return `context compacting${tr}${ci}`
    }
    case 'compact.post': return compactPostLabel(a)
    case 'rewind': return rewindLabel(a)
    case 'prompt': return a.text ? a.text.slice(0, 60) : 'prompt'
    case 'conversation': return 'conversation start'
    case 'harness.local_command': {
      const cmd = a.command_name || 'command'
      return a.args ? `${cmd} ${a.args}` : cmd
    }
    case 'harness.recap':
      return a.content ? `recap: ${a.content.slice(0, 60)}` : 'recap'
    case 'workflow.phase':
      return a.title ? `phase: ${a.title}` : 'phase'
    case 'subagent.start': {
      const tag = subagentTag(a)
      return tag ? `subagent: ${tag}` : 'subagent'
    }
    case 'subagent.stop': {
      const tag = subagentTag(a)
      return tag ? `subagent done: ${tag}` : 'subagent done'
    }
    default:
      if (span.name === 'tool.failure') {
        const tool = toolDisplayLabel(a.tool_name || 'tool')
        const bits = [`failed: ${tool}`]
        if (a.is_interrupt) bits.push('(user interrupt)')
        if (a.error) bits.push(`— ${a.error}`)
        return bits.join(' ')
      }
      if (span.name.startsWith('tool.')) {
        return toolLabel(a, span.name.slice(5))
      }
      if (span.name.startsWith('pre_tool.')) {
        return `pre: ${toolDisplayLabel(a.tool_name || span.name.slice(9))}`
      }
      return span.name
  }
}

// ── Terminal flat-log labels/details (SessionTerminalLog) ────
//
// The Terminal view renders a flat two-column "SPAN · DETAIL" log. These
// formatters are a separate family from `fullLabel`/`spanLabel` above:
// the label keeps the canonical span name (e.g. `tool.Bash`) and the
// detail carries terse per-event context. Moved verbatim out of the SFC.

// Terminal-log truncate: collapses internal whitespace (newlines/tabs →
// single space) BEFORE the length check, unlike the exported `truncate`.
// Kept private so the two truncation behaviours can't be conflated.
function _terminalTruncate(text, max) {
  if (!text) return ''
  text = String(text).replace(/\s+/g, ' ').trim()
  if (text.length <= max) return text
  return text.slice(0, max) + '…'
}

// Multi-line "question → answer" preview for AskUserQuestion tool spans.
// NOT truncated — the multi-line join is rendered line-by-line by the row.
export function terminalAskQuestionPreview(a) {
  const qs = a.questions || []
  if (!qs.length) return ''
  const answers = a.answers || {}
  return qs.map(q => {
    const text = q?.question || q?.header || ''
    const ans = answers[q?.question]
    return ans ? `${text} → ${ans}` : text
  }).join('\n')
}

// Span names that label as themselves (the canonical name is the label).
// The fallthrough already returns `n` for unknown names, so these are
// identity branches kept explicit only for documentation of coverage.
const _TERMINAL_IDENTITY_LABELS = new Set([
  'prompt', 'assistant_response',
  'skill.read', 'skill.invoke', 'skill.launch',
  'rule.check', 'subagent.start', 'subagent.stop',
  'session.start', 'session.end', 'compact.pre', 'compact.post',
  'environment.git_status', 'rewind',
])

export function terminalSpanLabel(span) {
  const n = span.name || ''
  if (_TERMINAL_IDENTITY_LABELS.has(n)) return n
  if (n.startsWith('tool.')) {
    return `tool.${toolDisplayLabel(n.slice(5))}`
  }
  if (n.startsWith('pre_tool.')) {
    return `pre_tool.${toolDisplayLabel(n.slice(9))}`
  }
  return n
}

// Detail for the non-tool span families. Returns undefined when `n` isn't
// one of them, signalling the caller to fall through to the tool branch.
const _TERMINAL_DETAIL_BUILDERS = {
  prompt: (a) => (a.text ? _terminalTruncate(a.text, 100) : ''),
  assistant_response: (a) => (a.text ? `"${_terminalTruncate(a.text, 80)}"` : ''),
  'assistant.thinking': (a) => (a.thinking_text ? _terminalTruncate(a.thinking_text, 100) : ''),
  'skill.read': (a) => a.skill_id || '',
  'skill.invoke': (a) => a.skill_id || '',
  'skill.launch': (a) => a.skill_id || '',
  'rule.check': (a) => (a.rule_id ? `${a.rule_id}${a.findings === 0 ? ' (no findings)' : ''}` : ''),
  'subagent.start': (a) => a.agent_type || (a.agent_id ? a.agent_id.slice(0, 8) : ''),
  'subagent.stop': (a) => a.agent_type || '',
  'session.start': (a) => a.cwd || a.model || '',
  'session.end': (a) => a.reason || '',
  'environment.git_status': (a) => {
    const br = a.branch ? `[${a.branch}]` : ''
    const n = a.changed_count
    const changes = n ? `${n} change${n === 1 ? '' : 's'}` : 'clean'
    return `${br} ${changes}`.trim()
  },
  'compact.pre': (a) => {
    const tr = a.trigger ? `[${a.trigger}]` : ''
    const ci = a.custom_instructions ? ` ${_terminalTruncate(a.custom_instructions, 80)}` : ''
    return `${tr}${ci}`.trim()
  },
  'compact.post': (a) => {
    const tr = a.trigger ? `[${a.trigger}]` : ''
    const sum = a.summary ? ` summary: ${_terminalTruncate(a.summary, 70)}` : ''
    return `${tr}${sum}`.trim()
  },
  rewind: (a) => {
    const parts = []
    if (a.abandoned_prompt_count) parts.push(`${a.abandoned_prompt_count}p`)
    if (a.rolled_back_count) parts.push(`${a.rolled_back_count}f`)
    return parts.length ? `[${parts.join(', ')}]` : ''
  },
}

// Task-tool detail: subject / task-id / status are the entire signal.
function _terminalTaskDetail(n, a) {
  if (n === 'tool.TaskCreate' && a.subject) {
    return a.task_id ? `#${a.task_id}: ${a.subject}` : a.subject
  }
  if (n === 'tool.TaskUpdate' && a.task_id) {
    return a.status ? `#${a.task_id} → ${a.status}` : `#${a.task_id}`
  }
  if (n === 'tool.TaskOutput' && a.task_id) {
    return a.status ? `#${a.task_id} → ${a.status}` : `#${a.task_id}`
  }
  return undefined
}

// File / pattern tail of the tool detail (the lowest-priority attributes).
function _terminalToolFile(a) {
  if (a.pattern && a.file_path) return `${a.pattern} in ${a.file_path.split('/').pop()}`
  if (a.pattern) return a.pattern
  if (a.file_path) {
    const fname = a.file_path.split('/').pop()
    if (a.lines) return `${fname} (${a.lines} lines)`
    return fname
  }
  return ''
}

// Detail string for a `tool.*` span. Priority order preserved verbatim
// from the original SFC switch fallthrough.
function _terminalToolDetail(n, a) {
  if (a.command_preview) return a.command_preview
  const taskDetail = _terminalTaskDetail(n, a)
  if (taskDetail !== undefined) return taskDetail
  if (n === 'tool.Skill' && a.skill_name) return a.skill_name
  if (a.questions) return terminalAskQuestionPreview(a)
  const tsTools = a.loaded_tools && a.loaded_tools.length
    ? a.loaded_tools
    : a.selected_tools
  if (tsTools && tsTools.length) {
    return tsTools.map(t => t.split('__').pop()).join(', ')
  }
  if (a.query) return a.query
  if (a.url) return a.url
  return _terminalToolFile(a)
}

export function terminalSpanDetail(span) {
  const a = span.attributes || {}
  const n = span.name || ''
  // `Object.hasOwn` so an inherited prototype key (constructor/toString/…)
  // can't masquerade as a builder — matches the original `switch` falling
  // through to the tool branch / '' for any non-listed name.
  if (Object.hasOwn(_TERMINAL_DETAIL_BUILDERS, n)) return _TERMINAL_DETAIL_BUILDERS[n](a)
  if (n.startsWith('tool.')) return _terminalToolDetail(n, a)
  if (n.startsWith('pre_tool.')) return a.tool_name || ''
  return ''
}

// ── Tool group badges (sidebar token rollup) ────────────────

const _TOOL_BADGE_FS = { label: 'FS', classes: 'bg-amber-100 text-amber-800', group: 'Read / write', order: 1 }
const _TOOL_BADGE_SH = { label: 'SH', classes: 'bg-slate-200 text-slate-700', group: 'Shell', order: 2 }
const _TOOL_BADGE_AGT = { label: 'AGT', classes: 'bg-pink-100 text-pink-800', group: 'Agents & skills', order: 3 }
const _TOOL_BADGE_BG = { label: 'BG', classes: 'bg-orange-100 text-orange-800', group: 'Background tasks', order: 4 }
const _TOOL_BADGE_NET = { label: 'NET', classes: 'bg-purple-100 text-purple-800', group: 'Network', order: 5 }
const _TOOL_BADGE_MCP = { label: 'MCP', classes: 'bg-cyan-100 text-cyan-800', group: 'MCP tools', order: 6 }
const _TOOL_BADGE_AI = { label: 'AI', classes: 'bg-emerald-100 text-emerald-800', group: 'Model output', order: 7 }
const _TOOL_BADGE_TH = { label: 'TH', classes: 'bg-amber-200 text-amber-900', group: 'Thinking', order: 8 }
const _TOOL_BADGE_SYS = { label: 'SYS', classes: 'bg-slate-100 text-slate-600', group: 'System', order: 9 }

export function toolBadge(fullName) {
  if (!fullName) return _TOOL_BADGE_SYS
  if (fullName === 'assistant_text') return _TOOL_BADGE_AI
  if (fullName === 'assistant_thinking') return _TOOL_BADGE_TH
  if (fullName.startsWith('mcp__')) return _TOOL_BADGE_MCP
  if (['Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit'].includes(fullName)) {
    return _TOOL_BADGE_FS
  }
  if (fullName === 'Bash') return _TOOL_BADGE_SH
  if (['WebFetch', 'WebSearch'].includes(fullName)) return _TOOL_BADGE_NET
  if (['Agent', 'Skill', 'AskUserQuestion', 'ToolSearch'].includes(fullName)) {
    return _TOOL_BADGE_AGT
  }
  if (['TaskCreate', 'TaskUpdate', 'TaskStop', 'TaskGet', 'TaskList',
       'TaskOutput', 'ScheduleWakeup', 'CronCreate', 'CronDelete',
       'CronList'].includes(fullName)) {
    return _TOOL_BADGE_BG
  }
  return _TOOL_BADGE_SYS
}

// ── Span status / colour ────────────────────────────────────

export function dotColor(name) {
  const map = {
    'prompt': 'bg-purple-500',
    'task.notification': 'bg-amber-500',
    'assistant_response': 'bg-emerald-500',
    'skill.read': 'bg-green-500',
    'skill.invoke': 'bg-green-600',
    'file.edit': 'bg-orange-500',
    'plan.edit': 'bg-green-600',
    'rule.check': 'bg-red-500',
    'memory.recall': 'bg-fuchsia-500',
    'subagent.start': 'bg-pink-500',
    'harness.local_command': 'bg-teal-500',
    'harness.recap': 'bg-indigo-400',
    'environment.git_status': 'bg-cyan-600',
  }
  if (map[name]) return map[name]
  if (name && name.startsWith('tool.')) return 'bg-blue-500'
  return 'bg-slate-400'
}

export function isRejectedToolSpan(span) {
  return Boolean(span?.attributes?.rejected)
}

export function isDeniedToolSpan(span) {
  return Boolean(span?.attributes?.denied)
}

export function isErrorToolSpan(span) {
  return span?.name?.startsWith('tool.failure') || isRejectedToolSpan(span)
}

export function toolRowDotClass(span) {
  if (isDeniedToolSpan(span)) return 'bg-amber-400'
  if (isErrorToolSpan(span)) return 'bg-red-500'
  return dotColor(span.name)
}

export function toolRowTextClass(span) {
  if (span?.name?.startsWith('tool.failure')) return 'text-red-600'
  if (isRejectedToolSpan(span)) return 'text-red-700'
  if (isDeniedToolSpan(span)) return 'text-amber-800'
  return 'text-slate-700'
}

// ── Span category buckets (Terminal filter bar + /live filter sheet) ──
//
// ONE source for the category split, chip list, and search haystack —
// shared by SessionTerminalLog (desktop) and the /live mobile card
// (utils/liveRows.js). Moved verbatim out of SessionTerminalLog.

// File-mutating tools. They are `tool.*` spans but bucket under `edit`
// (not the generic `tool` pill) so the edit filter mirrors the Sessions
// list's Edits column. Must be checked before the tool-prefix rule.
export const EDIT_TOOL_NAMES = new Set([
  'tool.Edit', 'tool.Write', 'tool.MultiEdit', 'tool.NotebookEdit', 'tool.apply_patch',
])

// Categorize a span into one of the visible buckets (excluding 'all').
// Server-side tools (advisor today, any future ones with
// attributes.server_side) bucket separately from local tools so users
// can isolate model-to-model calls — they look like a tool span but
// cost orders of magnitude more and carry a textual reply.
export function categoryOf(span) {
  const n = span.name || ''
  if (n === 'prompt') return 'prompt'
  if (n === 'assistant_response') return 'assistant'
  if (n === 'assistant.thinking') return 'thinking'
  if (span.attributes?.server_side || n === 'tool.advisor') return 'advisor'
  if (EDIT_TOOL_NAMES.has(n)) return 'edit'
  if (n.startsWith('tool.') || n.startsWith('pre_tool.')) return 'tool'
  if (n === 'skill.read' || n === 'skill.invoke' || n === 'skill.launch') return 'skill'
  if (n === 'rule.check') return 'rule'
  return 'other'
}

// Chip palette mirrors the dot color used per row. Soft tints for the
// chip background, saturated dots — matches the sketch.
export const SPAN_CATEGORIES = [
  { id: 'all',       label: 'All',       dotClass: 'bg-slate-400' },
  { id: 'prompt',    label: 'prompt',    dotClass: 'bg-purple-500' },
  { id: 'assistant', label: 'assistant', dotClass: 'bg-emerald-500' },
  { id: 'thinking',  label: 'thinking',  dotClass: 'bg-amber-400' },
  { id: 'tool',      label: 'tool',      dotClass: 'bg-blue-500' },
  { id: 'advisor',   label: 'advisor',   dotClass: 'bg-violet-500' },
  { id: 'skill',     label: 'skill',     dotClass: 'bg-green-500' },
  { id: 'edit',      label: 'edit',      dotClass: 'bg-orange-500' },
  { id: 'rule',      label: 'rule',      dotClass: 'bg-red-500' },
  { id: 'other',     label: 'other',     dotClass: 'bg-slate-400' },
]

export function spanMatchesSearch(span, q) {
  if (!q) return true
  const a = span.attributes || {}
  const hay = [
    span.name,
    a.file_path,
    a.tool_name,
    a.command_preview,
    a.pattern,
    a.skill_id,
    a.rule_id,
    a.plan_filename,
    a.text,
    a.questions && a.questions.map(x => x.question).join(' '),
    a.answers && Object.values(a.answers).join(' '),
  ].filter(Boolean).join(' ').toLowerCase()
  return hay.includes(q.toLowerCase())
}

// ── Task row chips ──────────────────────────────────────────

// Status chip rendered before the label on task spans. TaskCreate
// implicitly puts the task into `pending`; TaskUpdate carries the new
// status in attributes. The chip lets the reader scan a column of task
// rows and tell created-vs-completed-vs-in_progress at a glance instead
// of parsing the `→ status` suffix on every row.
export function taskRowStatus(span) {
  if (span.name === 'tool.TaskCreate') return 'pending'
  if (span.name === 'tool.TaskUpdate') {
    const s = span.attributes?.status
    if (s === 'completed' || s === 'in_progress' || s === 'pending') return s
  }
  return null
}

export function taskRowIcon(status) {
  if (status === 'completed') return '☑'
  if (status === 'in_progress') return '◐'
  return '☐'
}

export function taskRowIconClass(status) {
  if (status === 'completed') return 'text-emerald-600'
  if (status === 'in_progress') return 'text-amber-600'
  return 'text-slate-400'
}

// ── Diff op labels ──────────────────────────────────────────

export function diffOpLabel(op) {
  if (op === 'write') return 'Create'
  return 'Update'
}

export function diffFileName(span) {
  const fp = span?.attributes?.file_path
  if (!fp) return ''
  const slash = fp.lastIndexOf('/')
  return slash >= 0 ? fp.slice(slash + 1) : fp
}

// ── Prompt preview (rail / collapsed prompt cards) ──────────

const PROMPT_PREVIEW_MAX_CHARS = 1800
const PROMPT_PREVIEW_MAX_LINES = 16

export function promptPreviewText(prompt) {
  const text = prompt?.attributes?.expanded_text || prompt?.attributes?.text || ''
  if (!text) return ''
  let preview = text
  if (preview.length > PROMPT_PREVIEW_MAX_CHARS) {
    preview = preview.slice(0, PROMPT_PREVIEW_MAX_CHARS).trimEnd() + '…'
  }
  const lines = preview.split('\n')
  if (lines.length > PROMPT_PREVIEW_MAX_LINES) {
    preview = lines.slice(0, PROMPT_PREVIEW_MAX_LINES).join('\n').trimEnd() + '\n…'
  }
  return preview
}

export function promptPreviewMeta(prompt) {
  const text = prompt?.attributes?.text || ''
  const imageCount = prompt?.attributes?.image_indices?.length || 0
  const imageTokens = prompt?.attributes?.image_tokens_estimate || 0
  const truncated = promptPreviewText(prompt) !== text
  return { imageCount, imageTokens, truncated }
}

// A prompt row that is still a live `promptlive-` placeholder (its real
// `prompt-<uuid>` anchor never landed) is "unresolved": the serve-time merge
// would have dropped it the moment the anchor arrived. The only such row that
// survives to the client is the single newest one — in a live session that's
// the in-flight prompt, so callers gate on the session having ended before
// treating it as stranded. A stranded placeholder is typically a scheduled /
// loop wakeup (delivered as a plain UserPromptSubmit, never anchored) or an
// interrupted final prompt — neither is a turn the user actually typed.
export function isUnresolvedPrompt(prompt) {
  return (
    prompt?.name === 'prompt' &&
    prompt?.status_code === 'PENDING' &&
    typeof prompt?.span_id === 'string' &&
    prompt.span_id.startsWith('promptlive-')
  )
}

// ── AskUserQuestion answer rendering ────────────────────────

// Options used to be stored as bare strings (the label). New traces
// store the full `{label, description, preview?}` object so the panel
// can render the description the user saw in the terminal. Helpers
// normalise both shapes.

function _askAnswer(span, q) {
  const answers = span?.attributes?.answers || {}
  return answers[q?.question]
}

export function askOptLabel(opt) {
  if (opt && typeof opt === 'object') return opt.label
  return opt
}

export function askOptDescription(opt) {
  return opt && typeof opt === 'object' ? (opt.description || '') : ''
}

export function askIsChosen(span, q, opt) {
  const ans = _askAnswer(span, q)
  if (!ans) return false
  const label = askOptLabel(opt)
  if (q?.multiSelect && Array.isArray(ans)) return ans.includes(label)
  return ans === label
}

export function askFreeText(span, q) {
  const ans = _askAnswer(span, q)
  if (!ans || typeof ans !== 'string') return ''
  const labels = (q?.options || []).map(askOptLabel)
  if (labels.includes(ans)) return ''
  return ans
}

export function askNote(span, q) {
  const ann = span?.attributes?.annotations || {}
  return ann[q?.question]?.notes || ''
}

// ── Live-span reconcile ──────────────────────────────────────

// Converge the append-only `session.spans` (which feeds the conversation cards)
// to the server's reconciliation. `mergeLoadedSpans` is an append/update-only
// keyed merge — it never removes — so a placeholder the serve-time merge has
// since dropped (a `promptlive-` prompt promoted to its anchor, or a
// `pending-`/`permreq-`/`permission.request` tool/permission placeholder
// superseded by its resolved span) lingers and renders a SECOND, duplicate
// card next to the real one. The server is the single reconciliation authority:
// the `/map?shallow=1` response lists `retired_span_ids` (PENDING rows the
// merge dropped from the live window), and we drop exactly those. A still-live
// placeholder is never in that list, so instant feedback is preserved; loaded-
// older history and `/children`-fetched spans are untouched.
export function dropRetiredSpans(spans, retiredIds) {
  const retired = retiredIds instanceof Set ? retiredIds : new Set(retiredIds || [])
  if (!retired.size) return spans || []
  return (spans || []).filter(s => !retired.has(s?.span_id))
}
