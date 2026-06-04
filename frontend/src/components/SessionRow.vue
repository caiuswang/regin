<script setup>
// Desktop session-table row, extracted verbatim from SessionsView's
// `<tr v-for>` to keep that view under the Vue template-complexity
// thresholds. Selection + delete state stays in the parent: this row
// receives `selected`/`isDeleting` as props and emits `toggle`/`delete`.
//
// The formatting helpers below are copied verbatim from SessionsView so
// the rendered output is byte-identical. They intentionally do NOT use
// the shared traceFormatters `fmtDuration`/`fmtAgo` — those have a
// different output shape (e.g. "1m05s" vs this view's "1m5s"), so reusing
// them would change what the row renders.
import { fmtTokens } from '../utils/traceFormatters.js'

defineProps({
  s: { type: Object, required: true },
  selected: { type: Boolean, default: false },
  isDeleting: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle', 'delete'])

function onToggle(e) {
  emit('toggle', e.target.checked)
}

const STALE_FALLBACK_WINDOW_MS = 10 * 60 * 1000

function parseLocalIso(iso) {
  if (!iso) return null
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?/)
  if (!m) return new Date(iso)
  const ms = m[7] ? parseInt(m[7].slice(0, 3).padEnd(3, '0'), 10) : 0
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6], ms)
}

function isActive(s) {
  if (s.status === 'active') return true
  if (s.status === 'ended') return false
  const d = parseLocalIso(s.last_seen)
  if (!d) return false
  const age = Date.now() - d.getTime()
  return age >= 0 && age < STALE_FALLBACK_WINDOW_MS
}

function titlePreview(title) {
  if (!title) return ''
  const firstLine = title.split('\n')[0].trim()
  return firstLine.length > 70 ? firstLine.slice(0, 70) + '…' : firstLine
}

function shortTestName(nodeid) {
  if (!nodeid) return ''
  const idx = nodeid.indexOf('::')
  return idx >= 0 ? nodeid.slice(idx + 2) : nodeid
}

function fmtDate(iso) {
  const d = parseLocalIso(iso)
  if (!d) return '-'
  return d.toLocaleString()
}

function fmtDuration(ms) {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`

  const seconds = Math.floor(ms / 1000) % 60
  const minutes = Math.floor(ms / 60000) % 60
  const hours = Math.floor(ms / 3600000) % 24
  const days = Math.floor(ms / 86400000)

  const units = [
    { value: days, label: 'd' },
    { value: hours, label: 'h' },
    { value: minutes, label: 'm' },
    { value: seconds, label: 's' },
  ]

  const start = units.findIndex(u => u.value > 0)
  if (start === -1) return '-'

  let end = units.length - 1
  while (end > start && units[end].value === 0) {
    end--
  }

  return units.slice(start, end + 1).map(u => `${u.value}${u.label}`).join('')
}

function contextBadgeClass(pct) {
  if (pct == null) return 'bg-gray-100 text-gray-500 border-gray-200'
  if (pct >= 80) return 'bg-red-50 text-red-700 border-red-200'
  if (pct >= 50) return 'bg-amber-50 text-amber-700 border-amber-200'
  return 'bg-green-50 text-green-700 border-green-200'
}

function agentTypeLabel(s) {
  if (s.origin === 'workflow') return 'Workflow run'
  if (s.agent_kind === 'claude') return 'Claude Code session'
  if (s.agent_kind === 'codex') return 'OpenAI Codex session'
  return s.agent_type ? `Agent session: ${s.agent_type}` : 'Agent session'
}

function agentTypeClass(s) {
  if (s.origin === 'workflow') return 'agent-icon--workflow'
  if (s.agent_kind === 'claude') return 'agent-icon--claude'
  if (s.agent_kind === 'codex') return 'agent-icon--codex'
  return 'agent-icon--generic'
}

function totalMs(s) {
  const a = parseLocalIso(s.started_at)
  const b = parseLocalIso(s.last_seen)
  if (!a || !b) return 0
  return b.getTime() - a.getTime()
}

// Relative "time ago" for the Last seen column. Absolute started/last-seen
// stay available via the cell's title tooltip (timeTitle).
function fmtRelative(iso) {
  const d = parseLocalIso(iso)
  if (!d) return '-'
  const diff = Date.now() - d.getTime()
  if (diff < 0) return 'just now'
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day}d ago`
  const mo = Math.floor(day / 30)
  if (mo < 12) return `${mo}mo ago`
  return `${Math.floor(mo / 12)}y ago`
}

function timeTitle(s) {
  return `Started ${fmtDate(s.started_at)}\nLast seen ${fmtDate(s.last_seen)}`
}

