<script setup>
// The rows a bulk action did not move, each by name and the server's reason.
// A count ("2 could not be") leaves the operator hunting for which two.
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { taskTitle } from '../../utils/taskTitle.js'

defineProps({
  rows: { type: Array, required: true },
})
const emit = defineEmits(['dismiss'])
</script>

<template>
  <div class="br" role="alert" data-testid="task-bulk-rejected">
    <div class="br-head">
      <span>{{ rows.length }} not changed</span>
      <Button variant="ghost" size="icon" aria-label="Dismiss" class="br-x" @click="emit('dismiss')">
        <Icon name="x" :size="12" />
      </Button>
    </div>
    <ul class="br-rows">
      <li v-for="row in rows" :key="row.id" :data-testid="`task-bulk-rejected-${row.id}`">
        <router-link :to="`/tasks/${row.id}`" class="br-ref">#{{ row.id }}</router-link> <span class="br-title">{{ taskTitle(row) }}</span> <span class="br-reason">— {{ row.reason }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.br {
    border: 1px solid var(--color-danger);
    border-radius: var(--radius-lg);
    padding: 0.5rem 0.75rem;
    font-size: 0.8125rem;
}
.br-head { display: flex; align-items: center; justify-content: space-between; font-weight: 600; color: var(--color-danger); }
.br .br-x { width: 24px; height: 24px; }
.br-rows { margin: 0.25rem 0 0; padding: 0; list-style: none; }
.br-rows li { overflow-wrap: anywhere; }
.br-ref { font-family: var(--font-mono, ui-monospace, monospace); color: var(--color-primary); }
.br-title { color: var(--color-fg); }
.br-reason { color: var(--color-fg-muted); }
</style>
