<script setup>
// The chrome of the lane graph's edit mode: the hint (with the kind to draw
// once a source is chosen) on top, the pending-changes save bar below.
import Button from '../ui/Button.vue'
import DepKindToggle from './DepKindToggle.vue'

defineProps({
  source: { type: Number, default: null },
  kind: { type: String, default: 'requires' },
  changed: { type: Number, default: 0 },
  saving: { type: Boolean, default: false },
  refusal: { type: Object, default: null },
})
const emit = defineEmits(['update:kind', 'cancel-source', 'save', 'discard'])
</script>

<template>
  <div class="tgeb-hint" role="status" data-testid="graph-edit-hint">
    <template v-if="source == null">
      Pick a source task, then pick the task it should wait for.
    </template>
    <template v-else>
      <span>#{{ source }} waits for… pick the target.</span>
      <DepKindToggle
        :model-value="kind"
        label="Kind of the new edge"
        data-testid="graph-edit-kind"
        @update:model-value="v => emit('update:kind', v)"
      />
      <Button variant="link" size="sm" data-testid="graph-edit-cancel-source" @click="emit('cancel-source')">
        Cancel selection
      </Button>
    </template>
  </div>
  <div v-if="changed || refusal" class="tgeb-save" data-testid="graph-edit-savebar">
    <span class="tgeb-count" data-testid="graph-edit-count">
      {{ changed }} {{ changed === 1 ? 'task' : 'tasks' }} changed
    </span>
    <span v-if="refusal" class="tgeb-refusal" role="alert" data-testid="graph-edit-refusal">
      <template v-if="refusal.sourceId != null">#{{ refusal.sourceId }}: </template>{{ refusal.message }}
      <template v-if="refusal.pair.length === 2"> ({{ refusal.pair[0] }} → {{ refusal.pair[1] }})</template>
    </span>
    <Button variant="ghost" size="sm" :disabled="saving" data-testid="graph-edit-discard" @click="emit('discard')">
      Discard
    </Button>
    <Button variant="primary" size="sm" :disabled="saving || !changed" data-testid="graph-edit-save" @click="emit('save')">
      {{ saving ? 'Saving…' : 'Save changes' }}
    </Button>
  </div>
</template>

<style scoped>
.tgeb-hint {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.625rem;
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
.tgeb-save {
    position: absolute;
    bottom: 0.625rem;
    left: 50%;
    z-index: 3;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    max-width: calc(100% - 7rem);
    padding: 0.375rem 0.5rem 0.375rem 0.75rem;
    font-size: 0.8125rem;
    background: var(--color-surface);
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-md);
    box-shadow: var(--ds-shadow-lg);
    transform: translateX(-50%);
}
.tgeb-count { font-variant-numeric: tabular-nums; color: var(--color-fg-muted); }
.tgeb-refusal { color: var(--color-danger); }
</style>
