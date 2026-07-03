<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import Button from './ui/Button.vue'
import { renderPreview, unknownVariables } from '../composables/usePromptPreview'

const props = defineProps({
  skeleton: { type: Object, required: true },
  busy: { type: String, default: '' },
  saveError: { type: String, default: '' },
})
const emit = defineEmits(['save', 'cancel', 'reset'])

const draftBody = ref(props.skeleton.body || '')
const bodyRef = ref(null)
const validationError = ref('')

// A row switch unmounts/remounts this editor, but a Reset mutates the body of
// the still-mounted skeleton in place — mirror it into the draft so Reset shows.
watch(() => props.skeleton.body, (body) => { draftBody.value = body || '' })

function varToken(name) {
  return `{{${name}}}`
}

const vars = computed(() => props.skeleton.variables || [])
const unknownVars = computed(() => unknownVariables(draftBody.value, vars.value))
const preview = computed(() => renderPreview(draftBody.value, vars.value))
const unknownVarsLabel = computed(() => unknownVars.value.map(varToken).join(', '))
const dirty = computed(() => draftBody.value !== (props.skeleton.body || ''))

function insertVar(name) {
  const token = `{{${name}}}`
  const el = bodyRef.value
  if (!el) {
    draftBody.value += token
    return
  }
  // Insert at the caret (replacing any selection) instead of appending.
  const start = el.selectionStart ?? draftBody.value.length
  const end = el.selectionEnd ?? start
  const text = draftBody.value
  draftBody.value = text.slice(0, start) + token + text.slice(end)
  nextTick(() => {
    el.focus()
    const caret = start + token.length
    el.setSelectionRange(caret, caret)
  })
}

function onSave() {
  validationError.value = ''
  if (!draftBody.value.trim()) {
    validationError.value = 'Body cannot be empty.'
    return
  }
  if (unknownVars.value.length) {
    validationError.value = `Unknown variable(s): ${unknownVarsLabel.value}. `
      + 'Only the declared variables above are filled at render time — remove these or a run falls back to the default.'
    return
  }
  emit('save', draftBody.value)
}
</script>

<template>
  <div class="skeleton-editor">
    <div class="editor-head">
      <code class="text-xs text-slate-500">{{ skeleton.slug }}</code>
      <Button variant="secondary" size="sm" :disabled="busy === 'reset'" @click="emit('reset')">
        Reset to default
      </Button>
    </div>

    <div v-if="vars.length" class="var-palette">
      <span class="palette-label">Variables</span>
      <Button
        v-for="v in vars"
        :key="v.name"
        variant="ghost"
        size="sm"
        class="var-chip"
        :title="v.description || v.name"
        @click="insertVar(v.name)"
      >
        {{ varToken(v.name) }}
        <span v-if="v.required === false" class="var-optional">opt</span>
      </Button>
    </div>

    <div class="editor-grid">
      <label class="block">
        <span class="form-label">Body</span>
        <textarea
          ref="bodyRef"
          v-model="draftBody"
          rows="18"
          class="topics-input w-full font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
        ></textarea>
      </label>
      <div class="block">
        <span class="form-label">Live preview (sample values)</span>
        <pre class="preview-pane">{{ preview }}</pre>
      </div>
    </div>

    <div v-if="unknownVars.length" class="alert alert-warn mt-2">
      Unknown variable(s): {{ unknownVarsLabel }} — not filled at render time.
    </div>
    <div v-if="validationError || saveError" class="alert alert-info mt-2">
      {{ validationError || saveError }}
    </div>

    <div class="mt-4 flex gap-2 justify-end">
      <Button variant="secondary" :disabled="busy === 'save'" @click="emit('cancel')">Close</Button>
      <Button variant="primary" :disabled="busy === 'save' || !dirty || unknownVars.length > 0" @click="onSave">
        {{ busy === 'save' ? 'Saving…' : 'Save' }}
      </Button>
    </div>
  </div>
</template>

<style scoped>
.skeleton-editor {
    padding: 0.5rem 0.25rem 0.25rem;
}
.editor-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 0.75rem;
}
.var-palette {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem;
    margin-bottom: 0.75rem;
}
.palette-label {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-gray-500);
}
.var-chip {
    font-family: var(--font-mono, monospace);
    border: 1px solid var(--color-slate-300);
}
.var-optional {
    color: var(--color-slate-400);
    margin-left: 0.25rem;
}
.editor-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.75rem;
}
@media (max-width: 900px) {
    .editor-grid { grid-template-columns: 1fr; }
}
.preview-pane {
    background: var(--color-slate-50);
    border: 1px solid var(--color-slate-200);
    border-radius: 0.4rem;
    padding: 0.6rem;
    font-size: 0.72rem;
    line-height: 1.4;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 27rem;
    overflow: auto;
}
</style>
