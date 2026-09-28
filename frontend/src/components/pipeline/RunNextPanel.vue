<script setup>
// "Run next" (1a / 2a): armed and queued tasks, dependency-clear first. A row
// shows the one action it needs — Run, or the task it waits on, dimmed.
// On desktop the card opens with "Running now" while any agent is working;
// the mobile 2a design does not draw it.
import { computed, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import RunningNow from './RunningNow.vue'
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

const busy = ref(null)
const refusal = ref('')
async function run(row) {
  busy.value = row.id
  refusal.value = ''
  try {
    const body = await api.post(`/tasks/${row.id}/run`, {})
    if (body?.ok === false) refusal.value = `#${row.id}: ${body.msg || 'the run was refused'}`
    else emit('ran', row.id)
  } catch (e) {
    refusal.value = `#${row.id}: ${e?.message || e}`
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <section class="pl-panel rn" data-testid="run-next" aria-labelledby="rn-title">
    <RunningNow v-if="!compact && running?.total" :running="running" />
    <header class="pl-panel__head" :class="{ 'rn-head--after': !compact && running?.total }">
      <h2 id="rn-title" class="pl-panel__title">Run next</h2>
      <span class="pl-panel__sub" data-testid="run-next-count">{{ runNext.total }}</span>
      <router-link :to="tasksTo" class="pl-panel__link focus-visible:outline-2 focus-visible:outline-blue-500"
                   data-testid="run-next-all"
      >All in Tasks →</router-link>
    </header>
    <p v-if="!rows.length" class="pl-panel__empty" data-testid="run-next-empty">
      {{ compact && runNext.total ? 'Nothing is dependency-clear yet.' : 'Nothing armed or queued — arm an idea to see it here.' }}
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
        <span v-if="row.action.kind === 'wait'" class="rn-wait" :data-testid="`run-next-wait-${row.id}`">
          <Icon name="dependency-tree" :size="12" />
          waits on #{{ row.action.on }}{{ row.action.on_status === 'running' ? ' (running)' : '' }}
        </span>
        <Button
          v-else-if="canWrite"
          size="sm"
          variant="secondary"
          class="rn-run"
          :class="{ 'rn-run--compact': compact }"
          :disabled="busy === row.id"
          :aria-label="`Run #${row.id}`"
          :data-testid="`run-next-run-${row.id}`"
          @click="run(row)"
        >
          <Icon name="play" :size="11" /><span v-if="!compact">Run</span>
        </Button>
      </li>
    </ul>
    <p v-if="refusal" class="pl-panel__refusal" role="alert">{{ refusal }}</p>
  </section>
</template>

<style scoped>
.rn-rows { display: flex; flex-direction: column; }
.rn-head--after { border-top: 1px solid var(--color-border-subtle); }
.rn-row {
    display: flex; align-items: center; gap: 0.6rem; min-width: 0;
    padding: 0.6rem 1.1rem; border-top: 1px solid var(--color-border-subtle);
}
.rn-row.is-waiting { opacity: 0.55; }
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
