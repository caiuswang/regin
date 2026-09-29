<script setup>
// One page of rows.
//
// Both list shapes — the flat feed and each grouped feed — render through here
// so a row looks and behaves the same in either.
//
// There is no hierarchy here and there was never one: a task has attempts, not
// child tasks, and its attempts are a detail the run history sheet owns. The
// expand set, the flip() and the `:expanded`/`@expand` pair that used to sit
// here drove a control TaskRow never had and an emit it never sent.
import TaskRow from './TaskRow.vue'

defineProps({
  tasks: { type: Array, required: true },
  liveness: { type: Object, default: () => ({}) },
  refusals: { type: Object, default: () => ({}) },
  selection: { type: Set, default: () => new Set() },
  busyId: { type: Number, default: null },
  projectFor: { type: Function, default: () => null },
})
const emit = defineEmits(['verb', 'open', 'focus', 'select'])
</script>

<template>
  <TaskRow
    v-for="task in tasks"
    :key="task.id"
    :task="task"
    :liveness="liveness[task.id] || null"
    :refusal="refusals[task.id] || null"
    :project="projectFor(task)"
    :selected="selection.has(task.id)"
    :busy="busyId === task.id"
    @verb="(v, t) => emit('verb', v, t)"
    @open="(kind, t) => emit('open', kind, t)"
    @focus="id => emit('focus', id)"
    @select="t => emit('select', t)"
  />
</template>
