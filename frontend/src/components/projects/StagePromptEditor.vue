<script setup>
// A stage's prompt: the body a step opened from this stage is launched with.
// The preview is the body as the agent receives it — sample values filled,
// every other character literal — so it is drawn by PromptBody, not markdown.
import { computed, nextTick, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import PromptBody from '../PromptBody.vue'
import Textarea from '../ui/Textarea.vue'
import { renderPreview, unknownVariables } from '../../composables/usePromptPreview'
import { STAGE_PROMPT_VARIABLES } from '../../composables/useProjectStages'

const props = defineProps({
  stage: { type: Object, required: true },
  canWrite: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['save'])

const saved = computed(() => props.stage.prompt_body || '')
const draft = ref(saved.value)
const area = ref(null)
watch(saved, (body) => { draft.value = body })

const dirty = computed(() => draft.value !== saved.value)
const unknown = computed(() => unknownVariables(draft.value, STAGE_PROMPT_VARIABLES))
const preview = computed(() => renderPreview(draft.value, STAGE_PROMPT_VARIABLES))

const varToken = name => `{{${name}}}`
const unknownLabel = computed(() => unknown.value.map(varToken).join(', '))

function insertVar(name) {
  const token = varToken(name)
  const el = area.value?.$el
  const start = el?.selectionStart ?? draft.value.length
  const end = el?.selectionEnd ?? start
  draft.value = draft.value.slice(0, start) + token + draft.value.slice(end)
  nextTick(() => {
    el?.focus()
    el?.setSelectionRange(start + token.length, start + token.length)
  })
}
</script>

<template>
  <div class="spe" :data-testid="`stage-prompt-${stage.key}`">
    <div v-if="canWrite" class="spe-vars">
      <span class="spe-label" data-pl-part="field-label">Variables</span>
      <Button
        v-for="v in STAGE_PROMPT_VARIABLES" :key="v.name"
        variant="ghost" size="sm" class="spe-var" data-pl-part="key" :title="v.description"
        @click="insertVar(v.name)"
      >{{ varToken(v.name) }}</Button>
    </div>
    <div class="spe-grid">
      <label v-if="canWrite" class="spe-col">
        <span class="spe-label" data-pl-part="field-label">Prompt</span>
        <Textarea
          ref="area" v-model="draft" :rows="10" :disabled="busy"
          class="spe-input" data-pl-part="input"
          placeholder="empty — a step from this stage runs with no stage prompt"
          :data-testid="`stage-prompt-input-${stage.key}`"
        />
      </label>
      <div class="spe-col">
        <span class="spe-label" data-pl-part="field-label">{{ canWrite ? 'Preview (sample values)' : 'Prompt' }}</span>
        <div class="spe-preview" data-pl-part="preview" :data-testid="`stage-prompt-preview-${stage.key}`">
          <PromptBody v-if="preview.trim()" :text="preview" />
          <p v-else class="spe-empty">No stage prompt.</p>
        </div>
      </div>
    </div>
    <p v-if="canWrite && unknown.length" class="spe-warn" role="status">
      Not a stage variable, left unfilled: {{ unknownLabel }}
    </p>
    <div v-if="canWrite" class="spe-actions">
      <Button variant="ghost" size="sm" data-pl-part="key" :disabled="!dirty || busy" @click="draft = saved">Revert</Button>
      <Button
        variant="primary" size="sm" data-pl-part="key--primary" :disabled="!dirty || busy"
        :data-testid="`stage-prompt-save-${stage.key}`" @click="emit('save', draft)"
      >{{ busy ? 'Saving…' : 'Save prompt' }}</Button>
    </div>
  </div>
</template>

<style scoped>
.spe { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.6rem 0 0.2rem; }
.spe-vars { display: flex; flex-wrap: wrap; align-items: center; gap: 0.3rem; }
.spe-var { font-family: var(--font-mono, monospace); font-size: 11px; }
.spe-label { font-size: 11px; font-weight: 600; color: var(--color-fg-subtle); }
.spe-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: 0.75rem; }
.spe-col { display: flex; flex-direction: column; gap: 0.25rem; min-width: 0; }
.spe-input { width: 100%; font-family: var(--font-mono, monospace); font-size: 12px; }
.spe-preview {
    min-height: 3rem; padding: 0.45rem 0.55rem; font-size: 12px;
    border: 1px solid var(--color-border); border-radius: var(--radius-md);
    background: var(--color-surface-2);
}
.spe-empty { font-size: 12px; color: var(--color-fg-faint); }
.spe-warn { font-size: 11.5px; color: var(--color-warning-strong); }
.spe-actions { display: flex; justify-content: flex-end; gap: 0.4rem; }
</style>
