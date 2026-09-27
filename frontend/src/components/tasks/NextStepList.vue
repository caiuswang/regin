<script setup>
// The Tasks tab's default reading: the open backlog grouped by what each task
// needs next — Ready to run, Running, Blocked, Ideas — every header counted by
// the server (`/api/tasks?group=next_step`), done and failed counted and
// hidden. Each group pages on its own; Ideas opens on three.
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import NextStepRow from './NextStepRow.vue'
import TaskArmSheet from './TaskArmSheet.vue'
import { useConfirm } from '../../composables/useConfirm.js'
import { useFlash } from '../../composables/useFlash.js'
import { useNextStep } from '../../composables/useTaskViews.js'
import { useTaskExec } from '../../composables/useTaskExec.js'
import { invalidatePipelineCounts } from '../../composables/usePipelineCounts.js'

const props = defineProps({
  params: { type: Object, required: true },
})
const emit = defineEmits(['count'])

const params = computed(() => props.params)
const feed = useNextStep(params)
const { confirm } = useConfirm()
const { flash } = useFlash()
const collapsed = ref({})
const arming = ref(null)
const runningAll = ref(false)

const groups = computed(() => (feed.body.value?.groups || []).filter(g => g.n > 0))
const empty = computed(() => feed.body.value && !groups.value.length)

async function reload() {
  await feed.load()
  const body = feed.body.value
  emit('count', body && !feed.error.value
    ? `${body.open} open · ${body.done_hidden} done hidden` : '')
}

async function afterWrite() {
  invalidatePipelineCounts()
  await reload()
}

const { refusals, busyId, control } = useTaskExec(afterWrite)

function moreLabel(group) {
  return group.key === 'ideas'
    ? `${group.more} more ideas · sorted by priority`
    : `${group.more} more`
}

async function runAll(group) {
  const ok = await confirm('Run all ready tasks',
    `Start ${group.n} ${group.n === 1 ? 'run' : 'runs'} — every task in Ready to run?`)
  if (!ok) return
  runningAll.value = true
  try {
    const ids = await feed.readyIds()
    const results = []
    for (const id of ids) results.push(await api.post(`/tasks/${id}/run`))
    const refused = results.filter(r => r.ok === false).length
    flash(refused
      ? `${ids.length - refused} started · ${refused} refused`
      : `${ids.length} ${ids.length === 1 ? 'run' : 'runs'} started`, refused ? 'error' : undefined)
  } finally {
    runningAll.value = false
    await afterWrite()
  }
}

function toggle(key) {
  collapsed.value = { ...collapsed.value, [key]: !collapsed.value[key] }
}

async function armed() {
  arming.value = null
  await afterWrite()
}

watch(() => JSON.stringify(props.params), reload, { immediate: true })

defineExpose({ reload })
</script>

<template>
  <div class="nsl" data-testid="next-step-list">
    <p v-if="feed.error.value" class="nsl-note" role="alert" data-testid="next-step-error">
      Could not read the task list — {{ feed.error.value }}
      <Button variant="link" size="sm" @click="reload">Retry</Button>
    </p>
    <p v-else-if="!feed.body.value" class="nsl-note">Loading tasks…</p>
    <p v-else-if="empty" class="nsl-note" data-testid="next-step-empty">
      No open tasks match. Press <kbd>c</kbd> to capture one.
    </p>

    <section
      v-for="group in groups"
      :key="group.key"
      class="nsl-group"
      :data-testid="`next-step-group-${group.key}`"
    >
      <header class="nsl-head">
        <Button
          variant="ghost"
          size="sm"
          class="nsl-toggle"
          :aria-expanded="!collapsed[group.key]"
          :data-testid="`next-step-toggle-${group.key}`"
          @click="toggle(group.key)"
        >
          <Icon :name="collapsed[group.key] ? 'chevron-right' : 'chevron-down'" :size="14" />
          <span class="nsl-label">{{ group.label }}</span>
          <span class="nsl-n" data-testid="next-step-n">{{ group.n }}</span>
        </Button>
        <span v-if="group.hint" class="nsl-hint">{{ group.hint }}</span>
        <Button
          v-if="group.key === 'ready'"
          variant="link"
          size="sm"
          class="nsl-bulk"
          :disabled="runningAll"
          data-testid="next-step-run-all"
          @click="runAll(group)"
        >Run all {{ group.n }}</Button>
      </header>
      <template v-if="!collapsed[group.key]">
        <ul class="nsl-rows">
          <NextStepRow
            v-for="task in group.rows"
            :key="task.id"
            :task="task"
            :busy="busyId === task.id || runningAll"
            :refusal="refusals[task.id] || null"
            @run="t => control(t, 'run')"
            @arm="t => (arming = t)"
          />
        </ul>
        <Button
          v-if="group.more > 0"
          variant="ghost"
          size="sm"
          class="nsl-more"
          :data-testid="`next-step-more-${group.key}`"
          @click="feed.more(group.key)"
        >{{ moreLabel(group) }}</Button>
      </template>
    </section>

    <TaskArmSheet v-if="arming" :task="arming" @close="arming = null" @armed="armed" />
  </div>
</template>

<style scoped>
.nsl-note {
    padding: 1.25rem 0;
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
.nsl-head {
    display: flex;
    align-items: center;
    gap: 0.625rem;
    padding: 0.625rem 0 0.25rem;
}
.nsl .nsl-toggle {
    gap: 0.625rem;
    margin-left: -0.5rem;
    padding-inline: 0.5rem;
    color: var(--color-fg);
}
.nsl-label { font-size: 0.8125rem; font-weight: 600; }
.nsl-n {
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
    font-variant-numeric: tabular-nums;
}
.nsl-hint {
    overflow: hidden;
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
    text-overflow: ellipsis;
    white-space: nowrap;
}
.nsl .nsl-bulk {
    margin-left: auto;
    padding-inline: 0;
    font-size: 0.75rem;
    font-weight: 500;
}
.nsl-rows {
    margin: 0;
    padding: 0;
    list-style: none;
}
.nsl .nsl-more {
    width: 100%;
    justify-content: flex-start;
    padding-left: 3rem;
    font-size: 0.75rem;
    font-weight: 400;
    color: var(--color-fg-subtle);
    border-top: 1px solid var(--color-border-subtle);
    border-radius: 0;
}

@media (max-width: 767px) {
    .nsl-hint { display: none; }
    .nsl .nsl-bulk,
    .nsl .nsl-toggle { min-height: 2.75rem; }
    .nsl .nsl-bulk { font-size: 0.8125rem; font-weight: 600; }
    .nsl .nsl-more { min-height: 2.75rem; padding-left: 0; }
}
</style>
