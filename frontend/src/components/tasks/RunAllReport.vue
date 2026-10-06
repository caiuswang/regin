<script setup>
// What "Run all N" could not start, one line per refused task with its reason
// — never just a count. A refused task may sit on a page of Ready that is not
// loaded, so the report names and links it rather than relying on its row.
import Button from '../ui/Button.vue'
import TaskRefusal from './TaskRefusal.vue'
import { taskTitle } from '../../utils/taskTitle.js'

defineProps({
  report: { type: Object, required: true },
})
defineEmits(['dismiss'])
</script>

<template>
  <div class="rar" role="alert" data-testid="run-all-report">
    <p class="rar-head">
      {{ report.started }} started · {{ report.refused.length }} refused
      <Button variant="link" size="sm" class="rar-dismiss" @click="$emit('dismiss')">Dismiss</Button>
    </p>
    <ul class="rar-rows">
      <li
        v-for="row in report.refused"
        :key="row.id"
        class="rar-row"
        :data-testid="`run-all-refused-${row.id}`"
      >
        <router-link :to="`/tasks/${row.id}`" class="rar-task">#{{ row.id }} {{ taskTitle(row) }}</router-link>
        <TaskRefusal class="rar-reason" :refusal="row" />
      </li>
    </ul>
  </div>
</template>

<style scoped>
.rar {
    margin: 0.25rem 0 0.5rem;
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--color-danger-soft, var(--color-border));
    border-radius: var(--radius-md);
    background: var(--color-surface);
}
.rar-head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--color-danger);
}
.rar .rar-dismiss { margin-left: auto; font-size: 0.75rem; }
.rar-rows { margin: 0.25rem 0 0; padding: 0; list-style: none; }
.rar-row { padding: 0.25rem 0; border-top: 1px solid var(--color-border-subtle); }
.rar-task {
    font-size: 0.8125rem;
    font-weight: 500;
    color: var(--color-fg);
}
.rar .rar-reason { margin-top: 0.125rem; }
</style>
