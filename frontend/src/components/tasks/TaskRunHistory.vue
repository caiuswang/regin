<script setup>
// One task's attempts. `status` and `live` are shown as two separate facts,
// because they answer different questions: the row records what the attempt
// claimed, and `live` is the server's merged answer about whether anything is
// still behind it.
//
// Each attempt links to /trace/sessions/<trace_id> — the mounted trace route.
import { onMounted } from 'vue'
import Badge from '../Badge.vue'
import Dialog from '../ui/Dialog.vue'
import { useTaskRuns } from '../../composables/useTaskRuns.js'
import { localStamp, utcTitle } from '../../utils/time.js'
import { taskTitle } from '../../utils/taskTitle.js'

const props = defineProps({
  task: { type: Object, required: true },
})
const emit = defineEmits(['close'])

const { runs, loading, error, load } = useTaskRuns()

const OPEN = ['starting', 'running']
const RUN_COLOR = { done: 'green', failed: 'red', running: 'purple', starting: 'yellow' }

function runColor(status) {
  return RUN_COLOR[status] || 'gray'
}

function liveLabel(run) {
  if (run.live) return { color: 'green', label: 'live' }
  if (OPEN.includes(run.status)) return { color: 'red', label: 'not live' }
  return null
}

onMounted(() => load(props.task.id))
</script>

<template>
  <Dialog
    :open="true"
    title="Run history"
    :description="taskTitle(task)"
    @update:open="v => !v && emit('close')"
  >
    <div v-if="loading" class="task-runs-note">reading attempts…</div>
    <div v-else-if="error" class="live-launch-warn" data-testid="task-runs-error">
      {{ error }}
    </div>
    <div v-else-if="!runs.length" class="task-runs-note" data-testid="task-runs-empty">
      This task has never been run.
    </div>
    <ul v-else class="task-runs" data-testid="task-runs">
      <li v-for="run in runs" :key="run.id" class="task-run">
        <div class="task-run-head">
          <span class="task-run-attempt">attempt {{ run.attempt }}</span>
          <Badge :color="runColor(run.status)" :label="run.status" />
          <Badge
            v-if="liveLabel(run)"
            :color="liveLabel(run).color"
            :label="liveLabel(run).label"
          />
        </div>
        <div class="task-run-facts">
          <span :title="utcTitle(run.started_at)">{{ localStamp(run.started_at) }}</span>
          <span v-if="run.ended_at" :title="utcTitle(run.ended_at)">→ {{ localStamp(run.ended_at) }}</span>
        </div>
        <p v-if="run.exit_reason" class="task-run-reason">{{ run.exit_reason }}</p>
        <RouterLink
          v-if="run.trace_id"
          class="task-run-link"
          :to="`/trace/sessions/${run.trace_id}`"
          :data-testid="`task-run-trace-${run.attempt}`"
        >open the session trace</RouterLink>
        <span v-else class="task-run-notrace" :data-testid="`task-run-notrace-${run.attempt}`">
          no session was minted for this attempt
        </span>
      </li>
    </ul>
  </Dialog>
</template>

<style scoped>
.task-runs-note {
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
.task-runs {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    max-height: 24rem;
    overflow-y: auto;
    list-style: none;
    padding: 0;
    margin: 0;
}
.task-run {
    padding: 0.625rem 0.75rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    background: var(--color-surface-2);
    min-width: 0;
}
.task-run-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.375rem;
}
.task-run-attempt {
    font-size: 0.8125rem;
    font-weight: 600;
    color: var(--color-fg);
}
.task-run-facts {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.25rem;
    font-size: 0.6875rem;
    color: var(--color-fg-faint);
    font-variant-numeric: tabular-nums;
}
.task-run-reason {
    margin-top: 0.25rem;
    font-size: 0.75rem;
    color: var(--color-fg-muted);
    overflow-wrap: anywhere;
}
.task-run-link {
    display: inline-block;
    margin-top: 0.375rem;
    font-size: 0.75rem;
    color: var(--color-primary);
    text-decoration: underline;
}
.task-run-notrace {
    display: inline-block;
    margin-top: 0.375rem;
    font-size: 0.75rem;
    color: var(--color-fg-faint);
}
</style>
