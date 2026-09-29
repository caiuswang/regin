<script setup>
// One box of the lane graph. It carries the task's primary action only
// (feature #119 R4a); clicking anywhere else on the box opens the task — the
// title's link is stretched over it, with the action above.
import TaskPrimaryAction from './TaskPrimaryAction.vue'
import TaskStatusIcon from './TaskStatusIcon.vue'

defineProps({
  node: { type: Object, required: true },
  tag: { type: String, required: true },
  busy: { type: Boolean, default: false },
  canWrite: { type: Boolean, default: true },
})
const emit = defineEmits(['verb', 'hover'])
</script>

<template>
  <div
    class="tln cursor-pointer hover:shadow-md focus-visible:outline-2"
    :data-testid="`graph-node-${node.id}`"
    :data-column="node.column"
    @mouseenter="emit('hover', node.id)"
    @mouseleave="emit('hover', null)"
    @focusin="emit('hover', node.id)"
    @focusout="emit('hover', null)"
  >
    <span class="tln-top">
      <TaskStatusIcon :status="node.status" :size="12" />
      <span class="tln-id">#{{ node.id }}</span>
      <span v-if="!node.action && node.waits_on == null" class="tlg-node-tag">{{ tag }}</span>
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
    <router-link :to="`/tasks/${node.id}`" class="tln-title" :title="node.title">{{ node.title }}</router-link>
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
.tln-title::after {
    content: "";
    position: absolute;
    inset: 0;
}
</style>
