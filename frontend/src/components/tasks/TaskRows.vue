<script setup>
// One page of rows, with a feature's runs unfolded beneath it on demand.
//
// Both list shapes — the flat feed and each grouped feed — render through
// here so that expanding a feature works the same way in either. The child
// rows are fetched when the chevron is pressed and forgotten when it is
// pressed again: the runs of a feature are a detail the operator asked for,
// not a second page loaded on the chance they will.
import { ref } from 'vue'
import TaskChildren from './TaskChildren.vue'
import TaskRow from './TaskRow.vue'

defineProps({
  tasks: { type: Array, required: true },
  liveness: { type: Object, default: () => ({}) },
  refusals: { type: Object, default: () => ({}) },
  selection: { type: Set, default: () => new Set() },
  busyId: { type: Number, default: null },
  projectFor: { type: Function, default: () => null },
})
const emit = defineEmits(['toggle', 'open', 'control', 'focus', 'select',
                          'delete'])

const expanded = ref(new Set())

function flip(task) {
  const next = new Set(expanded.value)
  if (next.has(task.id)) next.delete(task.id)
  else next.add(task.id)
  expanded.value = next
}
</script>

<template>
  <template v-for="task in tasks" :key="task.id">
    <TaskRow
      :task="task"
      :liveness="liveness[task.id] || null"
      :refusal="refusals[task.id] || null"
      :project="projectFor(task)"
      :selected="selection.has(task.id)"
      :busy="busyId === task.id"
      :expanded="expanded.has(task.id)"
      @expand="flip(task)"
      @toggle="t => emit('toggle', t)"
      @open="(kind, t) => emit('open', kind, t)"
      @control="(t, action) => emit('control', t, action)"
      @focus="id => emit('focus', id)"
      @select="t => emit('select', t)"
      @delete="t => emit('delete', t)"
    />
    <TaskChildren
      v-if="expanded.has(task.id)"
      :parent="task"
      :liveness="liveness"
      :refusals="refusals"
      :busy-id="busyId"
      :project-for="projectFor"
      @open="(kind, t) => emit('open', kind, t)"
      @control="(t, action) => emit('control', t, action)"
      @focus="id => emit('focus', id)"
      @delete="t => emit('delete', t)"
    />
  </template>
</template>
