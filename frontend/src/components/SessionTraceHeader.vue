<script setup>
// Session metadata header for the trace view: eyebrow, title (+ source badge,
// expand, live/ended status), the identity + pivot chip row (trace id, model,
// start clock, plans, workflow runs, tasks), the expandable plan/workflow/task
// lists, the segmented view-mode switcher, and the reload control.
//
// Collapsed state (`collapsed` prop): the whole detail surface (eyebrow,
// title row, meta/chips, expanded lists) swaps for a single compact row —
// status dot, title, the parent-computed mono `digest`, switcher, Reload —
// while the parent folds the vitals/overview/spend strips away. The Details
// toggle (also bound to the H key in the parent) lives on the right in both
// states.
//
// The volume stats — spans, duration, active%, context%, cache, total tokens —
// deliberately do NOT live here any more: they are cells in TraceVitalsStrip,
// which gives each a stable slot instead of letting them reflow this paragraph.
// Cache read/write survives in the Overview panel's session bill, where it also
// carries a dollar figure.
//
// Self-contained by design: it takes the raw session + collections + the few
// spans-derived facts it can't compute alone (snapshotStaleAt,
// workflowParentTo), and derives everything else (title, task summary)
// internally. It owns only presentational toggle state and emits intent
// (`reload`, `jump-to-task`, `update:viewMode`) back to the parent, which
// still owns the data model.
import { ref, computed } from 'vue'
import { fmtTokens } from '../utils/traceFormatters.js'
import { badgeScopeOf, badgeTasks, countTasks, taskAgentSections } from '../utils/taskScope.js'
import Button from './ui/Button.vue'
import Icon from './ui/Icon.vue'

// Date formatters kept local (exact copies of SessionTraceView's) rather than
// imported: traceFormatters exposes a differently-behaved `fmtTime` (HH:MM), so
// importing it would silently change how the snapshot time, plan dates, and the
// "updated" clock render here. Unifying the siblings is a separate follow-up.
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
const props = defineProps({
  session: { type: Object, required: true },
  plans: { type: Array, default: () => [] },
  workflowRuns: { type: Array, default: () => [] },
  viewMode: { type: String, default: 'conversation' },
  // Per-mode row counts for the switcher (e.g. { terminal: 38, messages: 3 }).
  // A missing or zero entry renders no count pill.
  modeCounts: { type: Object, default: () => ({}) },
  reloading: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  lastReloadedAt: { type: Object, default: null },
  // A reload that failed AFTER the session loaded. The spans on screen are
  // still real, just frozen at `lastReloadedAt`, so this degrades the reload
  // control rather than taking the pane.
  reloadFailed: { type: Boolean, default: false },
  reloadErrorDetail: { type: String, default: '' },
  hasTurns: { type: Boolean, default: false },
  snapshotStaleAt: { type: [String, null], default: null },
  workflowParentTo: { type: [Object, null], default: null },
  // Collapsed compact-row state, owned (and scroll-driven) by the parent.
  collapsed: { type: Boolean, default: false },
  // [{ key, value, label, tone }] — the mono digest on the compact row.
  digest: { type: Array, default: () => [] },
})

const MODE_OPTIONS = [
  { id: 'conversation', label: 'Conversation' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'terminal', label: 'Terminal' },
  { id: 'messages', label: 'Messages' },
]

defineEmits(['update:viewMode', 'reload', 'jump-to-task', 'toggle-collapse'])

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

// A session with no `ended_at` is still attached to a live agent — the pulsing
// pill is the one piece of session state that has to be legible without
// reading a number, so it sits on the title line rather than in the vitals.
// (The ingest clears `ended_at` when a genuine resume lands, so an
// exited-then-resumed session reads Live again here.)
const isLive = computed(() => !props.session?.ended_at)
const startedClock = computed(() => fmtLocalClock(props.session?.started_at))

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
  // Only flag a genuine compaction reset: the peak must sit meaningfully above
  // the live context (same 1%-of-window bar as the +sub chip). An exact `> 0`
  // test let peak≈live jitter toggle this chip null↔value on every live poll —
  // on mobile that added/removed a wrapped header line and jerked the whole
  // page (scroll up/down) each reload cycle.
  if (peak - live <= win * 0.01) return null
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

