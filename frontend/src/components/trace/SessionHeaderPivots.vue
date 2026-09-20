<script setup>
// The session's VARIABLE-length chips — what this run happened to produce:
// a frozen-manifest warning, a launched-from backlink, the two context
// divergence markers, plan files, workflow runs, the task list. Count varies
// from zero to seven, which is why they get their own row instead of sharing
// the identity run: one extra `plans 3` chip there pushed `started …` onto a
// line of its own, and the `·` separators between them stranded a dot at
// every wrap.
//
// Owns the expand state for the three lists it opens, so the parent header
// carries neither the toggles nor ~180 lines of list markup — `.vue`
// template-binding headroom on SessionTraceHeader is what pays for this.
import { ref, computed } from 'vue'
import { fmtTokens } from '../../utils/traceFormatters.js'
import { badgeScopeOf, badgeTasks, countTasks, taskAgentSections } from '../../utils/taskScope.js'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  session: { type: Object, required: true },
  plans: { type: Array, default: () => [] },
  workflowRuns: { type: Array, default: () => [] },
  snapshotStaleAt: { type: [String, null], default: null },
  workflowParentTo: { type: [Object, null], default: null },
})
defineEmits(['jump-to-task'])

const plansExpanded = ref(false)
const workflowRunsExpanded = ref(false)
const tasksExpanded = ref(false)

function fmtTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleTimeString() + '.' + String(d.getMilliseconds()).padStart(3, '0')
}
function fmtDate(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString()
}

// Pre-compaction high-water mark. The headline ctx% is the *live* peak
// (since the last /compact); when the session compacted, the all-time main
// peak sat higher — surface it as a muted "peaked X%" chip so the drop is
// legible rather than looking like lost data. Null when no compaction
// reclaimed context (peaks coincide).
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
// usage. Compare the two all-time peaks directly (>1% of window) — NOT the
// headline ctx%, which now tracks the post-compaction live peak and would
// otherwise make every compacted session look like advisor spill.
const contextSubDiverges = computed(() => {
  const s = props.session
  const win = s?.context_window_tokens
  const full = s?.peak_context_tokens
  const main = s?.peak_main_context_tokens
  if (!win || win <= 0 || !Number.isFinite(full) || !Number.isFinite(main)) return false
  return (full - main) > win * 0.01
})
const showsContextMarkers = computed(
  () => props.session?.context_pct != null
    && (contextPeakPct.value != null || contextSubDiverges.value),
)

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

// An empty row is not a thin row: it is `mt-1.5` of dead space above the
// attribution on every session that ran no plan, no workflow and kept no task
// list. Render nothing instead.
const hasAny = computed(() => Boolean(
  props.snapshotStaleAt || props.workflowParentTo || showsContextMarkers.value
  || props.plans.length || props.workflowRuns.length || taskSummary.value,
))
</script>

