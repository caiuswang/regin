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
  first: { type: Boolean, default: false },
  last: { type: Boolean, default: false },
})
const emit = defineEmits(['rename', 'role', 'prompt', 'remove', 'move'])

const expanded = ref(false)
const label = ref(props.stage.label)
watch(() => props.stage, (stage) => { label.value = stage.label })

const archived = computed(() => !!props.stage.archived_at)
const editable = computed(() => props.canWrite && !archived.value)

function commitLabel() {
  const next = label.value.trim()
  if (!next) { label.value = props.stage.label; return }
  if (next !== props.stage.label) emit('rename', next)
}
</script>

<template>
  <li
    class="sr" :class="{ 'is-archived': archived }"
    :data-testid="`stage-row-${stage.key}`" :data-stage-key="stage.key"
  >
    <div class="sr-line">
      <span v-if="editable" class="sr-grip" aria-hidden="true" :data-testid="`stage-grip-${stage.key}`">
        <Icon name="grip-vertical" :size="13" />
      </span>
      <Input
        v-if="editable" v-model="label" class="sr-label-input"
        :aria-label="`Label of stage ${stage.key}`" :disabled="busy"
        :data-testid="`stage-label-${stage.key}`"
        @keydown.enter="commitLabel" @keydown.esc="label = stage.label" @blur="commitLabel"
      />
      <span v-else class="sr-label" :data-testid="`stage-label-${stage.key}`">{{ stage.label }}</span>
      <code class="sr-key">{{ stage.key }}</code>
      <Select
        v-if="editable && !stage.builtin" :model-value="stage.role" :options="STAGE_ROLES"
        :aria-label="`Role of stage ${stage.key}`" :disabled="busy"
        :data-testid="`stage-role-${stage.key}`"
        @update:model-value="emit('role', $event)"
      />
      <span v-else class="sr-chip" :data-testid="`stage-role-${stage.key}`">{{ stage.role }}</span>
      <span v-if="stage.builtin" class="sr-chip sr-chip--quiet">built-in</span>
      <span v-if="archived" class="sr-chip sr-chip--quiet" :data-testid="`stage-archived-${stage.key}`">archived</span>
      <span class="sr-spacer" />
      <template v-if="editable">
        <Button
          variant="ghost" size="sm" :disabled="first || busy" :aria-label="`Move ${stage.label} up`"
          :data-testid="`stage-up-${stage.key}`" @click="emit('move', -1)"
        ><Icon name="arrow-up" :size="12" /></Button>
        <Button
          variant="ghost" size="sm" :disabled="last || busy" :aria-label="`Move ${stage.label} down`"
          :data-testid="`stage-down-${stage.key}`" @click="emit('move', 1)"
        ><Icon name="arrow-down" :size="12" /></Button>
        <Button
          v-if="!stage.builtin" variant="ghost" size="sm" :disabled="busy"
          :data-testid="`stage-remove-${stage.key}`" @click="emit('remove')"
        >Remove</Button>
      </template>
      <Button
        variant="ghost" size="sm" :aria-expanded="expanded"
        :data-testid="`stage-expand-${stage.key}`" @click="expanded = !expanded"
      >
        Prompt
        <Icon :name="expanded ? 'chevron-up' : 'chevron-down'" :size="12" />
      </Button>
    </div>
    <StagePromptEditor
      v-if="expanded" :stage="stage" :can-write="editable" :busy="busy"
      @save="emit('prompt', $event)"
    />
  </li>
</template>

<style scoped>
.sr { padding: 0.5rem 0.65rem; border-bottom: 1px solid var(--color-border); background: var(--color-surface); }
.sr:last-child { border-bottom: 0; }
.sr.is-archived { opacity: 0.65; }
.sr-line { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; min-width: 0; }
.sr-grip { display: inline-flex; color: var(--color-fg-faint); cursor: grab; }
.sr-label-input { width: 12rem; max-width: 100%; height: 28px; font-size: 13px; }
.sr-label { font-size: 13px; font-weight: 600; color: var(--color-fg); }
.sr-key { font-size: 11px; color: var(--color-fg-muted); }
.sr-chip {
    font-size: 11px; padding: 0 0.4rem; border-radius: var(--radius-sm);
    background: var(--color-surface-3); color: var(--color-fg-muted);
}
.sr-chip--quiet { background: none; border: 1px solid var(--color-border); }
.sr-spacer { flex: 1 1 auto; }
</style>