// Tasks summary for the header badge: counts of every status across the list
// the badge speaks for — the main agent's, not the session-wide roll-up
// (taskScope.js). A `deleted` task was taken off the list by the model, so it
// counts nowhere: reporting it as "open" would claim work that is no longer
// planned, and would disagree with the per-span TASK LIST cards in the
// conversation feed, which drop it.
const taskSummary = computed(() => {
  const tasks = badgeTasks(props.session?.task_list?.final)
  if (!tasks.length) return null
  const c = countTasks(tasks)
  return {
    total: c.total, completed: c.done, inProgress: c.inProgress, pending: c.open,
    pct: Math.round((c.done / c.total) * 100) + '%',
  }
})

// The expanded list keeps EVERY agent's tasks — the badge counts one agent, so
// the sections (each with its own count) are what make that number explicable
// instead of a silent disagreement with the list it opens.
const taskSections = computed(
  () => taskAgentSections(props.session?.task_list?.final, props.session?.agent_roster))

// Only worth saying once a second agent kept a list; the wording has to stay
// honest about a session whose main agent never wrote one.
const TASK_SCOPE_NOTE = { main: " (main agent's list)", all: ' (all agents)' }
const taskBadgeScope = computed(() => badgeScopeOf(taskSections.value))
const taskBadgeTitle = computed(() => {
  const s = taskSummary.value
  if (!s) return ''
  const note = TASK_SCOPE_NOTE[taskBadgeScope.value] || ''
  return `session task list${note} — ${s.completed} done · ${s.inProgress} in progress`
    + ` · ${s.pending} open. Click to expand.`
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
</script>

<template>
  <header
    class="flex justify-between gap-4 flex-wrap"
    :class="collapsed ? 'items-center' : 'items-start mb-5'"
  >
    <!-- Keyed by fold state: the two layouts share no DOM, so only a fresh
         subtree can restart the enter fade; the height glide across the swap
         is the parent view's useFoldTransition tween. -->
    <!-- basis-80 is what makes the header's flex-wrap fire: the actions column
         is ~600px intrinsic, so a basis-0 `flex-1` title never overflows the
         line and instead shrinks to ~40px, wrapping the title one character
         per line below xl. The floor drops the actions onto their own row
         there, and is low enough that ≥xl still lays out on one row. The
         compact row is exempt: it truncates rather than wraps, so it wants
         to keep sharing the row. -->
    <div
      :key="collapsed ? 'compact' : 'full'"
      class="min-w-0 flex-1 trace-fold-enter"
      :class="collapsed ? '' : 'basis-80'"
    >
      <!-- Compact identity row: status dot + title + mono digest. Everything
           else (eyebrow, title row, meta chips, expanded lists) only renders
           in the full state. -->
      <div v-if="collapsed" class="flex items-center gap-2.5 overflow-hidden">
        <span
          class="h-1.5 w-1.5 shrink-0 rounded-full"
          :class="isLive ? 'bg-emerald-500 animate-pulse' : 'bg-slate-400'"
          :title="isLive ? 'this session has not ended — spans are still arriving' : 'this session has ended'"
        ></span>
        <!-- Still the page's h1: collapsed is the normal reading state at ≥lg,
             and swapping it for a span leaves heading navigation with no
             page heading for most of the session. -->
        <h1
          class="truncate text-sm font-semibold text-slate-800"
          :title="session.title || ''"
        >{{ sessionTitle }}</h1>
        <span
          class="flex shrink-0 items-center gap-2 overflow-hidden font-mono text-[11px] text-slate-500"
          data-testid="trace-header-digest"
        >
          <span
            v-for="d in digest"
            :key="d.key"
            class="flex items-baseline gap-1 whitespace-nowrap"
          >
            <span class="text-slate-300">·</span>
            <span class="font-semibold tabular-nums" :class="d.tone">{{ d.value }}</span>
            <span v-if="d.label" class="text-slate-400">{{ d.label }}</span>
          </span>
        </span>
      </div>
      <template v-else>
      <div class="text-[11px] tracking-widest uppercase text-slate-400 font-semibold mb-1">
        Observability · Session Trace
      </div>
      <!-- items-start + a nudge, NOT items-baseline: the pill is a bordered
           box whose padding hangs below its text baseline, so baseline
           alignment drops it ~6px under the title's optical centre. The nudge
           is (title line-height 30px - pill height 22.5px) / 2 = 3.75px, i.e.
           `mt-1` — it centres the pill on the title's FIRST line, which is
           what a wrapping title needs. `session-header-live-pill.spec.js`
           pins the result. -->
      <div class="flex items-start gap-3">
        <h1
          class="text-2xl font-semibold text-slate-900 leading-tight m-0 break-words min-w-0 flex-1"
          :title="session.title || ''"
        >{{ sessionTitle }}<span
          v-if="session.title && session.title_source"
          class="ml-2 align-middle inline-block rounded border border-slate-200 bg-slate-50 text-slate-500 text-[10px] font-medium px-1.5 py-0.5 uppercase tracking-wide"
          :title="titleSourceTooltip(session.title_source)"
        >{{ titleSourceLabel(session.title_source) }}</span></h1>
        <span
          class="mt-1 shrink-0 inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-0.5 text-[11px] font-semibold"
          :class="isLive
            ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
            : 'border-slate-200 bg-slate-50 text-slate-500'"
          :title="isLive ? 'this session has not ended — spans are still arriving' : 'this session has ended'"
        >
          <span
            class="h-1.5 w-1.5 rounded-full"
            :class="isLive ? 'bg-emerald-500 animate-pulse' : 'bg-slate-400'"
          ></span>{{ isLive ? 'Live' : 'Ended' }}
        </span>
      </div>
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
        <template v-if="session.model">
          <span class="text-slate-300">·</span>
          <span class="font-mono">{{ session.model }}</span>
        </template>
        <template v-if="startedClock">
          <span class="text-slate-300">·</span>
          <span>started <span class="font-mono">{{ startedClock }}</span></span>
        </template>
        <template v-if="session.context_pct != null">
          <!-- The headline ctx% now lives in the vitals strip; only the two
               divergence markers stay here, because each says something the
               single gauge cannot. -->
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
        <!-- Cache read/write is NOT dropped: it moved to the Overview panel's
             session bill, which shows it against a dollar figure — the reading
             that actually matters, since cache dominates tokens but not cost. -->
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
            class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-[11px] font-medium ml-1 cursor-pointer select-none border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100"
            data-testid="trace-tasks-badge"
            :title="taskBadgeTitle"
            @click="tasksExpanded = !tasksExpanded"
          >tasks
            <span class="font-mono tabular-nums">{{ taskSummary.completed }}<span class="opacity-50">/</span>{{ taskSummary.total }}</span>
            <span class="inline-block h-1 w-[34px] overflow-hidden rounded-full bg-indigo-200">
              <span class="block h-full rounded-full bg-indigo-500" :style="{ width: taskSummary.pct }"></span>
            </span>
            <span class="opacity-60">{{ tasksExpanded ? '▾' : '▸' }}</span>
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
      <!-- Expanded task list (final state across the session), sectioned by
           owning agent — the badge counts the main agent's section only, and a
           heading per agent is what keeps that number explicable. Headings
           appear only once a subagent kept its own list; a single-agent session
           reads as one plain list.
           Each row is clickable: jumps the spine to that task's
           TaskCreate span and selects it, so the user can click a
           task in the summary and land on the moment it was opened
           without scrolling through hundreds of spans. -->
      <div
        v-if="taskSummary && tasksExpanded"
        class="mt-2 rounded-md border border-indigo-200 bg-indigo-50/50 px-3 py-2 max-w-2xl"
        data-testid="trace-task-list"
      >
        <!-- With no main-agent list the badge counts the union, which is no
             single agent's section — say the total outright rather than leave
             the number matching nothing on screen. -->
        <p
          v-if="taskBadgeScope === 'all'"
          class="flex items-baseline gap-2 text-[11px] font-medium text-indigo-700 mb-1"
          data-testid="trace-task-total"
        >
          <span>all agents</span>
          <span class="font-mono tabular-nums text-slate-500">{{ taskSummary.completed }}/{{ taskSummary.total }}</span>
          <span class="text-slate-400 font-normal">counted in the badge</span>
        </p>
        <template v-for="s in taskSections" :key="s.agent_id">
        <p
          v-if="taskSections.length > 1"
          class="flex items-baseline gap-2 text-[11px] font-medium text-indigo-700 mt-1.5 first:mt-0"
          data-testid="trace-task-section"
        >
          <span class="truncate">{{ s.label }}</span>
          <span class="font-mono tabular-nums text-slate-500">{{ s.summary.done }}/{{ s.summary.total }}</span>
          <span v-if="s.isMain" class="text-slate-400 font-normal">counted in the badge</span>
        </p>
        <ul class="text-[13px] text-slate-800 leading-snug">
          <li
            v-for="t in s.tasks"
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
        </template>
      </div>
      </template>
    </div>
    <div
      class="flex min-w-0 max-w-full"
      :class="collapsed ? 'flex-row flex-wrap items-center gap-x-3 gap-y-1.5' : 'flex-col items-end gap-1.5'"
    >
      <div class="flex flex-wrap justify-end items-center gap-1.5">
        <!-- Header-row actions the parent owns (e.g. the agents popover).
             These survive the fold: the roster's running-count badge is live
             session status, not a detail, and folding it away hid it during
             the exact stretch — scrolled into a long transcript — when the
             reader most needs to see whether subagents are still working. -->
        <slot name="actions"></slot>
        <!-- Segmented control on one recessed track: the four modes are a
             single exclusive choice, and loose bordered pills read as four
             independent toggles. Below lg the compact sticky strip owns the
             switcher; a second visible copy here would double every button. -->
        <div class="hidden lg:inline-flex gap-0.5 rounded-[11px] bg-slate-100 p-[3px]">
          <Button
            v-for="opt in MODE_OPTIONS"
            :key="opt.id"
            variant="ghost"
            size="sm"
            class="h-auto gap-1.5 rounded-lg px-3 py-1.5 text-[12.5px]"
            :class="viewMode === opt.id
              ? 'bg-white text-blue-800 font-semibold shadow-[0_1px_3px_rgba(15,23,42,0.12)] hover:bg-white'
              : 'text-slate-500 hover:bg-slate-200/60 hover:text-slate-700'"
            :aria-pressed="viewMode === opt.id"
            :title="modeCounts[opt.id] ? `${opt.label} · ${modeCounts[opt.id]} rows` : opt.label"
            @click="$emit('update:viewMode', opt.id)"
          >{{ opt.label }}<!-- aria-hidden: the count is a visual density hint,
            and folding it into the accessible name would turn every mode
            button's name into a moving target ("Terminal" → "Terminal 38").
            The title above carries it for anyone who wants it. --><span
            v-if="modeCounts[opt.id]"
            aria-hidden="true"
            class="rounded-md px-1.5 text-[10.5px] font-semibold tabular-nums"
            :class="viewMode === opt.id ? 'bg-blue-50 text-blue-800' : 'bg-slate-200 text-slate-500'"
          >{{ modeCounts[opt.id] }}</span></Button>
        </div>
        <!-- Manual override for the collapse: pins the choice until the body
             scroll returns to the top (the parent owns that state; H is the
             keyboard twin). -->
        <Button
          variant="ghost"
          size="sm"
          class="h-auto shrink-0 gap-1 border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-semibold text-slate-500 hover:border-slate-300 hover:bg-slate-100 hover:text-slate-700"
          :title="(collapsed ? 'Show session details' : 'Collapse header for more transcript') + ' (H)'"
          :aria-expanded="!collapsed"
          data-testid="header-details-toggle"
          @click="$emit('toggle-collapse')"
        ><span
          aria-hidden="true"
          class="inline-block leading-none transition-transform"
          :class="collapsed ? 'rotate-180' : ''"
        >⌃</span>{{ collapsed ? 'Details' : 'Hide details' }}</Button>
      </div>
      <div class="flex items-center gap-2 text-[11px] text-slate-400 font-mono">
        <!-- tabular-nums: a proportional-width timestamp jitters the column
             width on every live poll, which can toggle a wrap in the row
             above and bounce everything below the header. -->
        <span v-if="lastReloadedAt" class="tabular-nums">updated {{ fmtLocalClock(lastReloadedAt.toISOString()) }}</span>
        <!-- Degraded marker, not a banner: the spans behind it are still worth
             reading. The transport detail rides the tooltip instead of the row
             because a 500 body is often one unbroken token, and rendering it
             inline would widen (or wrap) the header on every poll tick. -->
        <span
          v-if="reloadFailed"
          data-testid="trace-reload-error"
          role="status"
          class="inline-flex items-center gap-1 text-warning-strong font-sans"
          :title="reloadErrorDetail ? `Couldn’t refresh: ${reloadErrorDetail}` : 'Couldn’t refresh'"
        ><Icon name="alert-triangle" :size="12" />not updating</span>
        <Button
          variant="link"
          size="sm"
          :disabled="reloading || loading"
          :title="'Re-fetch spans' + (hasTurns ? ' and turns' : '') + ' from the server'"
          @click="$emit('reload')"
        >
          <span :class="reloading ? 'animate-spin inline-block' : 'inline-block'">↻</span>
          <!-- Label stays constant width: a "Reload"→"Reloading…" swap widened
               this button, and as a flex sibling of the metrics column that
               squeezed the metrics into an extra wrapped line — on mobile,
               scrolled to the top of a live session, the header grew/shrank
               ~15px every poll and jerked the page. The spinning icon +
               disabled state already signal the in-progress reload. -->
          Reload
        </Button>
      </div>
    </div>
  </header>
</template>
