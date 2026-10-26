<script setup>
// One box of the lane graph. It carries the task's primary action only
// (feature #119 R4a); clicking anywhere else on the box opens the task — the
// title's link is stretched over it, with the action above.
//
// Hover reveals Drawio-style connection handles. Edges run left to right, so
// the left handle reaches upstream (this task waits for another) and the right
// handle reaches downstream (another task waits for this one).
//
// `readonly` drops the action and the handles: the box is only a link to the
// task.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import TaskPrimaryAction from './TaskPrimaryAction.vue'
import TaskScheduleChip from './TaskScheduleChip.vue'
import TaskStatusIcon from './TaskStatusIcon.vue'
import { taskTitle } from '../../utils/taskTitle.js'

const props = defineProps({
  node: { type: Object, required: true },
  tag: { type: String, required: true },
  busy: { type: Boolean, default: false },
  canWrite: { type: Boolean, default: true },
  readonly: { type: Boolean, default: false },
})
const emit = defineEmits(['verb', 'hover', 'handle-down', 'handle-click'])

// The open defects lead: they are what the task still owes. A task whose
// defects are all closed keeps a neutral mark of what it fixed.
const fixes = computed(() => {
  const open = props.node.fixes_open_defects || []
  const ids = open.length ? open : (props.node.fixes_defects || [])
  if (!ids.length) return null
  return {
    open: open.length > 0,
    label: ids.length === 1 ? `fixes #${ids[0]}` : `fixes ${ids.length}`,
    title: `Fixes ${ids.map(id => `#${id}`).join(', ')}`,
  }
})
const showAction = computed(() => !props.readonly && (props.node.action || props.node.waits_on != null))
</script>

<template>
  <div
    class="tln cursor-pointer hover:shadow-md focus-visible:outline-2"
    :data-testid="`graph-node-${node.id}`"
    :data-node-id="node.id"
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
      <TaskScheduleChip :task="node" compact />
      <span v-if="!showAction" class="tlg-node-tag" data-pl-part="meta-label">{{ tag }}</span>
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
    <span
      v-if="fixes"
      class="tln-fixes"
      :class="{ 'is-open': fixes.open }"
      :title="fixes.title"
      data-pl-part="fix-mark"
      :data-testid="`graph-node-fixes-${node.id}`"
      :data-open="fixes.open ? '' : undefined"
    >{{ fixes.label }}</span>
    <template v-if="canWrite && !readonly">
      <Button
        v-for="side in ['left', 'right']"
        :key="side"
        variant="secondary"
        size="icon"
        :class="`tln-handle tln-handle-${side}`"
        :aria-label="side === 'left' ? `#${node.id} waits for…` : `Waits for #${node.id}…`"
        :title="side === 'left' ? 'Drag to a task this one waits for' : 'Drag to a task that waits for this one'"
        :data-testid="`graph-connect-${side}-${node.id}`"
        @pointerdown="ev => emit('handle-down', node, side, ev)"
        @click="ev => emit('handle-click', node, side, ev)"
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
.tln-fixes {
    position: absolute;
    top: -0.5625rem;
    right: 0.5rem;
    z-index: 2;
    padding: 0 0.375rem;
    font-size: 0.625rem;
    font-weight: 600;
    line-height: 1rem;
    color: var(--color-fg-muted);
    white-space: nowrap;
    background: var(--color-surface-2);
    border: 1px solid var(--color-border-strong);
    border-radius: 999px;
}
.tln-fixes.is-open {
    color: var(--color-warning-strong);
    background: var(--color-warning-soft);
    border-color: var(--color-warning);
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
    cursor: crosshair;
    touch-action: none;
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
