<script setup>
// "Run next" (1a / 2a): armed tasks, dependency-clear first. A row shows the
// one action it needs — Run, or a link to the task it waits on, dimmed. A
// refused Run stays beside its row, its blocking tasks linked.
// On desktop the card opens with "Running now" while any agent is working;
// the mobile 2a design does not draw it.
import { computed } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import RunningNow from './RunningNow.vue'
import TaskRefusal from '../tasks/TaskRefusal.vue'
import { useTaskVerbs } from '../../composables/useTaskVerbs.js'
import { runNextSub } from '../../utils/pipelineFormat'

const props = defineProps({
  runNext: { type: Object, required: true },
  running: { type: Object, default: null },
  compact: { type: Boolean, default: false },
  tasksTo: { type: Object, required: true },
})
const emit = defineEmits(['ran'])

const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')
// Mobile shows ready rows only: they lead the server's order, so a prefix.
const rows = computed(() => (props.compact
  ? props.runNext.rows.filter(r => r.action.kind === 'run').slice(0, 3)
  : props.runNext.rows))

const { refusals, busyId, act } = useTaskVerbs(async () => emit('ran'))
</script>

<template>
  <section class="pl-panel rn" data-testid="run-next" aria-labelledby="rn-title">
    <RunningNow v-if="!compact && running?.total" :running="running" @stopped="emit('ran')" />
    <header class="pl-panel__head" :class="{ 'rn-head--after': !compact && running?.total }">
      <h2 id="rn-title" class="pl-panel__title">Run next</h2>
      <span class="pl-panel__sub" data-testid="run-next-count">{{ runNext.total }}</span>
      <router-link :to="tasksTo" class="pl-panel__link focus-visible:outline-2 focus-visible:outline-blue-500"
                   data-testid="run-next-all"
      >All in Tasks →</router-link>
    </header>
    <p v-if="!rows.length" class="pl-panel__empty" data-testid="run-next-empty">
      {{ compact && runNext.total ? 'Nothing is dependency-clear yet.' : 'Nothing armed — arm an idea to see it here.' }}
    </p>
    <ul v-else class="rn-rows">
      <li
        v-for="row in rows"
        :key="row.id"
        class="rn-row"
        :class="{ 'is-waiting': row.action.kind === 'wait' }"
        :data-testid="`run-next-row-${row.id}`"
      >
        <span v-if="!compact" class="rn-id">#{{ row.id }}</span>
        <router-link :to="`/tasks/${row.id}`" class="rn-main focus-visible:outline-2 focus-visible:outline-blue-500">
          <span class="rn-title">{{ row.title }}</span>
          <span class="rn-sub">{{ compact ? `#${row.id} · ` : '' }}{{ runNextSub(row) }}</span>
        </router-link>
        <router-link
          v-if="row.action.kind === 'wait'" :to="`/tasks/${row.action.on}`"
          class="rn-wait focus-visible:outline-2 focus-visible:outline-blue-500" :data-testid="`run-next-wait-${row.id}`"
        >
          <Icon name="dependency-tree" :size="12" />
          waits on #{{ row.action.on }}{{ row.action.on_status === 'running' ? ' (running)' : '' }}
        </router-link>
        <Button
          v-else-if="canWrite"
          size="sm"
          variant="secondary"
          class="rn-run"
          :class="{ 'rn-run--compact': compact }"
          :disabled="busyId === row.id"
          :aria-label="`Run #${row.id}`"
          :data-testid="`run-next-run-${row.id}`"
          @click="act('run', row)"
        >
          <Icon name="play" :size="11" /><span v-if="!compact">Run</span>
        </Button>
        <TaskRefusal
          v-if="refusals[row.id]" class="rn-refusal" :refusal="refusals[row.id]"
          :data-testid="`run-next-refusal-${row.id}`"
        />
      </li>
    </ul>
  </section>
</template>

<style scoped>
.rn-rows { display: flex; flex-direction: column; }
.rn-head--after { border-top: 1px solid var(--color-border-subtle); }
.rn-row {
    display: flex; align-items: center; gap: 0.6rem; min-width: 0;
    padding: 0.6rem 1.1rem; border-top: 1px solid var(--color-border-subtle);
}
.rn-row { flex-wrap: wrap; }
.rn-row.is-waiting { opacity: 0.55; }
.rn-row .rn-refusal { flex: 1 0 100%; margin-top: 0; }
.rn-wait:hover { color: var(--color-fg); text-decoration: underline; }
.rn-id { min-width: 2.25rem; font-size: 12px; color: var(--color-fg-faint); font-variant-numeric: tabular-nums; flex: none; }
.rn-main { flex: 1; min-width: 0; display: flex; flex-direction: column; text-decoration: none; }
.rn-title { font-size: 13.5px; font-weight: 500; color: var(--color-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rn-sub { font-size: 11.5px; color: var(--color-fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rn-main:hover .rn-title { text-decoration: underline; }
.rn-wait { display: inline-flex; align-items: center; gap: 0.3rem; flex: none; font-size: 12px; color: var(--color-fg-muted); }
.rn-row .rn-run {
    flex: none; gap: 0.3rem; color: var(--color-primary);
    background: var(--color-surface); border-color: var(--color-border);
}
.rn-row .rn-run--compact { width: 44px; height: 44px; }
</style>
