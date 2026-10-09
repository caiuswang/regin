<script setup>
// One box of the lane graph. It carries the task's primary action only
// (feature #119 R4a); clicking anywhere else on the box opens the task — the
// title's link is stretched over it, with the action above.
//
// Hover reveals Drawio-style connection handles on each side. The right handle
// starts an edge where this task waits for another; the left handle starts an
// edge where another task waits for this one.
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import TaskPrimaryAction from './TaskPrimaryAction.vue'
import TaskStatusIcon from './TaskStatusIcon.vue'
import { taskTitle } from '../../utils/taskTitle.js'

defineProps({
  node: { type: Object, required: true },
  tag: { type: String, required: true },
  busy: { type: Boolean, default: false },
  canWrite: { type: Boolean, default: true },
})
const emit = defineEmits(['verb', 'hover', 'connect'])
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
    <template v-if="canWrite">
      <Button
        variant="secondary"
        size="icon"
        class="tln-handle tln-handle-left"
        :aria-label="`Something depends on task ${node.id}`"
        :data-testid="`graph-connect-left-${node.id}`"
        @click="emit('connect', node, 'blocks')"
      >
        <Icon name="plus" :size="10" />
      </Button>
      <Button
        variant="secondary"
        size="icon"
        class="tln-handle tln-handle-right"
        :aria-label="`Task ${node.id} depends on something`"
        :data-testid="`graph-connect-right-${node.id}`"
        @click="emit('connect', node, 'depends-on')"
      >
        <Icon name="plus" :size="10" />
      </Button>
    </template>
  </div>
</template>

<style scoped>
.tln:focus-within { outline: 2px solid var(--color-ring); outline-offset: 2px; }
.tln {
    position: relative;
}
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
.tln-title::after {
    content: "";
    position: absolute;
    inset: 0;
}
.tln-handle {
    position: absolute;
    top: 50%;
    z-index: 3;
    width: 1.25rem;
    height: 1.25rem;
    padding: 0;
    opacity: 0;
    transform: translateY(-50%) scale(0.85);
    transition: opacity 120ms ease, transform 120ms ease, background-color 120ms ease;
    pointer-events: none;
}
.tln-handle-left { left: -0.625rem; }
.tln-handle-right { right: -0.625rem; }
.tln:hover .tln-handle,
.tln-handle:focus-visible {
    opacity: 1;
    transform: translateY(-50%) scale(1);
    pointer-events: auto;
}
.tln-handle:hover {
    color: var(--color-primary-fg);
    background: var(--color-primary);
    border-color: var(--color-primary);
}
</style>
