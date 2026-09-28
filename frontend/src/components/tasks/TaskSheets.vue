<script setup>
// The sheets a task verb opens — arm, edit, deps — for surfaces that act on
// a row without owning the sheets themselves (the next-step list, the board,
// the graph, Overview). The pending attempt rides on the row as `armed`, so
// the arm sheet re-arms with what the task was last armed with.
import TaskArmSheet from './TaskArmSheet.vue'
import TaskDepsEditor from './TaskDepsEditor.vue'
import TaskEditSheet from './TaskEditSheet.vue'

defineProps({
  sheet: { type: Object, default: null },
})
const emit = defineEmits(['close', 'done'])
</script>

<template>
  <template v-if="sheet">
    <TaskArmSheet
      v-if="sheet.kind === 'arm'"
      :task="sheet.task"
      @close="emit('close')"
      @armed="emit('done')"
    />
    <TaskEditSheet
      v-else-if="sheet.kind === 'edit'"
      :task="sheet.task"
      @close="emit('close')"
      @saved="emit('done')"
    />
    <TaskDepsEditor
      v-else-if="sheet.kind === 'deps'"
      :task="sheet.task"
      @close="emit('close')"
      @saved="emit('done')"
    />
  </template>
</template>
