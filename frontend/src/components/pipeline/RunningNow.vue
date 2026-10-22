<script setup>
// "Running now" (1a): the tasks an agent is working on, newest attempt first,
// heading the Run next card. Each row offers Watch and Stop (feature #119
// R4a); a run with no trace yet has nothing to watch, so Watch opens the task.
//
// The header's slot chip and the "finished but still open" group answer the
// question a refused Run raises — who holds the pool — before Run is refused.
import { computed, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import RunSlotList from '../tasks/RunSlotList.vue'
import TaskRefusal from '../tasks/TaskRefusal.vue'
import { finishedHolders } from '../../composables/useRunSlots.js'
import { ago, runningMeta, runningSub } from '../../utils/pipelineFormat'
import { useTaskVerbs } from '../../composables/useTaskVerbs.js'
import { taskTitle } from '../../utils/taskTitle.js'

const props = defineProps({
  running: { type: Object, required: true },
  slots: { type: Object, default: null },
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['stopped', 'closed'])

const slotsOpen = ref(false)
const finished = computed(() => finishedHolders(props.slots))

const canWrite = api.getStoredUser()?.role !== 'viewer'
const { refusals, busyId, act } = useTaskVerbs(async () => emit('stopped'))

function watchTo(row) {
  return row.live?.trace_id ? `/trace/sessions/${row.live.trace_id}` : `/tasks/${row.id}`
}
</script>

<template>
  <div class="rnw" :class="{ 'rnw--compact': compact }" data-testid="running-now">
    <header class="pl-panel__head" data-pl-part="panel-head">
      <h2 id="rnw-title" class="pl-panel__title" data-pl-part="panel-title">Running now</h2>
      <span class="pl-panel__sub" data-pl-part="panel-meta" data-testid="running-now-count">{{ running.total }}</span>
      <Button
        v-if="slots" variant="ghost" size="sm" class="rnw-slots" data-pl-part="tag"
        :aria-expanded="slotsOpen" aria-controls="rnw-slot-list" data-testid="run-slots-chip"
        @click="slotsOpen = !slotsOpen"
      >Run slots {{ slots.used }}/{{ slots.limit }}</Button>
      <router-link to="/live" class="pl-panel__link focus-visible:outline-2 focus-visible:outline-blue-500" data-pl-part="panel-link">Open Live →</router-link>
    </header>
    <div v-if="slotsOpen && slots" id="rnw-slot-list" class="rnw-slotlist">
      <RunSlotList
        :holders="slots.holders" :can-write="canWrite" testid="run-slots-holders"
        @closed="trace => emit('closed', trace)"
      />
      <p v-if="!slots.holders.length" class="rnw-empty">No run holds a slot.</p>
    </div>
    <ul class="rnw-rows" aria-labelledby="rnw-title">
      <li v-for="row in running.rows" :key="row.id" class="rnw-row" data-pl-part="row" :data-testid="`running-now-row-${row.id}`">
        <span class="rnw-id" data-pl-part="row-id">#{{ row.id }}</span>
        <router-link :to="`/tasks/${row.id}`" class="rnw-main focus-visible:outline-2 focus-visible:outline-blue-500">
          <span class="rnw-title">{{ taskTitle(row) }}</span>
          <span class="rnw-sub">{{ runningSub(row) }}</span>
        </router-link>
        <span class="rnw-facts">
          <span v-if="row.live" class="rnw-live" data-pl-part="live"><i class="rnw-dot" aria-hidden="true" />live · {{ ago(row.live?.started_at) }}</span>
          <span class="rnw-meta">{{ runningMeta(row) }}</span>
        </span>
        <router-link
          :to="watchTo(row)"
          class="rnw-watch"
          data-pl-part="key"
          :aria-label="`Watch #${row.id}`"
          :data-testid="`running-now-watch-${row.id}`"
        >Watch</router-link>
        <Button
          v-if="canWrite"
          variant="secondary"
          size="sm"
          class="rnw-stop"
          data-pl-part="key"
          data-tone="danger"
          :disabled="busyId === row.id"
          :aria-label="`Stop #${row.id}`"
          :data-testid="`running-now-stop-${row.id}`"
          @click="act('stop', row)"
        >Stop</Button>
        <TaskRefusal v-if="refusals[row.id]" class="rnw-refusal" :refusal="refusals[row.id]" />
      </li>
    </ul>
    <template v-if="finished.length">
      <h3 id="rnw-finished-title" class="rnw-group" data-testid="running-now-finished-title">已完成但会话未关闭</h3>
      <div class="rnw-slotlist" data-testid="running-now-finished">
        <RunSlotList
          :holders="finished" :can-write="canWrite" close-label="Close session"
          testid="running-now-finished-list" @closed="trace => emit('closed', trace)"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.rnw .rnw-slots {
    flex: none; height: 1.5rem; padding: 0 0.5rem; font-size: 11.5px; font-weight: 600;
    color: var(--color-fg-muted); font-variant-numeric: tabular-nums; cursor: pointer;
    background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: 999px;
}
.rnw .rnw-slots:hover { color: var(--color-fg); }
.rnw .rnw-slots:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
.rnw-slotlist { padding: 0.25rem 1.1rem 0.6rem; border-top: 1px solid var(--color-border-subtle); }
.rnw-empty { margin: 0.375rem 0 0; font-size: 12px; color: var(--color-fg-muted); }
.rnw-group {
    margin: 0; padding: 0.6rem 1.1rem 0; border-top: 1px solid var(--color-border-subtle);
    font-size: 12px; font-weight: 600; color: var(--color-fg-muted);
}
.rnw-rows { display: flex; flex-direction: column; }
.rnw-row .rnw-refusal { flex: 1 0 100%; margin-top: 0; }
.rnw-row .rnw-stop { flex: none; color: var(--color-danger); }
.rnw-row {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; min-width: 0;
    padding: 0.6rem 1.1rem; border-top: 1px solid var(--color-border-subtle);
}
.rnw-id { min-width: 2.25rem; font-size: 12px; color: var(--color-fg-faint); font-variant-numeric: tabular-nums; flex: none; }
.rnw-main { flex: 1; min-width: 0; display: flex; flex-direction: column; text-decoration: none; }
.rnw-title { font-size: 13.5px; font-weight: 500; color: var(--color-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rnw-sub { font-size: 11.5px; color: var(--color-fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rnw-main:hover .rnw-title { text-decoration: underline; }
.rnw-facts { display: flex; flex-direction: column; align-items: flex-end; gap: 0.2rem; flex: none; }
.rnw-live { display: inline-flex; align-items: center; gap: 0.3rem; font-size: 11.5px; font-weight: 600; color: var(--color-agent); }
.rnw-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--color-agent); box-shadow: 0 0 0 3px var(--color-agent-soft); }
.rnw-meta { font-size: 11px; color: var(--color-fg-muted); font-variant-numeric: tabular-nums; }
.rnw-watch {
    display: inline-flex; align-items: center; flex: none; height: 1.75rem; padding: 0 0.625rem;
    font-size: 0.75rem; font-weight: 600; color: var(--color-primary); text-decoration: none;
    background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-lg);
}
.rnw--compact .rnw-row .rnw-watch, .rnw--compact .rnw-row .rnw-stop { min-height: 44px; }
.rnw-watch:hover { filter: brightness(0.97); }
.rnw-watch:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
</style>