<template>
  <!-- Single root: the chip row and the three lists it opens are one block, and
       a multi-root SFC would drop any class the parent binds on it. -->
  <div v-if="hasAny">
  <div
    class="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-1"
    data-testid="trace-header-pivots"
  >
    <!-- Stale-snapshot badge: the run resumed past the manifest snapshot,
         which the runtime only flushes at pause/completion — so phases and
         counts here are frozen and can't refresh live. -->
    <span
      v-if="snapshotStaleAt"
      class="hdr-chip hdr-chip-warning"
      :title="`This run resumed and is still in flight. The Workflow runtime writes its progress snapshot only at pause/completion, so the phases and agent counts shown are frozen as of ${fmtTime(snapshotStaleAt)} and can't be refreshed live from disk. Pause the run (or let it finish) to update.`"
    ><Icon name="pause" :size="10" class="shrink-0" />snapshot {{ fmtTime(snapshotStaleAt) }}</span>
    <!-- No workflow-name chip here: the session title already *is* the
         workflow name (title_source=workflow_name), so a chip repeating it
         would be redundant. The backlink stays. -->
    <router-link
      v-if="workflowParentTo"
      :to="workflowParentTo"
      class="hdr-chip"
      title="Open the Claude Code session that launched this workflow run"
    ><Icon name="arrow-up" :size="10" class="shrink-0" />launched from</router-link>
    <!-- The headline ctx% lives in the vitals strip; only the two divergence
         markers stay here, because each says something the gauge cannot. -->
    <span
      v-if="showsContextMarkers && contextPeakPct != null"
      class="hdr-chip"
      :title="`peaked at ${(session.peak_main_context_tokens || session.peak_context_tokens || 0).toLocaleString()} tokens (${contextPeakPct}% of window) before /compact; the headline ctx% tracks the live context since the most recent compaction.`"
    >peaked {{ contextPeakPct }}%</span>
    <!-- All-inclusive peak only when it diverges (advisor turns). Shown as an
         absolute token count, not a % of window: it can exceed the window
         (server-side sub-call tokens are summed into one turn's bill), so a
         percentage reads as broken. -->
    <span
      v-if="showsContextMarkers && contextSubDiverges"
      class="hdr-chip"
      :title="`all-inclusive peak turn: ${(session.peak_context_tokens || 0).toLocaleString()} tokens (vs ${(session.context_window_tokens || 0).toLocaleString()} window). Includes advisor/server-side sub-call tokens that Anthropic rolls into the parent turn's usage, so it can exceed the window — the headline ctx% excludes these.`"
    >+sub {{ fmtTokens(session.peak_context_tokens) }}</span>
    <!-- Plan chips: each PlanSession row this session authored or edited lets
         the reader pivot session → plan from the header. N=1 renders inline as
         a direct link; N≥2 collapses to a `plans N` chip with click-to-expand,
         matching the workflow and task chips beside it. -->
    <router-link
      v-if="plans.length === 1"
      :to="`/plans/${encodeURIComponent(plans[0].plan_filename)}`"
      class="hdr-chip hdr-chip-info"
      :title="`plan: ${plans[0].plan_filename}`"
    >plan <span class="hdr-chip-clip">{{ plans[0].plan_filename }}</span></router-link>
    <span
      v-else-if="plans.length > 1"
      class="hdr-chip hdr-chip-info hdr-chip-action"
      title="plan files this session authored or edited — click to expand"
      @click="plansExpanded = !plansExpanded"
    >plans {{ plans.length }}
      <Icon :name="plansExpanded ? 'chevron-down' : 'chevron-right'" :size="10" class="shrink-0 opacity-70" />
    </span>
    <!-- Workflow run chips: dynamic-workflow runs this session launched, so
         the reader can pivot session → run from the header. -->
    <router-link
      v-if="workflowRuns.length === 1"
      :to="`/trace/sessions/${workflowRuns[0].run_id}`"
      class="hdr-chip hdr-chip-success"
      :title="`workflow run: ${workflowRuns[0].name || workflowRuns[0].run_id}`"
    ><Icon name="workflow" :size="10" class="shrink-0" /><span class="hdr-chip-clip">{{ workflowRuns[0].name || 'workflow run' }}</span></router-link>
    <span
      v-else-if="workflowRuns.length > 1"
      class="hdr-chip hdr-chip-success hdr-chip-action"
      title="dynamic-workflow runs launched from this session — click to expand"
      @click="workflowRunsExpanded = !workflowRunsExpanded"
    ><Icon name="workflow" :size="10" class="shrink-0" />workflows {{ workflowRuns.length }}
      <Icon :name="workflowRunsExpanded ? 'chevron-down' : 'chevron-right'" :size="10" class="shrink-0 opacity-70" />
    </span>
    <!-- Tasks summary badge: the final task-list state across the session, so
         the reader doesn't have to scroll the spine to find it. -->
    <span
      v-if="taskSummary"
      class="hdr-chip hdr-chip-agent hdr-chip-action"
      data-testid="trace-tasks-badge"
      :title="taskBadgeTitle"
      @click="tasksExpanded = !tasksExpanded"
    >tasks {{ taskSummary.completed }}<span class="opacity-50">/</span>{{ taskSummary.total }}
      <span class="inline-block h-1 w-[34px] shrink-0 overflow-hidden rounded-full bg-agent-line">
        <span class="block h-full rounded-full bg-agent" :style="{ width: taskSummary.pct }"></span>
      </span>
      <Icon :name="tasksExpanded ? 'chevron-down' : 'chevron-right'" :size="10" class="shrink-0 opacity-70" />
    </span>
  </div>

  <!-- Expanded plans list. Each row is a router-link to /plans/<filename>, so
       the reader can pivot to any of the session's plan files without
       scrolling or hunting in the spine. -->
  <div
    v-if="plans.length > 1 && plansExpanded"
    class="mt-2 max-w-2xl rounded-md border border-info-soft bg-info-soft px-3 py-2"
  >
    <ul class="text-[13px] leading-snug text-fg">
      <li v-for="p in plans" :key="p.id" class="flex items-baseline gap-2 py-0.5">
        <router-link
          :to="`/plans/${encodeURIComponent(p.plan_filename)}`"
          class="break-all font-mono text-[12px] text-primary hover:underline focus-visible:outline-2 focus-visible:outline-ring"
          :title="p.plan_filename"
        >{{ p.plan_filename }}</router-link>
        <span class="shrink-0 font-mono text-[11px] text-fg-faint">
          {{ fmtDate(p.started_at) }}<span v-if="p.ended_at"> – {{ fmtDate(p.ended_at) }}</span>
        </span>
      </li>
    </ul>
  </div>
  <!-- Expanded workflow runs list. Each row links to the run's captured trace. -->
  <div
    v-if="workflowRuns.length > 1 && workflowRunsExpanded"
    class="mt-2 max-w-2xl rounded-md border border-success-soft bg-success-soft px-3 py-2"
  >
    <ul class="text-[13px] leading-snug text-fg">
      <li v-for="r in workflowRuns" :key="r.run_id" class="flex items-baseline gap-2 py-0.5">
        <router-link
          :to="`/trace/sessions/${r.run_id}`"
          class="break-all text-success-strong hover:underline focus-visible:outline-2 focus-visible:outline-ring"
          :title="r.run_id"
        >{{ r.name || r.run_id }}</router-link>
        <span v-if="r.agent_count" class="shrink-0 font-mono text-[11px] text-fg-subtle">{{ r.agent_count }} agent<span v-if="r.agent_count !== 1">s</span><template v-if="r.phase_count"> · {{ r.phase_count }} phase<span v-if="r.phase_count !== 1">s</span></template><template v-if="r.tokens"> · {{ fmtTokens(r.tokens) }} tok</template></span>
        <span class="shrink-0 font-mono text-[11px] text-fg-faint">{{ r.run_id }}</span>
      </li>
    </ul>
  </div>
  <!-- Expanded task list (final state across the session), sectioned by owning
       agent — the badge counts the main agent's section only, and a heading per
       agent is what keeps that number explicable. Headings appear only once a
       subagent kept its own list; a single-agent session reads as one plain
       list. Each row jumps the spine to that task's span and selects it. -->
  <div
    v-if="taskSummary && tasksExpanded"
    class="mt-2 max-w-2xl rounded-md border border-agent-line bg-agent-soft px-3 py-2"
    data-testid="trace-task-list"
  >
    <!-- With no main-agent list the badge counts the union, which is no single
         agent's section — say the total outright rather than leave the number
         matching nothing on screen. -->
    <p
      v-if="taskBadgeScope === 'all'"
      class="mb-1 flex items-baseline gap-2 text-[11px] font-medium text-agent"
      data-testid="trace-task-total"
    >
      <span>all agents</span>
      <span class="font-mono tabular-nums text-fg-subtle">{{ taskSummary.completed }}/{{ taskSummary.total }}</span>
      <span class="font-normal text-fg-faint">counted in the badge</span>
    </p>
    <template v-for="s in taskSections" :key="s.agent_id">
      <p
        v-if="taskSections.length > 1"
        class="mt-1.5 flex items-baseline gap-2 text-[11px] font-medium text-agent first:mt-0"
        data-testid="trace-task-section"
      >
        <span class="truncate">{{ s.label }}</span>
        <span class="font-mono tabular-nums text-fg-subtle">{{ s.summary.done }}/{{ s.summary.total }}</span>
        <span v-if="s.isMain" class="font-normal text-fg-faint">counted in the badge</span>
      </p>
      <ul class="text-[13px] leading-snug text-fg">
        <li
          v-for="t in s.tasks"
          :key="t.task_id"
          tabindex="0"
          class="-mx-1 flex cursor-pointer items-baseline gap-2 rounded px-1 py-0.5 hover:bg-agent-line focus-visible:outline-2 focus-visible:outline-agent"
          :title="(t.current_span_id || t.created_span_id) ? `jump to the ${t.status === 'pending' ? 'creation' : t.status === 'in_progress' ? 'in-progress moment' : 'completion'} of this task` : ''"
          @click="$emit('jump-to-task', t)"
          @keydown.enter.prevent="$emit('jump-to-task', t)"
        >
          <span class="font-mono text-[12px]" :class="t.status === 'completed' ? 'text-success' : t.status === 'in_progress' ? 'text-warning' : 'text-fg-faint'">{{ t.status === 'completed' ? '☑' : t.status === 'in_progress' ? '◐' : '☐' }}</span>
          <span class="shrink-0 font-mono text-[11px] text-fg-faint">#{{ t.task_id }}</span>
          <span class="min-w-0 flex-1 break-words" :class="t.status === 'completed' ? 'text-fg-subtle line-through decoration-slate-300' : ''">{{ t.subject || '(no subject)' }}</span>
        </li>
      </ul>
    </template>
  </div>
  </div>
</template>
