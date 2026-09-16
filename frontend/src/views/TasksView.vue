<script setup>
// The list and the graph are two readings of one table, so they are two tabs
// inside this view rather than two sidebar entries: AppLayout compares
// `route.path`, so a second entry would need a `?query` the highlight cannot
// see.
import { ref } from 'vue'
import Button from '../components/ui/Button.vue'
import Icon from '../components/ui/Icon.vue'
import TaskGraphPanel from '../components/tasks/TaskGraphPanel.vue'
import TaskListPanel from '../components/tasks/TaskListPanel.vue'
import Tabs from '../components/ui/Tabs.vue'

const VIEWS = [
  { value: 'list', label: 'List' },
  { value: 'graph', label: 'Graph' },
]

const view = ref('list')
const list = ref(null)
</script>

<template>
  <div class="space-y-4">
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Engineering</div>
        <h1 class="page-title">Tasks</h1>
        <p class="page-subtitle">
          Nothing runs until someone arms it — and an armed task waits for
          every dependency it names.
        </p>
      </div>
      <Button
        v-if="view === 'list'"
        variant="primary"
        data-testid="task-new"
        @click="list?.openComposer()"
      >
        <Icon name="plus" :size="14" />
        <span>New task</span>
        <kbd class="tv-kbd">c</kbd>
      </Button>
    </header>

    <Tabs v-model="view" :tabs="VIEWS" data-testid="tasks-view-tabs" />

    <TaskListPanel v-if="view === 'list'" ref="list" />
    <TaskGraphPanel v-else />
  </div>
</template>

<style scoped>
.tv-kbd {
    margin-left: 0.25rem;
    padding: 0 0.25rem;
    font-size: 0.6875rem;
    font-family: var(--font-mono, ui-monospace, monospace);
    border: 1px solid currentColor;
    border-radius: var(--radius-sm, 0.25rem);
    opacity: 0.7;
}
</style>
