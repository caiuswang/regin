<script setup>
// One stage of a project's list. A built-in stage can be renamed and its
// prompt edited, never removed or re-roled: the ledger needs a build entry.
import { computed, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import StagePromptEditor from './StagePromptEditor.vue'
import { STAGE_ROLES } from '../../composables/useProjectStages'

const props = defineProps({
  stage: { type: Object, required: true },
  canWrite: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  position: { type: Number, default: 0 },
  first: { type: Boolean, default: false },
  last: { type: Boolean, default: false },
})
const emit = defineEmits(['rename', 'role', 'prompt', 'remove', 'move'])

const expanded = ref(false)
const label = ref(props.stage.label)
watch(() => props.stage, (stage) => { label.value = stage.label })

const archived = computed(() => !!props.stage.archived_at)
const editable = computed(() => props.canWrite && !archived.value)
// A built-in's key is its role, so it is shown once.
const showKey = computed(() => props.stage.key !== props.stage.role)
const hasPrompt = computed(() => !!(props.stage.prompt_body || '').trim())

function commitLabel() {
  const next = label.value.trim()
  if (!next) { label.value = props.stage.label; return }
  if (next !== props.stage.label) emit('rename', next)
}
</script>

<template>
  <li
    class="sr" :class="{ 'is-archived': archived, 'is-open': expanded }" data-pl-part="row"
    :data-expanded="expanded || undefined"
    :data-testid="`stage-row-${stage.key}`" :data-stage-key="stage.key"
  >
    <div class="sr-line">
      <span v-if="editable" class="sr-grip" aria-hidden="true" :data-testid="`stage-grip-${stage.key}`">
        <Icon name="grip-vertical" :size="13" />
      </span>
      <span v-else class="sr-grip" aria-hidden="true" />
      <span class="sr-pos" data-pl-part="row-id">{{ position || '' }}</span>
      <Input
        v-if="editable" v-model="label" class="sr-label-input" data-pl-part="input"
        :aria-label="`Label of stage ${stage.key}`" :disabled="busy"
        :data-testid="`stage-label-${stage.key}`"
        @keydown.enter="commitLabel" @keydown.esc="label = stage.label" @blur="commitLabel"
      />
      <span v-else class="sr-label" :data-testid="`stage-label-${stage.key}`">{{ stage.label }}</span>
      <span class="sr-tags">
        <code v-if="showKey" class="sr-key" title="The step's key — fixed once the stage exists">{{ stage.key }}</code>
        <Select
          v-if="editable && !stage.builtin" :model-value="stage.role" :options="STAGE_ROLES"
          class="sr-role" data-pl-part="input"
          :aria-label="`Role of stage ${stage.key}`" :disabled="busy"
          :data-testid="`stage-role-${stage.key}`"
          @update:model-value="emit('role', $event)"
        />
        <span v-else class="sr-chip" data-pl-part="tag" :data-testid="`stage-role-${stage.key}`">{{ stage.role }}</span>
        <span v-if="stage.builtin" class="sr-chip sr-chip--quiet" data-pl-part="tag" data-pl-dashed>built-in</span>
        <span v-if="archived" class="sr-chip sr-chip--quiet" data-pl-part="tag" data-pl-dashed :data-testid="`stage-archived-${stage.key}`">archived</span>
      </span>
      <span class="sr-actions">
        <template v-if="editable">
          <Button
            variant="ghost" size="sm" class="sr-icon" data-pl-part="key" :disabled="first || busy"
            :aria-label="`Move ${stage.label} up`" :title="`Move ${stage.label} up`"
            :data-testid="`stage-up-${stage.key}`" @click="emit('move', -1)"
          ><Icon name="arrow-up" :size="12" /></Button>
          <Button
            variant="ghost" size="sm" class="sr-icon" data-pl-part="key" :disabled="last || busy"
            :aria-label="`Move ${stage.label} down`" :title="`Move ${stage.label} down`"
            :data-testid="`stage-down-${stage.key}`" @click="emit('move', 1)"
          ><Icon name="arrow-down" :size="12" /></Button>
          <Button
            v-if="!stage.builtin" variant="ghost" size="sm" class="sr-remove" data-pl-part="key" data-pl-tone="danger" :disabled="busy"
            :data-testid="`stage-remove-${stage.key}`" @click="emit('remove')"
          >Remove</Button>
        </template>
        <Button
          variant="secondary" size="sm" class="sr-prompt" data-pl-part="key" :aria-expanded="expanded"
          :data-pl-on="expanded || undefined"
          :data-testid="`stage-expand-${stage.key}`" @click="expanded = !expanded"
        >
          {{ hasPrompt ? 'Prompt' : 'No prompt' }}
          <Icon :name="expanded ? 'chevron-up' : 'chevron-down'" :size="12" />
        </Button>
      </span>
    </div>
    <StagePromptEditor
      v-if="expanded" class="sr-editor" :stage="stage" :can-write="editable" :busy="busy"
      @save="emit('prompt', $event)"
    />
  </li>
</template>

<style scoped>
.sr { border-top: 1px solid var(--color-border-subtle); background: var(--color-surface); }
.sr.is-open { background: var(--color-surface-2); }
.sr.is-archived { opacity: 0.65; }
.sr-line {
    display: grid; grid-template-columns: 14px 1.4rem minmax(8rem, 14rem) minmax(0, 1fr) auto;
    align-items: center; gap: 0.5rem; min-width: 0; min-height: 44px;
    padding: 0.35rem 1.1rem 0.35rem 0.6rem;
}
.sr-grip { display: inline-flex; color: var(--color-fg-faint); cursor: grab; }
.sr-pos {
    min-width: 1.4rem; font-size: 11px; font-variant-numeric: tabular-nums; text-align: right;
    color: var(--color-fg-subtle);
}
.sr-label-input {
    width: 100%; height: 28px; font-size: 13px; font-weight: 600;
    border-color: transparent; background: transparent; box-shadow: none;
}
.sr-label-input:hover { border-color: var(--color-border); }
.sr-label-input:focus { border-color: var(--color-primary); background: var(--color-surface); }
.sr-label { padding-left: 0.6rem; font-size: 13px; font-weight: 600; color: var(--color-fg); }
.sr-key { font-size: 11px; color: var(--color-fg-muted); }
.sr-tags { display: flex; flex-wrap: wrap; align-items: center; gap: 0.35rem; min-width: 0; }
.sr-tags :deep(.sr-role) { height: 28px; min-width: 6.5rem; font-size: 12px; }
.sr-chip {
    font-size: 11px; padding: 0 0.45rem; line-height: 18px; border-radius: var(--radius-sm);
    background: var(--color-surface-3); color: var(--color-fg-muted);
}
.sr-chip--quiet { background: none; border: 1px dashed var(--color-border); }
.sr-actions { display: inline-flex; align-items: center; justify-content: flex-end; gap: 0.25rem; }
.sr-icon { width: 28px; padding: 0; }
.sr-remove { color: var(--color-danger); }
.sr-editor { padding: 0 1.1rem 0.75rem 3.5rem; }
@media (max-width: 639.98px) {
    .sr-line { grid-template-columns: 14px 1.4rem minmax(0, 1fr); }
    .sr-tags, .sr-actions { grid-column: 3; }
    .sr-actions { justify-content: flex-start; }
    .sr-editor { padding-left: 1.1rem; }
}
</style>
