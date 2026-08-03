// Presentation helpers shared by every session-list surface (grid row, mobile
// card, group headers). Kept out of the SFCs so each stays under the Vue
// complexity thresholds and so the desktop and mobile renderings can never
// disagree about what a row *says*.
import { parseLocalIso } from './sessionActivity.js'

export function titlePreview(title, cap = 70) {
  if (!title) return ''
  const firstLine = title.split('\n')[0].trim()
  return firstLine.length > cap ? firstLine.slice(0, cap) + '…' : firstLine
}

export function shortTestName(nodeid) {
  if (!nodeid) return ''
  const idx = nodeid.indexOf('::')
  return idx >= 0 ? nodeid.slice(idx + 2) : nodeid
}

export function fmtDate(iso) {
  const d = parseLocalIso(iso)
  return d ? d.toLocaleString() : '-'
}

const DURATION_UNITS = [
  { ms: 86400000, label: 'd' },
  { ms: 3600000, label: 'h' },
  { ms: 60000, label: 'm' },
  { ms: 1000, label: 's' },
]

export function fmtDuration(ms) {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`
  const parts = DURATION_UNITS.map((u, i) => ({
    label: u.label,
    value: i === 0 ? Math.floor(ms / u.ms) : Math.floor(ms / u.ms) % (i === 1 ? 24 : 60),
  }))
  const start = parts.findIndex(p => p.value > 0)
  if (start === -1) return '-'
  let end = parts.length - 1
  while (end > start && parts[end].value === 0) end--
  return parts.slice(start, end + 1).map(p => `${p.value}${p.label}`).join('')
}

export function totalMs(s) {
  const a = parseLocalIso(s.started_at)
  const b = parseLocalIso(s.last_seen)
  return a && b ? b.getTime() - a.getTime() : 0
}

export function timeTitle(s) {
  return `Started ${fmtDate(s.started_at)}\nLast seen ${fmtDate(s.last_seen)}`
}

// The context meter's fill colour tracks headroom, not identity: green under
// half, amber past half, red past 80% — the point where compaction is near.
export function contextTone(pct) {
  if (pct == null) return 'none'
  if (pct >= 80) return 'danger'
  if (pct >= 50) return 'warn'
  return 'ok'
}

// The 7 per-session counts collapse to one Activity cell: spans + edits stay
// visible, the rest fold behind a "+N more" hint whose tooltip enumerates the
// non-zero ones.
const FOLDED_METRICS = [
  { key: 'tool_calls', label: 'tools' },
  { key: 'skill_reads', label: 'reads' },
  { key: 'rule_checks', label: 'rules' },
  { key: 'plans', label: 'plans' },
  { key: 'prompts', label: 'prompts' },
]

function foldedNonzero(s) {
  return FOLDED_METRICS.filter(m => (s[m.key] || 0) > 0)
}

export function activityMoreLabel(s) {
  const n = foldedNonzero(s).length
  return n ? `+${n} more` : ''
}

export function activityMoreTitle(s) {
  const parts = foldedNonzero(s).map(m => `${s[m.key]} ${m.label}`)
  return parts.length ? parts.join(' · ') : 'no other activity'
}

// One presentation entry per non-interactive run origin (the origins the
// server's workflow=hide toggle filters). Adding a run origin means one entry
// here plus its icon branch in SessionAgentIcon.
const RUN_ORIGIN_META = {
  workflow: { label: 'Workflow run', tone: 'workflow' },
  'llm-stage': { label: 'LLM stage', tone: 'llm-stage' },
}

const AGENT_KIND_LABEL = {
  claude: 'Claude Code session',
  codex: 'OpenAI Codex session',
  kimi: 'Kimi Code session',
}

export function agentTypeLabel(s) {
  const run = RUN_ORIGIN_META[s.origin]
  if (run) return run.label
  return AGENT_KIND_LABEL[s.agent_kind]
    || (s.agent_type ? `Agent session: ${s.agent_type}` : 'Agent session')
}

export function agentTypeTone(s) {
  const run = RUN_ORIGIN_META[s.origin]
  if (run) return run.tone
  return AGENT_KIND_LABEL[s.agent_kind] ? s.agent_kind : 'generic'
}

export function primaryRepo(s) {
  if (!s.repos || !s.repos.length) return null
  return s.primary_repo || s.repos[0].name
}

export function otherRepoTitle(s) {
  return `Also touched: ${(s.repos || []).filter(r => !r.is_primary).map(r => r.name).join(', ')}`
}
