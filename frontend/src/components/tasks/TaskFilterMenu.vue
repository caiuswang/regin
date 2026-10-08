<script setup>
// The one "Filter" button behind the chips. What it offers depends on the
// reading: the next-step list and the board own the status axis (a status
// filter there would contradict the column a task sits in) and never show
// archived, the flat and legacy-grouped list takes both, and the graph takes
// the grouped reading's filters plus its own "Open chains only" switch.
//
// Toggles and priority apply on click; the free-text fields apply on submit,
// because each is a URL write and a history entry per keystroke is noise.
// A pipeline theme reads the same split off the hooks: its head counts what
// is on (one per chip the toolbar shows) and its foot says toggles apply now.
import { computed, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import Popover from '../ui/Popover.vue'
import Select from '../ui/Select.vue'
import FeaturePicker from '../pipeline/FeaturePicker.vue'
import PlGlyph from '../pipeline/PlGlyph.vue'
import { PRIORITIES, STATUS_FILTERS } from '../../utils/taskMeta.js'
import { taskChips } from '../../utils/taskChips.js'

const props = defineProps({
  state: { type: Object, required: true },
  //: 'grouped' | 'flat' | 'graph'
  mode: { type: String, default: 'grouped' },
  users: { type: Array, default: () => [] },
  mobile: { type: Boolean, default: false },
  // The currently selected feature, if its title has been resolved.
  feature: { type: Object, default: null },
})
const emit = defineEmits(['patch'])

const open = ref(false)
const draft = ref({ tag: '', source: '', feature_id: '', assignee: '' })

const onCount = computed(() => taskChips(props.state, { mode: props.mode }).length)

const priorities = computed(() => String(props.state.priority || '').split(',').filter(Boolean))
const statusOptions = STATUS_FILTERS.map(s => ({ value: s.key, label: s.key ? s.label : 'Any status' }))
const userOptions = computed(() => [
  { value: '', label: 'Anyone' },
  ...props.users.map(u => ({ value: String(u.id), label: u.name })),
])

function togglePriority(value, on) {
  const next = new Set(priorities.value)
  if (on) next.add(value)
  else next.delete(value)
  emit('patch', { priority: PRIORITIES.map(p => p.value).filter(v => next.has(v)).join(',') })
}

// Seeded on every opening, so the fields show what the URL says now rather
// than what was typed and abandoned last time.
watch(open, (isOpen) => {
  if (!isOpen) return
  const s = props.state
  draft.value = { tag: s.tag, source: s.source, feature_id: s.feature_id, assignee: s.assignee }
})

function apply() {
  emit('patch', { ...draft.value })
  open.value = false
}
</script>

<template>
  <Popover v-model:open="open" align="start">
    <template #trigger>
      <Button
        variant="ghost"
        class="tfm-trigger"
        :class="{ 'is-mobile': mobile }"
        :aria-expanded="open"
        data-pl-part="key"
        :data-pl-on="onCount ? '' : undefined"
        data-testid="task-filter-button"
      >
        <Icon name="plus" :size="13" />
        <span>Filter</span>
      </Button>
    </template>

    <form class="tfm" data-pl-part="filter" @submit.prevent="apply()">
      <div class="tfm-head" data-pl-part="filter-head">
        <span>Filter</span>
        <span data-pl-part="count" data-testid="task-filter-on">{{ onCount ? `${onCount} on` : 'None on' }}</span>
      </div>
      <fieldset class="tfm-field" data-pl-part="field">
        <legend class="tfm-label" data-pl-part="field-label">Priority</legend>
        <span class="tfm-row" data-pl-part="toggles">
          <Checkbox
            v-for="p in PRIORITIES"
            :key="p.value"
            :model-value="priorities.includes(p.value)"
            data-pl-part="toggle"
            :data-testid="`task-filter-priority-${p.value}`"
            @update:model-value="v => togglePriority(p.value, v)"
          >
            <PlGlyph :kind="`priority.${p.value}`" />{{ p.label }}
          </Checkbox>
        </span>
      </fieldset>
      <label v-if="mode === 'flat'" class="tfm-field" data-pl-part="field">
        <span class="tfm-label" data-pl-part="field-label">Status</span>
        <Select
          :model-value="state.status"
          :options="statusOptions"
          aria-label="Filter by status"
          data-pl-part="input"
          data-testid="list-facet-status"
          @update:model-value="v => emit('patch', { status: v })"
        />
      </label>
      <div class="tfm-field" data-pl-part="field">
        <span class="tfm-label" data-pl-part="field-label">Show</span>
        <span class="tfm-row" data-pl-part="checks">
          <Checkbox
            v-if="mode === 'graph'"
            :model-value="state.open !== '0'"
            label="Open chains only"
            data-pl-part="check"
            data-testid="task-filter-open"
            @update:model-value="v => emit('patch', { open: v ? '' : '0' })"
          />
          <Checkbox
            :model-value="state.unfeatured !== '1'"
            label="Feature steps"
            data-pl-part="check"
            data-testid="task-steps-toggle"
            @update:model-value="v => emit('patch', { unfeatured: v ? '' : '1' })"
          />
          <Checkbox
            v-if="mode === 'flat'"
            :model-value="state.archived === '1'"
            label="Archived tasks"
            data-pl-part="check"
            data-testid="task-archived-toggle"
            @update:model-value="v => emit('patch', { archived: v ? '1' : '' })"
          />
        </span>
      </div>
      <div class="tfm-pair">
        <label class="tfm-field" data-pl-part="field">
          <span class="tfm-label" data-pl-part="field-label">Tag</span>
          <Input v-model="draft.tag" placeholder="release" data-pl-part="input" data-testid="task-filter-tag" />
        </label>
        <label class="tfm-field" data-pl-part="field">
          <span class="tfm-label" data-pl-part="field-label">Source</span>
          <Input v-model="draft.source" placeholder="cli, linear…" data-pl-part="input" data-testid="task-filter-source" />
        </label>
      </div>
      <div class="tfm-pair">
        <!-- Not a <label>: a label re-clicks its first labelable descendant, which after a pick is the chip's clear button. -->
        <div class="tfm-field" data-pl-part="field">
          <span class="tfm-label" data-pl-part="field-label">Feature</span>
          <FeaturePicker
            v-model="draft.feature_id"
            :current="feature"
            label="Filter by feature"
            placeholder="Search features…"
            data-pl-part="input"
            testid="task-filter-feature"
          />
        </div>
        <label class="tfm-field" data-pl-part="field">
          <span class="tfm-label" data-pl-part="field-label">Assignee</span>
          <Select v-model="draft.assignee" :options="userOptions" data-pl-part="input" data-testid="task-filter-assignee" />
        </label>
      </div>
      <div class="tfm-acts" data-pl-part="filter-foot" data-pl-hint="Toggles apply now">
        <Button variant="primary" size="sm" type="submit" data-pl-part="key--primary" data-testid="task-more-apply">Apply</Button>
      </div>
    </form>
  </Popover>
</template>

<style scoped>
.tfm {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    width: 17rem;
    max-height: min(70vh, 30rem);
    overflow-y: auto;
}
.tfm-head { display: none; }
.tfm-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem 0.875rem;
}
.tfm-field {
    display: flex;
    flex-direction: column;
    gap: 0.3125rem;
    margin: 0;
    padding: 0;
    border: 0;
}
.tfm-label {
    font-size: 0.6875rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-fg-subtle);
}
.tfm-pair {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.5rem;
}
.tfm-pair .tfm-field { min-width: 0; }
.tfm-acts {
    display: flex;
    justify-content: flex-end;
}
.tfm-trigger {
    border: 1px dashed var(--color-border-strong);
}
.tfm-trigger.is-mobile { height: 2.75rem; }
</style>
