<script setup>
import { computed } from 'vue'
import Icon from '../../ui/Icon.vue'
import { taskSummaryLabel } from '../../../utils/taskSnapshots.js'

// One card per task-write span (`tool.TaskCreate` / `tool.TaskUpdate`),
// showing the task list AS THE MODEL SAW IT at that point — the snapshot is
// replayed from `session.task_list.events` up to and including this span, so
// no later status ever leaks backwards into an earlier card.
const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
  // taskSnapshotsBySpan() entry: {tasks, done, active, open}
  snapshot: { type: Object, required: true },
})
defineEmits(['activate'])

const counts = computed(() => taskSummaryLabel(props.snapshot))
const selected = computed(() =>
  !!(props.selectedSpan && props.selectedSpan.span_id === props.span.span_id))
</script>

<template>
  <div
    role="button"
    tabindex="0"
    data-testid="task-list-card"
    class="rounded-lg border border-slate-200 bg-white px-3 py-2 cursor-pointer hover:border-slate-300 transition-colors focus-visible:outline-2 focus-visible:outline-blue-500"
    :class="selected ? 'event-selected' : ''"
    @click="$emit('activate', span)"
    @keydown.enter.prevent="$emit('activate', span)"
    @keydown.space.prevent="$emit('activate', span)"
  >
    <div class="flex items-center gap-2 mb-1.5">
      <span class="w-1.5 h-1.5 rounded-full bg-indigo-500 shrink-0"></span>
      <span class="font-semibold uppercase tracking-widest text-[10px] text-slate-700">Task list</span>
      <span
        class="ml-auto font-mono text-[10.5px] text-slate-600 tabular-nums shrink-0"
        data-testid="task-list-counts"
      >{{ counts }}</span>
    </div>
    <ul class="divide-y divide-slate-100">
      <li
        v-for="t in snapshot.tasks"
        :key="t.task_id"
        class="flex items-start gap-2 py-1"
        data-testid="task-list-row"
      >
        <!-- Status glyph. Shape carries the state on its own (check / filled /
             hollow), so the row survives greyscale: colour only reinforces. -->
        <span
          v-if="t.status === 'completed'"
          class="shrink-0 mt-0.5 w-3.5 h-3.5 rounded-full bg-emerald-600 text-white flex items-center justify-center"
          title="done"
        ><Icon name="check" :size="10" /></span>
        <span
          v-else-if="t.status === 'in_progress'"
          class="shrink-0 mt-0.5 w-3.5 h-3.5 rounded-full bg-amber-600"
          title="in progress"
        ></span>
        <span
          v-else
          class="shrink-0 mt-0.5 w-3.5 h-3.5 rounded-full border-2 border-slate-500"
          title="open"
        ></span>
        <span
          class="min-w-0 flex-1 text-[12.5px] leading-snug break-words"
          :class="t.status === 'completed' ? 'line-through text-slate-500' : 'text-slate-800'"
        >{{ t.subject || `Task ${t.task_id}` }}</span>
        <span
          v-if="t.status === 'in_progress'"
          class="shrink-0 text-[10px] font-semibold uppercase tracking-wider text-amber-800 mt-0.5"
        >in progress</span>
      </li>
    </ul>
  </div>
</template>
