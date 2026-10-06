<script setup>
// "Run next" (1a / 2a): armed tasks, dependency-clear first. A row shows the
// one action it needs — Run, or a link to the task it waits on, dimmed. A
// refused Run stays beside its row, its blocking tasks linked.
// The card opens with "Running now" while any agent is working. Mobile shows
// the same blocks and rows as desktop; `compact` only changes row density.
import { computed } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import RunningNow from './RunningNow.vue'
import TaskRefusal from '../tasks/TaskRefusal.vue'
import { useTaskVerbs } from '../../composables/useTaskVerbs.js'
import { runNextSub } from '../../utils/pipelineFormat'
import { taskTitle } from '../../utils/taskTitle.js'

const props = defineProps({
  runNext: { type: Object, required: true },
  running: { type: Object, default: null },
  compact: { type: Boolean, default: false },
  tasksTo: { type: Object, required: true },
})
const emit = defineEmits(['ran'])

const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')
const { refusals, busyId, act } = useTaskVerbs(async () => emit('ran'))

// "Run next" is the Ready group of the next-step list; with nothing armed, the
// ideas waiting to be armed are the place to go.
const groupTo = group => ({ path: props.tasksTo.path,
                            query: { ...props.tasksTo.query, view: 'list', group } })

// The feature half of the second line is its own link, so it is not folded
// into the text `runNextSub` prints.
const subOf = row => runNextSub({ ...row, feature_id: null })
  .replace(/^No feature$/, '')

// The first row is the one to run when its dependencies are clear; a theme
// may lift it into an "up next" hero with the panel's one primary key.
const isHero = (row, i) => i === 0 && row.action.kind === 'run'
</script>

<template>
  <section class="pl-panel rn" data-pl-part="panel" data-testid="run-next" aria-labelledby="rn-title">
    <RunningNow v-if="running?.total" :running="running" :compact="compact" @stopped="emit('ran')" />
    <header class="pl-panel__head" data-pl-part="panel-head" :class="{ 'rn-head--after': running?.total }">
      <h2 id="rn-title" class="pl-panel__title" data-pl-part="panel-title" data-pl-chapter="1-2">Run next</h2>
      <span class="pl-panel__sub" data-pl-part="panel-meta" data-pl-label="ready" data-testid="run-next-count">{{ runNext.ready }}</span>
      <router-link
        v-if="runNext.blocked" :to="groupTo('blocked')"
        class="pl-panel__sub rn-link focus-visible:outline-2 focus-visible:outline-blue-500" data-pl-part="panel-meta"
        data-testid="run-next-blocked"
      >· {{ runNext.blocked }} blocked</router-link>
      <router-link :to="groupTo('ready')" class="pl-panel__link focus-visible:outline-2 focus-visible:outline-blue-500" data-pl-part="panel-link"
                   data-testid="run-next-all"
      >All in Tasks →</router-link>
    </header>
    <p v-if="!runNext.rows.length" class="pl-panel__empty" data-pl-part="panel-empty" data-testid="run-next-empty">
      Nothing armed —
      <router-link :to="groupTo('ideas')" class="rn-link" data-testid="run-next-ideas">arm an idea</router-link>
      to see it here.
    </p>
    <ul v-else class="rn-rows">
      <li
        v-for="(row, i) in runNext.rows"
        :key="row.id"
        class="rn-row"
        data-pl-part="row"
        :data-pl-hero="isHero(row, i) || undefined"
        :class="{ 'is-waiting': row.action.kind === 'wait' }"
        :data-testid="`run-next-row-${row.id}`"
      >
        <span v-if="!compact" class="rn-id" data-pl-part="row-id">#{{ row.id }}</span>
        <span class="rn-main">
          <router-link :to="`/tasks/${row.id}`" class="rn-title focus-visible:outline-2 focus-visible:outline-blue-500" data-pl-part="row-title">
            {{ taskTitle(row) }}
          </router-link>
          <span class="rn-sub">
            {{ compact ? `#${row.id} · ` : '' }}{{ subOf(row) }}<template v-if="row.feature_id">{{ subOf(row) ? ' · ' : '' }}<router-link
              :to="`/features/${row.feature_id}`"
              class="rn-link"
              :data-testid="`run-next-feature-${row.id}`"
            >#{{ row.feature_id }} {{ row.feature_title || '' }}</router-link>{{ row.step ? ` · step ${row.step.n} of ${row.step.of}` : '' }}</template>
            <template v-else-if="!subOf(row)">No feature</template>
          </span>
        </span>
        <router-link
          v-if="row.action.kind === 'wait'" :to="`/tasks/${row.action.on}`"
          class="rn-wait focus-visible:outline-2 focus-visible:outline-blue-500" data-pl-part="wait" :data-testid="`run-next-wait-${row.id}`"
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
          :data-pl-part="isHero(row, i) ? 'key--primary' : 'key'"
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
.rn-title { font-size: 13.5px; font-weight: 500; color: var(--color-fg); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rn-sub { font-size: 11.5px; color: var(--color-fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rn-title:hover { text-decoration: underline; }
.rn-link { color: inherit; text-decoration: underline; text-decoration-color: var(--color-border-strong); }
.rn-link:hover { color: var(--color-fg); }
.rn-wait { display: inline-flex; align-items: center; gap: 0.3rem; flex: none; font-size: 12px; color: var(--color-fg-muted); }
.rn-row .rn-run {
    flex: none; gap: 0.3rem; color: var(--color-primary);
    background: var(--color-surface); border-color: var(--color-border);
}
.rn-row .rn-run--compact { width: 44px; height: 44px; }
</style>
