<script setup>
// When this task may start (a gate: not before a time, or only inside a daily
// window) or when it starts again (cron). Saved through the server, whose
// scheduler thread is the only thing that fires on time.
import { computed, onMounted, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Checkbox from '../ui/Checkbox.vue'
import Input from '../ui/Input.vue'
import RadioGroup from '../ui/RadioGroup.vue'
import Select from '../ui/Select.vue'
import { localStamp, utcTitle } from '../../utils/time.js'
import { CRON_PRESETS, draftFrom, scheduleBody, untilLabel } from '../../utils/taskSchedule.js'

const props = defineProps({
  task: { type: Object, required: true },
  armed: { type: Object, default: null },
  canWrite: { type: Boolean, default: true },
})
const emit = defineEmits(['changed'])

const MODES = [
  { value: 'at', label: '不早于某个时间' },
  { value: 'window', label: '只在时间窗内启动' },
  { value: 'cron', label: '周期执行' },
]

const saved = ref(null)
const draft = ref(draftFrom(null))
const upcoming = ref([])
const previewError = ref('')
const saveError = ref('')
const busy = ref(false)

const isGate = computed(() => saved.value && saved.value.kind !== 'cron')
// An `at` gate has no window to speak of: it is either not yet that moment or
// already past it.
const gateState = computed(() => saved.value.kind === 'at'
  ? (saved.value.gate_open ? '已到时间' : '未到时间')
  : `时间窗${saved.value.gate_open ? '开启中' : '关闭中'}`)
const pendingArm = computed(() => isGate.value && saved.value.enabled && !props.armed
  && props.task.status !== 'running')

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
  // An untouched `at` draft is not a mistake yet: asking the server about it
  // would paint an error over a form nobody has filled in.
  if (draft.value.kind === 'at' && !draft.value.run_at) {
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
  if (result?.ok === false) return
  await load()
  emit('changed')
}

async function save() {
  busy.value = true
  await done(await api.put(`/tasks/${props.task.id}/schedule`, scheduleBody(draft.value)))
}

async function toggle(on) {
  busy.value = true
  await done(await api.put(`/tasks/${props.task.id}/schedule`, { enabled: on }))
}

async function clear() {
  busy.value = true
  await done(await api.del(`/tasks/${props.task.id}/schedule`))
}

watch(draft, preview, { deep: true })
onMounted(async () => { await load(); preview() })
</script>

<template>
  <Card data-testid="task-schedule">
    <h2 class="card-header">定时</h2>
    <p v-if="saved" class="tsk-state" data-testid="task-schedule-state">
      <span :class="{ 'tsk-off': !saved.enabled }">{{ saved.enabled ? '已启用' : '已关闭' }}</span>
      <template v-if="saved.enabled && saved.next_fire_at">
        · 下次 <span :title="utcTitle(saved.next_fire_at)">{{ localStamp(saved.next_fire_at) }}</span>
        ({{ untilLabel(saved.next_fire_at) }})
      </template>
      <template v-if="isGate"> · {{ gateState }}</template>
    </p>
    <p v-if="pendingArm" class="tsk-hint" data-testid="task-schedule-arm-hint">
      arm 后在 {{ localStamp(saved.next_fire_at || saved.run_at) }} 启动
    </p>

    <fieldset class="tsk-form" :disabled="!canWrite || busy">
      <RadioGroup v-model="draft.kind" :options="MODES" inline name="schedule-kind" />
      <label v-if="draft.kind === 'at'" class="tsk-row">
        <span>开始时间</span>
        <Input v-model="draft.run_at" type="datetime-local" class="tsk-at" data-testid="task-schedule-at" />
      </label>
      <div v-else-if="draft.kind === 'window'" class="tsk-row">
        <span>时间窗</span>
        <Input v-model="draft.window_start" type="time" class="tsk-time" data-testid="task-schedule-start" />
        <span>–</span>
        <Input v-model="draft.window_end" type="time" class="tsk-time" data-testid="task-schedule-end" />
        <Button variant="ghost" size="sm" @click="Object.assign(draft, { window_start: '22:00', window_end: '06:00' })">夜间 22:00–06:00</Button>
      </div>
      <div v-else class="tsk-row">
        <span>cron</span>
        <Select
          model-value="" :options="[{ value: '', label: '预设…' }, ...CRON_PRESETS]"
          aria-label="cron 预设" @update:model-value="v => v && (draft.cron_expr = v)"
        />
        <Input v-model="draft.cron_expr" class="tsk-cron" data-testid="task-schedule-cron" />
      </div>
      <label class="tsk-row">
        <span>时区</span>
        <Input v-model="draft.tz" class="tsk-tz" data-testid="task-schedule-tz" />
      </label>
      <Checkbox v-model="draft.enabled" label="启用" />
    </fieldset>

    <p v-if="previewError" class="tsk-error" role="alert" data-testid="task-schedule-preview-error">{{ previewError }}</p>
    <div v-else-if="upcoming.length" class="tsk-upcoming" data-testid="task-schedule-upcoming">
      <span class="tsk-label">接下来 {{ upcoming.length }} 次</span>
      <span v-for="when in upcoming" :key="when" :title="utcTitle(when)">{{ localStamp(when) }}</span>
    </div>
    <p v-if="saveError" class="tsk-error" role="alert" data-testid="task-schedule-error">{{ saveError }}</p>

    <div v-if="canWrite" class="tsk-actions">
      <Button size="sm" :disabled="busy || !!previewError" data-testid="task-schedule-save" @click="save">保存</Button>
      <Button v-if="saved" variant="ghost" size="sm" :disabled="busy" @click="toggle(!saved.enabled)">
        {{ saved.enabled ? '关闭' : '启用' }}
      </Button>
      <Button v-if="saved" variant="ghost" size="sm" :disabled="busy" data-testid="task-schedule-clear" @click="clear">清除</Button>
    </div>
  </Card>
</template>

<style scoped>
.tsk-state, .tsk-hint { font-size: 0.8125rem; color: var(--color-fg-muted); margin-bottom: 0.5rem; }
.tsk-hint { color: var(--color-warning-strong); }
.tsk-off { color: var(--color-fg-faint); }
.tsk-form { display: flex; flex-direction: column; gap: 0.5rem; border: 0; padding: 0; margin: 0; }
.tsk-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; font-size: 0.8125rem; color: var(--color-fg); }
.tsk-row > span:first-child { min-width: 4rem; color: var(--color-fg-subtle); }
.tsk-cron { font-family: var(--font-mono, ui-monospace, monospace); width: 12rem; }
.tsk-tz { width: 12rem; }
.tsk-time { width: 8.5rem; }
.tsk-at { width: 14rem; }
.tsk-upcoming {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem 0.75rem;
    margin-top: 0.5rem;
    font-size: 0.75rem;
    color: var(--color-fg-muted);
    font-variant-numeric: tabular-nums;
}
.tsk-label { color: var(--color-fg-subtle); }
.tsk-error { margin-top: 0.5rem; font-size: 0.75rem; color: var(--color-danger); }
.tsk-actions { display: flex; gap: 0.375rem; margin-top: 0.75rem; }
</style>
