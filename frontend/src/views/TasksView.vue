<script setup>
// The list and the graph are two readings of one table, so they are two views
// of this tab rather than two sidebar entries. The view is a URL axis
// (`?view=graph`), so a reload lands where the reader was.
import { computed, ref } from 'vue'
import Button from '../components/ui/Button.vue'
import Icon from '../components/ui/Icon.vue'
import PipelineListToolbar from '../components/PipelineListToolbar.vue'
import TaskGraphPanel from '../components/tasks/TaskGraphPanel.vue'
import TaskListPanel from '../components/tasks/TaskListPanel.vue'
import { usePipelineTabActive } from '../composables/usePipelineHeader'
import { useTaskQuery } from '../composables/useTaskQuery'

const VIEWS = [
  { value: 'list', label: 'List' },
  { value: 'graph', label: 'Graph' },
]

const query = useTaskQuery()
const view = computed(() => query.state.value.view)
const list = ref(null)
const isCurrentTab = usePipelineTabActive()

function setView(v) { query.patch({ view: v }) }
</script>

<template>
  <div class="space-y-4">
    <!-- The one thing this tab sends up into the shell's header: its create
         action. Every list control stays in the body. -->
    <Teleport v-if="isCurrentTab" defer to="#pipeline-actions">
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
    </Teleport>

    <TaskListPanel v-if="view === 'list'" ref="list" @view="setView" />
    <template v-else>
      <PipelineListToolbar noun="task" :views="VIEWS" :view="view" bare @view="setView" />
      <TaskGraphPanel />
    </template>
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
