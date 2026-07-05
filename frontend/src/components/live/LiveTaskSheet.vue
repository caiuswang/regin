<script setup>
// Tasks sheet body for the /live card. Counts strip + task rows sourced from
// meta.task_list.final (the session's FINAL snapshot, computed server-side) —
// never re-derived from the loaded tail, whose older task spans fold away.
// Completed tasks sort last but STAY VISIBLE (struck ✓); pending ○,
// in_progress ◔ with its active_form line.
import { computed } from 'vue'
import { taskSummaryOf } from '../../utils/liveRows.js'

const props = defineProps({
  tasks: { type: Array, default: () => [] },
})

const RANK = { in_progress: 0, pending: 1, completed: 2 }
const MARK = { in_progress: '◔', completed: '✓', pending: '○' }
const CLS = { in_progress: 'doing', completed: 'done', pending: 'pending' }

const summary = computed(() => taskSummaryOf(props.tasks))
const sorted = computed(() => [...props.tasks].sort(
  (a, b) => (RANK[a.status] ?? 1) - (RANK[b.status] ?? 1)))

function markOf(t) { return MARK[t.status] || '○' }
function clsOf(t) { return CLS[t.status] || 'pending' }
</script>

<template>
  <div data-testid="live-task-sheet">
    <p v-if="summary" class="live-task-counts" data-testid="live-task-counts">
      {{ summary.inProgress }} in progress · {{ summary.open }} open · {{ summary.done }} done
    </p>
    <div v-else class="live-sheet-empty">no tasks yet</div>
    <div
      v-for="(t, i) in sorted"
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
  </div>
</template>
