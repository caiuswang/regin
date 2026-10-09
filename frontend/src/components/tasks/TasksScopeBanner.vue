<script setup>
// The head of a feature-scoped Tasks page. Arriving from a feature's "Open in
// Tasks", the reader needs to know which feature this is, that the rest of the
// backlog is withheld, and what to do next — so the next step is the page's
// primary button (F155 R1), not a line of prose. An empty scope repeats it
// under the head rather than leaving a blank list that only says "press c".
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import Button from '../ui/Button.vue'
import { useTaskVerbs } from '../../composables/useTaskVerbs.js'
import { scopeHeadAction } from '../../utils/scopeHeadAction.js'

const props = defineProps({
  featureId: { type: String, default: '' },
  // `GET /api/features/<id>/next-step`; null until it answers.
  head: { type: Object, default: null },
  canWrite: { type: Boolean, default: false },
})
const emit = defineEmits(['new-task', 'changed'])

const router = useRouter()
const verbs = useTaskVerbs(() => emit('changed'))

const action = computed(() => (props.canWrite && props.head
  ? scopeHeadAction(props.head.id, props.head.next_step) : null))
const empty = computed(() => props.head?.task_count === 0)
const refusal = computed(() => {
  const id = action.value?.runTaskId
  return id ? verbs.refusals.value[id]?.message : ''
})

function go() {
  const a = action.value
  if (a.runTaskId) verbs.act('run', { id: a.runTaskId })
  else router.push(a.to)
}
</script>

<template>
  <section v-if="featureId" class="tsh" data-testid="tasks-scoped-to">
    <div class="tsh-row">
      <p class="tsh-name">
        <router-link :to="`/features/${featureId}`" data-testid="tasks-scope-feature">
          <strong>#{{ featureId }}</strong>{{ head ? ` ${head.title}` : '' }}
        </router-link>
        <span v-if="head" class="tsh-stage" data-testid="tasks-scope-stage"> · {{ head.stage }}</span>
        <span class="tsh-out"> · <router-link to="/tasks">every task</router-link></span>
      </p>
      <span class="tsh-verbs">
        <Button
          v-if="canWrite" variant="secondary" size="sm"
          data-testid="tasks-scope-new" @click="emit('new-task')"
        >New task in #{{ featureId }}</Button>
        <Button
          v-if="action" variant="primary"
          :disabled="verbs.busyId.value === action.runTaskId && !!action.runTaskId"
          data-testid="tasks-scope-primary" :data-kind="head.next_step.kind" @click="go"
        >{{ action.label }}</Button>
      </span>
    </div>
    <p v-if="refusal" class="tsh-refusal" role="alert" data-testid="tasks-scope-refusal">{{ refusal }}</p>
    <p v-if="empty" class="tsh-empty" data-testid="tasks-scope-empty">
      No tasks under #{{ featureId }} yet.
      <template v-if="action">
        Its next step:
        <Button
          variant="primary" size="sm" class="tsh-empty-go"
          data-testid="tasks-scope-empty-primary" @click="go"
        >{{ action.label }}</Button>
      </template>
      <template v-else-if="canWrite">Add one with <kbd>c</kbd> — it is filed under #{{ featureId }}.</template>
    </p>
  </section>
</template>

<style scoped>
.tsh-row {
    display: flex; align-items: center; justify-content: space-between;
    gap: 0.5rem 0.75rem; flex-wrap: wrap;
}
.tsh-name { font-size: 0.8125rem; color: var(--color-fg); min-width: 0; }
.tsh-name a { color: inherit; text-decoration: none; }
.tsh-name a:hover { text-decoration: underline; }
.tsh-name strong { font-family: var(--font-mono, monospace); }
.tsh-stage, .tsh-out { font-size: 11.5px; color: var(--color-fg-muted); }
.tsh-out a { color: var(--color-info); }
.tsh-verbs { display: inline-flex; align-items: center; gap: 0.35rem; flex-wrap: wrap; }
.tsh-refusal {
    margin-top: 0.4rem; font-size: 11.5px; color: var(--color-amber-800);
    background: var(--color-amber-50); border-radius: 0.4rem; padding: 0.35rem 0.5rem;
}
.tsh-empty { margin-top: 0.5rem; font-size: 0.8125rem; color: var(--color-fg-muted); }
.tsh-empty-go { margin-left: 0.5rem; }
</style>
