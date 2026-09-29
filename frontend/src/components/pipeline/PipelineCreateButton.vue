<script setup>
// The header's create control: the tab's own default (what `c` opens) and a
// "▾" beside it offering every kind — Task · Feature · Defect — so nothing
// has to be created from the tab that happens to list it.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import DropdownMenu from '../ui/DropdownMenu.vue'
import Icon from '../ui/Icon.vue'
import { CREATE_KINDS, usePipelineCreate } from '../../composables/usePipelineCreate'

const props = defineProps({
  kind: { type: String, required: true },
  testid: { type: String, required: true },
  prefill: { type: Object, default: null },
})

const { openCreate } = usePipelineCreate()
const label = computed(() => `New ${props.kind}`)
const items = CREATE_KINDS.map(k => ({
  label: k.label,
  testid: `pipeline-new-${k.kind}`,
  onSelect: () => openCreate(k.kind, props.prefill),
}))
</script>

<template>
  <span class="pcb">
    <Button variant="primary" class="pcb-main" :data-testid="testid" @click="openCreate(kind, prefill)">
      <Icon name="plus" :size="14" />
      <span>{{ label }}</span>
      <kbd class="pcb-kbd">c</kbd>
    </Button>
    <DropdownMenu :items="items" align="end">
      <template #trigger>
        <Button
          variant="primary"
          class="pcb-more"
          aria-label="New… (task, feature or defect)"
          data-testid="pipeline-new"
        >
          <Icon name="chevron-down" :size="14" />
        </Button>
      </template>
    </DropdownMenu>
  </span>
</template>

<style scoped>
.pcb { display: inline-flex; align-items: stretch; }
.pcb .pcb-main { border-top-right-radius: 0; border-bottom-right-radius: 0; }
.pcb .pcb-more {
    border-top-left-radius: 0; border-bottom-left-radius: 0;
    border-left: 1px solid rgb(255 255 255 / 0.3); padding-inline: 0.45rem;
}
.pcb-kbd {
    margin-left: 0.25rem; padding: 0 0.25rem; font-size: 0.6875rem;
    font-family: var(--font-mono, ui-monospace, monospace);
    border: 1px solid currentColor; border-radius: var(--radius-sm, 0.25rem); opacity: 0.7;
}
</style>
