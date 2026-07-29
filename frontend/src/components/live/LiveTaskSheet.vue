<script setup>
// Tasks sheet body for the /live card. Counts strip + task rows sourced from
// meta.task_list.final (the session's FINAL snapshot, computed server-side) —
// never re-derived from the loaded tail, whose older task spans fold away.
// Completed tasks sort last but STAY VISIBLE (struck ✓); pending ○,
// in_progress ◔ with its active_form line. Status only sorts WITHIN an agent:
// a subagent keeps its own list in the same trace, so a status-only sort would
// braid it back through the main one.
//
// The strip counts the same list the header chip does (the main agent's — see
// taskScope.js), while every agent's list stays visible below it under its own
// heading, so the chip's number maps onto a section rather than disagreeing
// with the sheet it opens.
import { computed } from 'vue'
import { taskSummaryOf } from '../../utils/liveRows.js'
import { badgeScopeOf, taskAgentSections } from '../../utils/taskScope.js'

const props = defineProps({
  tasks: { type: Array, default: () => [] },
  agents: { type: Array, default: () => [] },
})

const RANK = { in_progress: 0, pending: 1, completed: 2 }
const MARK = { in_progress: '◔', completed: '✓', pending: '○' }
const CLS = { in_progress: 'doing', completed: 'done', pending: 'pending' }

const summary = computed(() => taskSummaryOf(props.tasks))
const sections = computed(() => taskAgentSections(props.tasks, props.agents)
  .map(s => ({
    ...s,
    tasks: [...s.tasks].sort((a, b) => (RANK[a.status] ?? 1) - (RANK[b.status] ?? 1)),
  })))

// The strip sits above every agent's rows, so once there is a second list it
// has to name the one it counts.
const SCOPE_NOTE = { main: 'main agent', all: 'all agents' }
const scopeNote = computed(() => SCOPE_NOTE[badgeScopeOf(sections.value)] || '')

function markOf(t) { return MARK[t.status] || '○' }
function clsOf(t) { return CLS[t.status] || 'pending' }
</script>

<template>
  <div data-testid="live-task-sheet">
    <p v-if="summary" class="live-task-counts" data-testid="live-task-counts">
      {{ summary.inProgress }} in progress · {{ summary.open }} open · {{ summary.done }} done<template
        v-if="scopeNote"> · {{ scopeNote }}</template>
    </p>
    <div v-else class="live-sheet-empty">no tasks yet</div>
    <template v-for="s in sections" :key="s.agent_id">
      <p
        v-if="sections.length > 1"
        class="live-task-agent"
        data-testid="live-task-agent"
      >
        {{ s.label }}
        <span class="live-tabnum">{{ s.summary.done }}/{{ s.summary.total }}</span>
      </p>
      <div
        v-for="(t, i) in s.tasks"
        :key="t.task_id ?? i"
        class="live-task-item"
        :class="`live-task-item-${clsOf(t)}`"
        data-testid="live-task-item"
      >
        <span class="live-task-mark" :class="`live-task-mark-${clsOf(t)}`" aria-hidden="true">
          {{ markOf(t) }}
        </span>
        <span class="live-task-body">
          <span class="live-task-subject">{{ t.subject }}</span>
          <span
            v-if="t.status === 'in_progress' && t.active_form"
            class="live-task-active"
          >{{ t.active_form }}</span>
        </span>
      </div>
    </template>
  </div>
</template>