// The 7 per-session counts collapse to one Activity cell: Spans + Edits stay
// visible; the rest fold behind a "+N more" hint whose tooltip enumerates the
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

function activityMoreLabel(s) {
  const n = foldedNonzero(s).length
  return n ? `+${n} more` : ''
}

function activityMoreTitle(s) {
  const parts = foldedNonzero(s).map(m => `${s[m.key]} ${m.label}`)
  return parts.length ? parts.join(' · ') : 'no other activity'
}
</script>

<template>
  <tr :class="{ 'tbl-row-active': selected }">
    <td class="w-6" @click.stop>
      <input
        type="checkbox"
        class="h-4 w-4 align-middle focus-visible:outline-2 focus-visible:outline-blue-500"
        :checked="selected"
        @change="onToggle"
        :aria-label="`Select session ${s.trace_id.slice(0, 8)}`"
      />
    </td>
    <td class="whitespace-nowrap">
      <div class="inline-flex items-center gap-2 align-middle">
        <span
          class="agent-icon"
          :class="agentTypeClass(s)"
          :title="agentTypeLabel(s)"
          :aria-label="agentTypeLabel(s)"
          role="img"
        >
          <svg v-if="s.origin === 'workflow'" viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="3.2" cy="8" r="1.5" />
            <path d="M4.7 8h2.8M7.5 4v8M7.5 4h3M7.5 8h3M7.5 12h3" />
            <circle cx="12" cy="4" r="1.3" />
            <circle cx="12" cy="8" r="1.3" />
            <circle cx="12" cy="12" r="1.3" />
          </svg>
          <svg v-else-if="s.agent_kind === 'claude'" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 2.2 9.5 6.5 13.8 8 9.5 9.5 8 13.8 6.5 9.5 2.2 8 6.5 6.5 8 2.2Z" />
          </svg>
          <svg v-else-if="s.agent_kind === 'codex'" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M3 3.5h10a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Zm2.2 3L7 8 5.2 9.5M8.2 10h3" />
          </svg>
          <svg v-else viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 2.5a5.5 5.5 0 1 1 0 11 5.5 5.5 0 0 1 0-11Zm0 2.2v3.1l2.2 2.1" />
          </svg>
        </span>
        <router-link :to="`/trace/sessions/${s.trace_id}`" class="table-link">
          <code class="cell-code">{{ s.trace_id.slice(0, 12) }}…</code>
        </router-link>
      </div>
      <span
        v-if="isActive(s)"
        class="ml-2 inline-flex items-center gap-1 rounded bg-green-100 text-green-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide"
        :title="s.status === 'active' ? 'SessionStart fired without a matching SessionEnd' : 'No explicit lifecycle — last span within 10 minutes'"
      >
        <span class="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></span>
        active
      </span>
      <span
        v-else-if="s.status === 'ended'"
        class="ml-2 inline-block rounded bg-gray-100 text-gray-600 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide"
        :title="s.ended_reason ? `reason: ${s.ended_reason}` : 'SessionEnd fired'"
      >
        ended
      </span>
      <template v-if="s.is_test">
        <span
          class="ml-2 inline-block rounded bg-amber-100 text-amber-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide"
          title="Span attributes carry is_test=true"
        >test</span>
        <span
          v-if="s.test_name"
          class="ml-1 text-xs text-gray-600 font-mono"
          :title="s.test_name"
        >{{ shortTestName(s.test_name) }}</span>
      </template>
      <span
        v-if="s.origin === 'workflow'"
        class="ml-2 inline-block rounded bg-teal-100 text-teal-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide"
        title="captured dynamic-workflow run"
      >workflow</span>
    </td>
    <td class="col-title">
      <span v-if="s.title" class="block truncate text-sm text-gray-800" :title="s.title">{{ titlePreview(s.title) }}</span>
      <span v-else class="text-gray-400 italic text-xs">no prompt</span>
    </td>
    <td class="whitespace-nowrap">
      <template v-if="s.repos && s.repos.length">
        <span
          class="inline-flex items-center rounded-md bg-slate-100 text-slate-600 text-[11px] px-2 py-0.5"
          :title="s.cwd || (s.primary_repo || s.repos[0].name)"
        >{{ s.primary_repo || s.repos[0].name }}</span>
        <span
          v-if="s.is_multi_repo"
          class="ml-1 text-[11px] text-slate-400"
          :title="`Also touched: ${s.repos.filter(r => !r.is_primary).map(r => r.name).join(', ')}`"
        >+{{ s.repos.length - 1 }}</span>
      </template>
      <span v-else class="text-gray-300 text-xs" title="No registered repo matched">-</span>
    </td>
    <td class="whitespace-nowrap">
      <div class="text-[13px] text-slate-800">
        {{ s.span_count }} <span class="text-slate-400">spans</span>
        <span class="text-slate-300 px-0.5">·</span>
        {{ s.file_edits }} <span class="text-slate-400">edits</span>
      </div>
      <div
        v-if="activityMoreLabel(s)"
        class="mt-0.5 text-[11px] text-slate-400 cursor-help"
        :title="activityMoreTitle(s)"
      >{{ activityMoreLabel(s) }}</div>
    </td>
    <td class="whitespace-nowrap">
      <span
        v-if="s.context_pct != null"
        class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[11px] font-medium"
        :class="contextBadgeClass(s.context_pct)"
        :title="`main-conversation peak ${fmtTokens(s.peak_main_context_tokens || s.peak_context_tokens)} / ${fmtTokens(s.context_window_tokens)} tokens`"
      >{{ s.context_pct }}%
        <span class="text-[10px] opacity-70">({{ fmtTokens(s.peak_main_context_tokens || s.peak_context_tokens) }} / {{ fmtTokens(s.context_window_tokens) }})</span>
      </span>
      <span v-else class="text-gray-300 text-xs">-</span>
    </td>
    <td class="text-gray-500 text-xs whitespace-nowrap">
      {{ fmtDuration(totalMs(s)) }}
      <template v-if="s.active_work_ms != null">
        <span class="text-gray-400">/</span>
        <span :title="`agent work time${s.active_pct != null ? ` (${s.active_pct}%)` : ''}, idle ${fmtDuration(s.idle_ms)} excluded`">{{ fmtDuration(s.active_work_ms) }}</span>
      </template>
    </td>
    <td class="text-gray-400 text-xs whitespace-nowrap" :title="timeTitle(s)">{{ fmtRelative(s.last_seen) }}</td>
    <td class="text-right">
      <button
        type="button"
        class="row-delete text-xs text-red-600 hover:text-red-800 hover:underline disabled:opacity-50 disabled:cursor-wait focus-visible:outline-2 focus-visible:outline-blue-500"
        :disabled="isDeleting"
        @click="emit('delete', s)"
        :title="`Delete session ${s.trace_id.slice(0, 12)}… and all its trace data`"
      >{{ isDeleting ? 'Deleting…' : 'Delete' }}</button>
    </td>
  </tr>
