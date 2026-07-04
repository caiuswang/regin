<script setup>
// Session metadata header for the trace view: title (+ source badge, expand),
// the stat/pivot chip row (spans, duration, active%, context%, cache, plans,
// workflow runs, tasks), the expandable plan/workflow/task lists, the
// view-mode toggle, and the reload control.
//
// Self-contained by design: it takes the raw session + collections + the few
// spans-derived stats it can't compute alone (traceDuration, activeWorkMs,
// snapshotStaleAt, workflowParentTo), and derives everything else (title,
// task summary, idle/active%) internally. It owns only presentational toggle
// state and emits intent (`reload`, `jump-to-task`, `update:viewMode`) back to
// the parent, which still owns the data model.
import { ref, computed } from 'vue'
import { fmtTokens } from '../utils/traceFormatters.js'
import Button from './ui/Button.vue'

// Date/duration formatters kept local (exact copies of SessionTraceView's)
// rather than imported: traceFormatters exposes differently-behaved variants
// (fmtTime as HH:MM, a simpler fmtDuration), so importing them would silently
// change how the snapshot time, durations, plan dates, and the "updated" clock
// render here. Unifying the sibling copies is a separate follow-up.
function fmtTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleTimeString() + '.' + String(d.getMilliseconds()).padStart(3, '0')
}
function fmtDate(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString()
}
function fmtLocalClock(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
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

const props = defineProps({
  session: { type: Object, required: true },
  plans: { type: Array, default: () => [] },
  workflowRuns: { type: Array, default: () => [] },
  viewMode: { type: String, default: 'conversation' },
  reloading: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  lastReloadedAt: { type: Object, default: null },
  hasTurns: { type: Boolean, default: false },
  traceDuration: { type: Number, default: 0 },
  activeWorkMs: { type: Number, default: 0 },
  snapshotStaleAt: { type: [String, null], default: null },
  workflowParentTo: { type: [Object, null], default: null },
})

defineEmits(['update:viewMode', 'reload', 'jump-to-task'])

// Presentational toggle state — lives with the header, not the data model.
const sessionTitleExpanded = ref(false)
const plansExpanded = ref(false)
const workflowRunsExpanded = ref(false)
const tasksExpanded = ref(false)

// Long titles wrap the h1 — keep the visible string under control so the
// page header stays compact. Tooltip on the h1 shows the full text.
const SESSION_TITLE_MAX = 90
const SESSION_TITLE_PROMPT_MAX = 72
const sessionTitleRaw = computed(() => (
  (props.session?.title || '').replace(/\s+/g, ' ').trim()
))
const sessionTitleNeedsExpand = computed(() => {
  const t = sessionTitleRaw.value
  if (!t) return false
  const max = props.session?.title_source === 'first_prompt'
    ? SESSION_TITLE_PROMPT_MAX
    : SESSION_TITLE_MAX
  return t.length > max
})
const sessionTitle = computed(() => {
  const t = sessionTitleRaw.value
  if (!t) return 'Session timeline'
  if (sessionTitleExpanded.value || !sessionTitleNeedsExpand.value) return t
  const max = props.session?.title_source === 'first_prompt'
    ? SESSION_TITLE_PROMPT_MAX
    : SESSION_TITLE_MAX
  return t.slice(0, max) + '…'
})

const idleMs = computed(() => Math.max(0, props.traceDuration - props.activeWorkMs))
const activePct = computed(() => {
  if (!props.traceDuration) return null
  return (props.activeWorkMs / props.traceDuration) * 100
})

// Pre-compaction high-water mark. The headline ctx% is the *live* peak
// (since the last /compact); when the session compacted, the all-time
// main peak sat higher — surface it as a muted "peaked X%" chip so the
// drop is legible rather than looking like lost data. Null when no
// compaction reclaimed context (peaks coincide).
const contextPeakPct = computed(() => {
  const s = props.session
  const win = s?.context_window_tokens
  const peak = s?.peak_main_context_tokens ?? s?.peak_context_tokens
  const live = s?.live_context_tokens
  if (!win || win <= 0 || !Number.isFinite(peak) || !Number.isFinite(live)) return null
  if (peak - live <= 0) return null
  return Math.round(peak * 1000.0 / win) / 10
})

// "+sub" chip: the all-inclusive peak exceeds the main peak because an
// advisor / server-side sub-call rolled its tokens into a parent turn's
// usage. Compare the two all-time peaks directly (>1% of window) — NOT
// the headline ctx%, which now tracks the post-compaction live peak and
// would otherwise make every compacted session look like advisor spill.
const contextSubDiverges = computed(() => {
  const s = props.session
  const win = s?.context_window_tokens
  const full = s?.peak_context_tokens
  const main = s?.peak_main_context_tokens
  if (!win || win <= 0 || !Number.isFinite(full) || !Number.isFinite(main)) return false
  return (full - main) > win * 0.01
})

// Tasks summary for the header badge: counts of every status across the
// session's final task-list snapshot.
const taskSummary = computed(() => {
  const tasks = props.session?.task_list?.final
  if (!Array.isArray(tasks) || !tasks.length) return null
  let completed = 0
  let inProgress = 0
  let pending = 0
  for (const t of tasks) {
    if (t.status === 'completed') completed++
    else if (t.status === 'in_progress') inProgress++
    else pending++
  }
  return { total: tasks.length, completed, inProgress, pending }
})

function titleSourceLabel(src) {
  if (src === 'claude_ai_title') return 'auto'
  if (src === 'user_rename') return 'renamed'
  if (src === 'first_prompt') return 'prompt'
  if (src === 'workflow_name') return 'workflow'
  if (src === 'user') return 'user'
  return src
}
function titleSourceTooltip(src) {
  if (src === 'claude_ai_title') return 'Auto-generated by Claude (the `ai-title` line in the transcript). Updated when the topic pivots.'
  if (src === 'user_rename') return 'You renamed this session in Claude (the `/rename` command writes a `custom-title` line). Sticky against Claude’s auto-titles.'
  if (src === 'first_prompt') return 'Derived from the first user prompt — Claude has not posted an ai-title yet.'
  if (src === 'workflow_name') return 'The workflow’s name (`meta.name` from its script) — the canonical identifier for a dynamic-workflow run. Its objective is shown as the opening bubble.'
  if (src === 'user') return 'Manually set via the regin API; not overwritten by Claude.'
  return src
}

function contextBadgeClass(pct) {
  if (pct == null) return 'bg-gray-100 text-gray-500 border-gray-200'
  if (pct >= 80) return 'bg-red-50 text-red-700 border-red-200'
  if (pct >= 50) return 'bg-amber-50 text-amber-700 border-amber-200'
  return 'bg-green-50 text-green-700 border-green-200'
}
</script>

<template>
  <header class="flex items-start justify-between gap-4 flex-wrap mb-5">
    <div class="min-w-0 flex-1">
      <div class="text-[11px] tracking-widest uppercase text-slate-400 font-semibold mb-1">
        Observability · Session
      </div>
      <h1
        class="text-2xl font-semibold text-slate-900 leading-tight m-0 break-words"
        :title="session.title || ''"
      >{{ sessionTitle }}<span
        v-if="session.title && session.title_source"
        class="ml-2 align-middle inline-block rounded border border-slate-200 bg-slate-50 text-slate-500 text-[10px] font-medium px-1.5 py-0.5 uppercase tracking-wide"
        :title="titleSourceTooltip(session.title_source)"
      >{{ titleSourceLabel(session.title_source) }}</span></h1>
      <div
        v-if="sessionTitleNeedsExpand"
        class="mt-1.5"
      >
        <Button
          variant="link"
          size="sm"
          @click="sessionTitleExpanded = !sessionTitleExpanded"
        >
          {{ sessionTitleExpanded ? 'Collapse title' : 'Show full title' }}
        </Button>
      </div>
      <p class="mt-1.5 flex items-center flex-wrap gap-x-2 gap-y-1 text-xs text-slate-500 m-0">
        <code class="font-mono text-[11px] text-slate-600 bg-slate-100 px-1.5 py-0.5 rounded">{{ session.trace_id }}</code>
        <!-- Stale-snapshot badge: the run resumed past the manifest snapshot,
             which the runtime only flushes at pause/completion — so phases
             and counts here are frozen and can't refresh live. -->
        <span
          v-if="snapshotStaleAt"
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-amber-300 bg-amber-50 text-[11px] font-semibold text-amber-700"
          :title="`This run resumed and is still in flight. The Workflow runtime writes its progress snapshot only at pause/completion, so the phases and agent counts shown are frozen as of ${fmtTime(snapshotStaleAt)} and can't be refreshed live from disk. Pause the run (or let it finish) to update.`"
        >⏸ snapshot as of {{ fmtTime(snapshotStaleAt) }} · pause to refresh</span>
        <!-- No ⚙ workflow-name chip here: the session title already *is*
             the workflow name (title_source=workflow_name), so a chip
             repeating it would be redundant. The backlink below stays. -->
        <router-link
          v-if="workflowParentTo"
          :to="workflowParentTo"
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-slate-300 bg-white text-[11px] font-medium text-slate-600 hover:bg-slate-50 no-underline focus-visible:outline-2 focus-visible:outline-blue-500"
          title="Open the Claude Code session that launched this workflow run"
        >↑ launched from session</router-link>
        <span class="text-slate-300">·</span>
        <span>{{ session.span_count_total ?? session.spans.length }} spans</span>
        <span class="text-slate-300">·</span>
        <span :title="`wall-clock from first to last span — includes user-idle gaps between turns`">
          duration <span class="font-mono">{{ fmtDuration(Math.round(traceDuration)) }}</span>
        </span>
        <template v-if="activeWorkMs > 0">
          <span class="text-slate-300">·</span>
          <span :title="`union of root-span intervals (overlaps merged) — agent work time, idle ${fmtDuration(idleMs)} excluded`">
            active <span class="font-mono">{{ activePct != null ? Math.round(activePct) + '%' : fmtDuration(activeWorkMs) }}</span>
          </span>
        </template>
        <template v-if="session.context_pct != null">
          <!-- Headline is the live main-conversation peak: the high-water
               mark since the last /compact (matches the terminal, which
               resets on compaction). -->
          <span
            class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] font-medium ml-1"
            :class="contextBadgeClass(session.context_pct)"
            :title="`live context peak (since last /compact): ${(session.live_context_tokens ?? session.peak_main_context_tokens ?? session.peak_context_tokens) || 0} / ${session.context_window_tokens} tokens`"
          >ctx {{ session.context_pct }}%
            <span class="opacity-75 font-mono">{{ fmtTokens(session.live_context_tokens ?? session.peak_main_context_tokens ?? session.peak_context_tokens) }} / {{ fmtTokens(session.context_window_tokens) }}</span>
          </span>
          <!-- Pre-compaction high-water mark, shown only when a /compact
               freed context so the headline drop reads as a reset, not
               missing data. -->
          <span
            v-if="contextPeakPct != null"
            class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] font-medium border-slate-200 bg-slate-50 text-slate-500"
            :title="`peaked at ${(session.peak_main_context_tokens || session.peak_context_tokens || 0).toLocaleString()} tokens (${contextPeakPct}% of window) before /compact; the headline ctx% tracks the live context since the most recent compaction.`"
          >peaked {{ contextPeakPct }}%</span>
          <!-- All-inclusive peak only when it diverges (advisor turns).
               Shown as an absolute token count, not a % of window: it
               can exceed the window (server-side sub-call tokens are
               summed into one turn's bill), so a percentage reads as
               broken. -->
          <span
            v-if="contextSubDiverges"
            class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] font-medium border-slate-200 bg-slate-50 text-slate-500"
            :title="`all-inclusive peak turn: ${(session.peak_context_tokens || 0).toLocaleString()} tokens (vs ${(session.context_window_tokens || 0).toLocaleString()} window). Includes advisor/server-side sub-call tokens that Anthropic rolls into the parent turn's usage, so it can exceed the window — the headline ctx% excludes these.`"
          >+sub <span class="opacity-75 font-mono">{{ fmtTokens(session.peak_context_tokens) }}</span></span>
        </template>
        <!-- Cache: read = context replayed each turn (the bulk of the API
             bill), write = cache creation. Not attributable per-tool, so
             it lives here rather than in the Tokens-by-tool rollup. -->
        <span
          v-if="session.cache_read_tokens || session.cache_creation_tokens"
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-slate-200 bg-slate-50 text-slate-500 text-[11px] font-medium ml-1"
          :title="`cache read (context replayed each turn): ${(session.cache_read_tokens || 0).toLocaleString()} tokens\ncache write (cache creation): ${(session.cache_creation_tokens || 0).toLocaleString()} tokens\n\nCache is a per-request cost, not attributable to a single tool — so it isn't in 'Tokens by tool', but it dominates the full session bill.`"
        >cache <span class="opacity-75 font-mono">{{ fmtTokens(session.cache_read_tokens) }} r · {{ fmtTokens(session.cache_creation_tokens) }} w</span></span>
        <!-- Workflow runs have no single context window (no ctx% chip), so
             surface the run's authoritative grand total (manifest
             totalTokens — input + cache + output across all agents). -->
        <template v-if="session.total_tokens">
          <span
            class="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-slate-200 bg-slate-50 text-slate-600 text-[11px] font-medium ml-1"
            title="total tokens across all workflow agents (input + cache + output), from the run manifest"
          >Σ <span class="opacity-75 font-mono">{{ fmtTokens(session.total_tokens) }}</span> tokens</span>
        </template>
        <template v-if="session.model">
          <span class="text-xs text-slate-500 font-mono ml-1">{{ session.model }}</span>
        </template>
        <!-- Plan chips: each PlanSession row this session authored or
             edited (from `plan_sessions`) lets the reader pivot
             session → plan from the header. N=1 renders inline as a
             direct link; N≥2 collapses to a `plans N` chip with
             click-to-expand, matching the tasks summary just above
             so the two summaries look and behave the same. -->
        <template v-if="plans.length === 1">
          <span class="text-slate-300">·</span>
          <router-link
            :to="`/plans/${encodeURIComponent(plans[0].plan_filename)}`"
            class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] font-medium border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100 no-underline focus-visible:outline-2 focus-visible:outline-blue-500"
            :title="`plan: ${plans[0].plan_filename}`"
          >plan
            <span class="font-mono opacity-80 truncate max-w-[14rem]">{{ plans[0].plan_filename }}</span>
          </router-link>
        </template>
        <template v-else-if="plans.length > 1">
          <span class="text-slate-300">·</span>
          <span
            class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] font-medium ml-0 cursor-pointer select-none border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100"
            :title="'plan files this session authored or edited — click to expand'"
            @click="plansExpanded = !plansExpanded"
          >plans {{ plans.length }}
            <span class="opacity-60 ml-0.5">{{ plansExpanded ? '▾' : '▸' }}</span>
          </span>
        </template>
        <!-- Workflow run chips: dynamic-workflow runs this session
             launched, so the reader can pivot session → run from the
             header. Mirrors the plan chips: N=1 inlines as `⚙ <name>`;
             N≥2 collapses to `workflows N` with click-to-expand. -->
        <template v-if="workflowRuns.length === 1">
          <span class="text-slate-300">·</span>
          <router-link
            :to="`/trace/sessions/${workflowRuns[0].run_id}`"
            class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-medium border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 no-underline focus-visible:outline-2 focus-visible:outline-emerald-500"
            :title="`workflow run: ${workflowRuns[0].name || workflowRuns[0].run_id}`"
          >⚙ <span class="truncate max-w-[14rem]">{{ workflowRuns[0].name || 'workflow run' }}</span></router-link>
        </template>
        <template v-else-if="workflowRuns.length > 1">
          <span class="text-slate-300">·</span>
          <span
            class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-medium cursor-pointer select-none border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
            :title="'dynamic-workflow runs launched from this session — click to expand'"
            @click="workflowRunsExpanded = !workflowRunsExpanded"
          >⚙ workflows {{ workflowRuns.length }}
            <span class="opacity-60 ml-0.5">{{ workflowRunsExpanded ? '▾' : '▸' }}</span>
          </span>
        </template>
        <!-- Tasks summary badge: shows the final task-list state
             across the whole session so the reader doesn't have to
             scroll the spine to find it. Click to expand the full
             list inline. -->
        <template v-if="taskSummary">
          <span
            class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] font-medium ml-1 cursor-pointer select-none border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100"
            :title="'session task list — click to expand'"
            @click="tasksExpanded = !tasksExpanded"
          >tasks {{ taskSummary.total }}
            <span class="opacity-75 font-mono">{{ taskSummary.completed }}☑ · {{ taskSummary.inProgress }}◐ · {{ taskSummary.pending }}☐</span>
            <span class="opacity-60 ml-0.5">{{ tasksExpanded ? '▾' : '▸' }}</span>
          </span>
        </template>
      </p>
      <!-- Expanded plans list (mirrors the tasks pattern below).
           Each row is a router-link to /plans/<filename>, so the
           reader can pivot to any of the session's plan files
           without scrolling or hunting in the spine. -->
      <div
        v-if="plans.length > 1 && plansExpanded"
        class="mt-2 rounded-md border border-blue-200 bg-blue-50/50 px-3 py-2 max-w-2xl"
      >
        <ul class="text-[13px] text-slate-800 leading-snug">
          <li
            v-for="p in plans"
            :key="p.id"
            class="flex items-baseline gap-2 py-0.5"
          >
            <router-link
              :to="`/plans/${encodeURIComponent(p.plan_filename)}`"
              class="font-mono text-[12px] text-blue-700 hover:text-blue-900 hover:underline focus-visible:outline-2 focus-visible:outline-blue-500 break-all"
              :title="p.plan_filename"
            >{{ p.plan_filename }}</router-link>
            <span class="text-slate-400 text-[11px] font-mono shrink-0">
              {{ fmtDate(p.started_at) }}<span v-if="p.ended_at"> – {{ fmtDate(p.ended_at) }}</span>
            </span>
          </li>
        </ul>
      </div>
      <!-- Expanded workflow runs list (mirrors the plans list). Each
           row links to the run's captured trace. -->
      <div
        v-if="workflowRuns.length > 1 && workflowRunsExpanded"
        class="mt-2 rounded-md border border-emerald-200 bg-emerald-50/50 px-3 py-2 max-w-2xl"
      >
        <ul class="text-[13px] text-slate-800 leading-snug">
          <li
            v-for="r in workflowRuns"
            :key="r.run_id"
            class="flex items-baseline gap-2 py-0.5"
          >
            <router-link
              :to="`/trace/sessions/${r.run_id}`"
              class="text-emerald-700 hover:text-emerald-900 hover:underline focus-visible:outline-2 focus-visible:outline-emerald-500 break-all"
              :title="r.run_id"
            >⚙ {{ r.name || r.run_id }}</router-link>
            <span
              v-if="r.agent_count"
              class="text-slate-500 text-[11px] font-mono shrink-0"
            >{{ r.agent_count }} agent<span v-if="r.agent_count !== 1">s</span><template v-if="r.phase_count"> · {{ r.phase_count }} phase<span v-if="r.phase_count !== 1">s</span></template><template v-if="r.tokens"> · {{ fmtTokens(r.tokens) }} tok</template></span>
            <span class="text-slate-400 text-[11px] font-mono shrink-0">{{ r.run_id }}</span>
          </li>
        </ul>
      </div>
      <!-- Expanded task list (final state across the session).
           Each row is clickable: jumps the spine to that task's
           TaskCreate span and selects it, so the user can click a
           task in the summary and land on the moment it was opened
           without scrolling through hundreds of spans. -->
      <div
        v-if="taskSummary && tasksExpanded"
        class="mt-2 rounded-md border border-indigo-200 bg-indigo-50/50 px-3 py-2 max-w-2xl"
      >
        <ul class="text-[13px] text-slate-800 leading-snug">
          <li
            v-for="t in session.task_list?.final || []"
            :key="t.task_id"
            tabindex="0"
            class="flex items-baseline gap-2 rounded px-1 -mx-1 py-0.5 cursor-pointer hover:bg-indigo-100 focus-visible:outline-2 focus-visible:outline-indigo-400"
            :title="(t.current_span_id || t.created_span_id) ? `jump to the ${t.status === 'pending' ? 'creation' : t.status === 'in_progress' ? 'in-progress moment' : 'completion'} of this task` : ''"
            @click="$emit('jump-to-task', t)"
            @keydown.enter.prevent="$emit('jump-to-task', t)"
          >
            <span class="font-mono text-[12px]" :class="t.status === 'completed' ? 'text-emerald-600' : t.status === 'in_progress' ? 'text-amber-600' : 'text-slate-400'">{{ t.status === 'completed' ? '☑' : t.status === 'in_progress' ? '◐' : '☐' }}</span>
            <span class="font-mono text-[11px] text-slate-400 shrink-0">#{{ t.task_id }}</span>
            <span class="break-words flex-1 min-w-0" :class="t.status === 'completed' ? 'text-slate-500 line-through decoration-slate-300' : ''">{{ t.subject || '(no subject)' }}</span>
          </li>
        </ul>
      </div>
    </div>
    <div class="flex flex-col items-end gap-1.5 min-w-0 max-w-full">
      <div class="flex flex-wrap justify-end items-center gap-1.5">
        <button
          v-for="opt in [
            { id: 'conversation', label: 'Conversation' },
            { id: 'timeline', label: 'Timeline' },
            { id: 'terminal', label: 'Terminal' },
            { id: 'messages', label: 'Messages' },
          ]"
          :key="opt.id"
          type="button"
          class="px-3 py-1 text-xs rounded-full border transition-colors focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="viewMode === opt.id
            ? 'bg-blue-50 border-blue-400 text-blue-700 font-medium'
            : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'"
          @click="$emit('update:viewMode', opt.id)"
        >{{ opt.label }}</button>
      </div>
      <div class="flex items-center gap-2 text-[11px] text-slate-400 font-mono">
        <span v-if="lastReloadedAt">updated {{ fmtLocalClock(lastReloadedAt.toISOString()) }}</span>
        <Button
          variant="link"
          size="sm"
          :disabled="reloading || loading"
          :title="'Re-fetch spans' + (hasTurns ? ' and turns' : '') + ' from the server'"
          @click="$emit('reload')"
        >
          <span :class="reloading ? 'animate-spin inline-block' : 'inline-block'">↻</span>
          {{ reloading ? 'Reloading…' : 'Reload' }}
        </Button>
      </div>
    </div>
  </header>
</template>
