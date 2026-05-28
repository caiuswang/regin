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

export function fmtTime(iso) {
  if (!iso) return '--:--:--'
  const d = new Date(iso)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

export function fmtClock(iso) {
  if (!iso) return '--:--:--'
  const d = new Date(iso)
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

// Relative "time ago" for a timestamp. Coarse buckets (just now / Nm /
// Nh / Nd) up to a week, then a plain Y-M-D date. Empty string for
// missing/unparseable input so callers can render nothing.
export function fmtAgo(iso) {
  if (!iso) return ''
  const then = Date.parse(iso)
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

export function fullLabel(span) {
  const a = span.attributes || {}
  const name = span.name || ''
  if (name === 'prompt') return a.text || 'prompt'
  if (name === 'task.notification') return a.summary || 'background task'
  if (name === 'assistant_response') return a.text || 'response'
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
    if (name === 'tool.TaskCreate' && a.subject) {
      return a.task_id ? `${tool} #${a.task_id}: ${a.subject}` : `${tool}: ${a.subject}`
    }
    if (name === 'tool.TaskUpdate' && a.task_id) {
      return a.status ? `${tool} #${a.task_id} → ${a.status}` : `${tool} #${a.task_id}`
    }
    if (name === 'tool.TaskOutput' && a.task_id) {
      return a.status ? `${tool} #${a.task_id} → ${a.status}` : `${tool} #${a.task_id}`
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
    case 'subagent.start': return `subagent: ${a.agent_type || ''}`
    case 'subagent.stop': return 'subagent done'
  }
  return name
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
    'subagent.start': 'bg-pink-500',
    'harness.local_command': 'bg-teal-500',
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
  const text = prompt?.attributes?.text || ''
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