</template>

<style scoped>
/* Single-table session list. Title is the lead column and truncates to a
 * fixed cap so the metric columns to its right stay aligned. Slightly tighter
 * horizontal padding than the global .tbl keeps all 9 columns on screen
 * without a horizontal scrollbar at common laptop widths. */
/* Keyed off the parent table's `.sessions-tbl` ancestor so these rules keep
 * the same specificity (0,2,1) the original SFC relied on to beat the global
 * `.tbl td` padding — a bare `td` here would tie the global and depend on CSS
 * source order. The descendant combinator works across the scope boundary:
 * `.sessions-tbl` is the parent's <table> (still in the DOM) and the child's
 * data-v attribute lands on the <td>/<tr> it renders. */
.sessions-tbl td {
  padding-left: 0.625rem;
  padding-right: 0.625rem;
}
.sessions-tbl .col-title {
  max-width: 16rem;
  min-width: 11rem;
}

/* Delete stays out of the way until the row is hovered or keyboard focus
 * lands inside it, so it never competes with the row content. */
.sessions-tbl .row-delete {
  opacity: 0;
  transition: opacity 0.12s ease;
}
.sessions-tbl tbody tr:hover .row-delete,
.sessions-tbl tbody tr:focus-within .row-delete {
  opacity: 1;
}
@media (hover: none) {
  /* Touch / non-hover pointers can't reveal on hover — keep it visible. */
  .sessions-tbl .row-delete { opacity: 1; }
}
.agent-icon {
  align-items: center;
  border: 1px solid currentColor;
  border-radius: 9999px;
  display: inline-flex;
  flex: 0 0 auto;
  height: 1.25rem;
  justify-content: center;
  width: 1.25rem;
}
.agent-icon svg {
  fill: none;
  height: 0.875rem;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.6;
  width: 0.875rem;
}
.agent-icon--claude {
  background: #fff7ed;
  color: #c2410c;
}
.agent-icon--codex {
  background: #eef2ff;
  color: #4338ca;
}
.agent-icon--generic {
  background: #f3f4f6;
  color: #4b5563;
}
.agent-icon--workflow {
  background: #ecfdf5;
  color: #0f766e;
}
</style>
