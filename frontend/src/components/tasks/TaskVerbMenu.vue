<script setup>
// The ⋯ menu of a task row (feature #119 R4a). Its entries are the server's
// `task.menu` for the row's state, in the order they are drawn; an empty menu
// draws no trigger at all.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import DropdownMenu from '../ui/DropdownMenu.vue'
import Icon from '../ui/Icon.vue'
import { VERB_LABELS } from '../../composables/useTaskVerbs.js'

const props = defineProps({
  task: { type: Object, required: true },
  busy: { type: Boolean, default: false },
  // Verbs a host already draws as their own button, so one action is never
  // offered twice on the same screen.
  omit: { type: Array, default: () => [] },
})
const emit = defineEmits(['verb'])

const items = computed(() => (props.task.menu || []).filter(v => !props.omit.includes(v)).map(verb => ({
  label: VERB_LABELS[verb] || verb,
  danger: verb === 'delete',
  disabled: props.busy,
  testid: `task-verb-${verb}`,
  onSelect: () => emit('verb', verb),
})))
</script>

<template>
  <DropdownMenu v-if="items.length" :items="items">
    <template #trigger>
      <Button
        variant="ghost"
        size="sm"
        class="tvm-trigger"
        :aria-label="`More actions for #${task.id}`"
        data-testid="task-menu"
        @click.stop
      >
        <Icon name="more-horizontal" :size="14" />
      </Button>
    </template>
  </DropdownMenu>
</template>

<style scoped>
@media (max-width: 767px) {
    .tvm-trigger {
        min-width: 2.75rem;
        min-height: 2.75rem;
    }
}
</style>
