<script setup>
// When this task starts: once at a moment, on a repeat, or only inside a
// daily time window. Saved through the server, whose scheduler thread is the
// only thing that fires on time. A schedule starts the task's armed attempt,
// so an unarmed task is sent to the arm sheet as part of saving.
import { computed, onMounted, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import TaskScheduleRepeat from './TaskScheduleRepeat.vue'
import { localStamp, utcTitle } from '../../utils/time.js'
import {
  MODES, NIGHT_WINDOW, describe, draftFrom, oncePresets, scheduleBody, timeZones, untilLabel,
} from '../../utils/taskSchedule.js'

const props = defineProps({
  task: { type: Object, required: true },
  armed: { type: Object, default: null },
  canWrite: { type: Boolean, default: true },
})
const emit = defineEmits(['changed', 'arm'])

const saved = ref(null)
const draft = ref(draftFrom(null))
const upcoming = ref([])
const previewError = ref('')
const saveError = ref('')
const busy = ref(false)
// A repeat cannot be saved before the task has launch settings; the save
// resumes once the arm sheet hands them back.
const awaitingArm = ref(false)

const presets = oncePresets()
const zones = computed(() => timeZones(draft.value.tz))
const needsArm = computed(() => !props.armed && props.task.status !== 'running')
const isGate = computed(() => saved.value && saved.value.kind !== 'cron')
const gateState = computed(() => {
  if (!isGate.value || !saved.value.enabled) return ''
  if (saved.value.kind === 'at') return saved.value.gate_open ? 'start time reached' : ''
  return saved.value.gate_open ? 'window open now' : 'window closed'
})
const incomplete = computed(() => (draft.value.mode === 'once' && !(draft.value.date && draft.value.time))
  || (draft.value.mode === 'repeat' && draft.value.freq === 'weekly' && !draft.value.days.length))
const upcomingLabel = computed(() => ({ once: 'Starts', window: 'Next openings', repeat: 'Next runs' })[draft.value.mode])
const shown = computed(() => (draft.value.mode === 'once' ? upcoming.value.slice(0, 1) : upcoming.value))

async function load() {
  try {
    saved.value = await api.get(`/tasks/${props.task.id}/schedule`)
  } catch (e) {
    if (e.status !== 404) saveError.value = e.message
    saved.value = null
  }
  draft.value = draftFrom(saved.value)
}

async function preview() {
  if (incomplete.value) {
    upcoming.value = []
    previewError.value = ''
    return
  }
  const result = await api.post('/tasks/schedule/preview', scheduleBody(draft.value))
  previewError.value = result.ok === false ? result.msg : ''
  upcoming.value = result.ok === false ? [] : result.upcoming
}

async function done(result) {
  busy.value = false
  saveError.value = result?.ok === false ? result.msg : ''
  if (result?.ok === false) return false
  await load()
  emit('changed')
  return true
}

async function save() {
  busy.value = true
  const result = await api.put(`/tasks/${props.task.id}/schedule`, scheduleBody(draft.value))
  if (result?.ok === false && draft.value.mode === 'repeat' && needsArm.value) {
    busy.value = false
    awaitingArm.value = true
    emit('arm')
    return
  }
  if (await done(result) && needsArm.value) emit('arm')
}

async function toggle(on) {
  busy.value = true
  await done(await api.put(`/tasks/${props.task.id}/schedule`, { enabled: on }))
}

async function clear() {
  busy.value = true
  await done(await api.del(`/tasks/${props.task.id}/schedule`))
}

function patch(fields) { Object.assign(draft.value, fields) }

watch(() => props.armed, (now) => {
  if (now && awaitingArm.value) {
    awaitingArm.value = false
    save()
  }
})
watch(draft, preview, { deep: true })
onMounted(async () => { await load(); preview() })
</script>

<template>
  <Card data-testid="task-schedule">
    <h2 class="card-header">Schedule</h2>

    <div v-if="saved" class="tsk-state" data-testid="task-schedule-state">
      <span :class="{ 'tsk-off': !saved.enabled }">{{ describe(saved, localStamp) }}</span>
      <span v-if="!saved.enabled" class="tsk-pill">Paused</span>
      <span v-else-if="saved.next_fire_at" class="tsk-next">
        Next <span :title="utcTitle(saved.next_fire_at)">{{ localStamp(saved.next_fire_at) }}</span>
        · {{ untilLabel(saved.next_fire_at) }}
      </span>
      <span v-if="gateState" class="tsk-next">· {{ gateState }}</span>
    </div>
    <p v-if="saved && saved.enabled && isGate && needsArm" class="tsk-hint" data-testid="task-schedule-arm-hint">
      Not armed yet — it will start on schedule once it has launch settings.
      <Button variant="ghost" size="sm" @click="emit('arm')">Choose launch settings</Button>
    </p>
    <p v-if="awaitingArm" class="tsk-hint">Choose launch settings to finish scheduling this repeat.</p>

    <fieldset class="tsk-form" :disabled="!canWrite || busy">
      <div class="segmented tsk-modes" role="group" aria-label="Schedule type">
        <Button
          v-for="m in MODES" :key="m.value" variant="ghost" size="sm"
          class="segmented-item" :class="{ 'is-active': draft.mode === m.value }"
          :aria-pressed="draft.mode === m.value" :data-testid="`task-schedule-mode-${m.value}`"
          @click="draft.mode = m.value"
        >{{ m.label }}</Button>
      </div>

      <template v-if="draft.mode === 'once'">
        <div class="tsk-row">
          <span class="tsk-field">Start at</span>
          <Input v-model="draft.date" type="date" class="tsk-date" aria-label="Date" data-testid="task-schedule-date" />
          <Input v-model="draft.time" type="time" class="tsk-time" aria-label="Time" data-testid="task-schedule-time" />
        </div>
        <div class="tsk-row">
          <span class="tsk-field" />
          <Button v-for="p in presets" :key="p.label" variant="ghost" size="sm" @click="patch({ date: p.date, time: p.time })">
            {{ p.label }}
          </Button>
        </div>
      </template>

      <TaskScheduleRepeat v-else-if="draft.mode === 'repeat'" :draft="draft" @update="patch" />

      <template v-else>
        <div class="tsk-row">
          <span class="tsk-field">Between</span>
          <Input v-model="draft.window_start" type="time" class="tsk-time" aria-label="Window start" data-testid="task-schedule-start" />
          <span>and</span>
          <Input v-model="draft.window_end" type="time" class="tsk-time" aria-label="Window end" data-testid="task-schedule-end" />
          <Button variant="ghost" size="sm" @click="patch({ window_start: NIGHT_WINDOW.window_start, window_end: NIGHT_WINDOW.window_end })">
            Overnight
          </Button>
        </div>
        <p class="tsk-note tsk-explain">
          Each time it is armed, the task waits for this window before starting. Work already running is never interrupted.
        </p>
      </template>

      <label v-if="draft.mode !== 'once'" class="tsk-row">
        <span class="tsk-field">Time zone</span>
        <Select v-model="draft.tz" :options="zones" aria-label="Time zone" data-testid="task-schedule-tz" />
      </label>
    </fieldset>

    <p v-if="previewError" class="tsk-error" role="alert" data-testid="task-schedule-preview-error">{{ previewError }}</p>
    <div v-else-if="shown.length" class="tsk-upcoming" data-testid="task-schedule-upcoming">
      <span class="tsk-label">{{ upcomingLabel }}</span>
      <span v-for="when in shown" :key="when" :title="utcTitle(when)">{{ localStamp(when) }}</span>
    </div>
    <p v-if="saveError" class="tsk-error" role="alert" data-testid="task-schedule-error">{{ saveError }}</p>

    <div v-if="canWrite" class="tsk-actions">
      <Button
        variant="primary" size="sm" :disabled="busy || incomplete || !!previewError"
        data-testid="task-schedule-save" @click="save"
      >{{ saved ? 'Update schedule' : 'Save schedule' }}</Button>
      <Button v-if="saved" variant="ghost" size="sm" :disabled="busy" @click="toggle(!saved.enabled)">
        {{ saved.enabled ? 'Pause' : 'Resume' }}
      </Button>
      <Button v-if="saved" variant="ghost" size="sm" :disabled="busy" data-testid="task-schedule-clear" @click="clear">Remove</Button>
    </div>
  </Card>
</template>

<style scoped>
.tsk-state {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.375rem;
    font-size: 0.8125rem;
    color: var(--color-fg);
    margin-bottom: 0.625rem;
}
.tsk-next { color: var(--color-fg-muted); font-variant-numeric: tabular-nums; }
.tsk-pill {
    padding: 0 0.375rem;
    font-size: 0.6875rem;
    color: var(--color-fg-subtle);
    background: var(--color-surface-2);
    border-radius: var(--radius-sm);
}
.tsk-hint {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.25rem 0.5rem;
    font-size: 0.8125rem;
    color: var(--color-warning-strong);
    margin-bottom: 0.625rem;
}
.tsk-off { color: var(--color-fg-faint); }
.tsk-form { display: flex; flex-direction: column; gap: 0.625rem; border: 0; padding: 0; margin: 0; }
.tsk-modes { align-self: flex-start; }
.tsk-form :deep(.tsk-row) { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; font-size: 0.8125rem; color: var(--color-fg); }
.tsk-form :deep(.tsk-field) { min-width: 5rem; color: var(--color-fg-subtle); }
.tsk-form :deep(.tsk-note) { font-size: 0.75rem; color: var(--color-fg-subtle); }
.tsk-form :deep(.tsk-time) { width: 7.5rem; }
.tsk-explain { margin: 0; padding-left: 5.5rem; }
.tsk-date { width: 10rem; }
.tsk-upcoming {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem 0.75rem;
    margin-top: 0.75rem;
    font-size: 0.75rem;
    color: var(--color-fg-muted);
    font-variant-numeric: tabular-nums;
}
.tsk-label { color: var(--color-fg-subtle); }
.tsk-error { margin-top: 0.5rem; font-size: 0.75rem; color: var(--color-danger); }
.tsk-actions { display: flex; gap: 0.375rem; margin-top: 0.875rem; }
</style>
