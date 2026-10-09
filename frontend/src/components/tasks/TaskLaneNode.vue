<script setup>
// One box of the lane graph. It carries the task's primary action only
// (feature #119 R4a); clicking anywhere else on the box opens the task — the
// title's link is stretched over it, with the action above.
//
// In the graph's edit mode a pick button covers the whole box, so a click
// chooses the task instead of opening it or firing its action.
import Button from '../ui/Button.vue'
import TaskPrimaryAction from './TaskPrimaryAction.vue'
import TaskStatusIcon from './TaskStatusIcon.vue'
import { taskTitle } from '../../utils/taskTitle.js'

defineProps({
  node: { type: Object, required: true },
  tag: { type: String, required: true },
  busy: { type: Boolean, default: false },
  canWrite: { type: Boolean, default: true },
  editing: { type: Boolean, default: false },
  isSource: { type: Boolean, default: false },
})
const emit = defineEmits(['verb', 'hover', 'pick'])
</script>

<template>
  <div
    class="tln cursor-pointer hover:shadow-md focus-visible:outline-2"
    :data-testid="`graph-node-${node.id}`"
    :data-column="node.column"
    data-pl-part="node"
    :data-pl-tag="tag"
    @mouseenter="emit('hover', node.id)"
    @mouseleave="emit('hover', null)"
    @focusin="emit('hover', node.id)"
    @focusout="emit('hover', null)"
  >
    <span class="tln-top">
      <TaskStatusIcon :status="node.status" :size="12" />
      <span class="tln-id" data-pl-part="row-id">#{{ node.id }}</span>
      <span v-if="!node.action && node.waits_on == null" class="tlg-node-tag" data-pl-part="meta-label">{{ tag }}</span>
      <TaskPrimaryAction
        v-else
        class="tln-action"
        compact
        :task="node"
        :busy="busy"
        :can-write="canWrite"
        @verb="v => emit('verb', v, node)"
      />
    </span>
    <router-link :to="`/tasks/${node.id}`" class="tln-title" :title="taskTitle(node)">{{ taskTitle(node) }}</router-link>
    <Button
      v-if="editing"
      variant="ghost"
      class="tln-pick"
      :aria-pressed="isSource"
      :aria-label="`Pick task ${node.id}`"
      :data-testid="`graph-pick-${node.id}`"
      @click="emit('pick', node.id)"
    />
  </div>
</template>

<style scoped>
.tln:focus-within { outline: 2px solid var(--color-ring); outline-offset: 2px; }
.tln-top {
    position: relative;
    z-index: 1;
    display: flex;
    align-items: center;
    gap: 0.375rem;
    min-height: 1.75rem;
}
.tln-id {
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.6875rem;
    color: var(--color-fg-subtle);
}
.tln-top .tln-action { margin-left: auto; }
.tln-title {
    display: -webkit-box;
    overflow: hidden;
    font-size: 0.8125rem;
    font-weight: 500;
    line-height: 1.3;
    color: var(--color-fg);
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
}
.tln-title:hover { text-decoration: none; }
.tln-title:focus-visible { outline: none; }
.tln .tln-pick {
    position: absolute;
    inset: 0;
    z-index: 2;
    width: auto;
    height: auto;
    padding: 0;
    background: transparent;
    border-radius: inherit;
    cursor: crosshair;
}
.tln-title::after {
    content: "";
    position: absolute;
    inset: 0;
}
</style>
