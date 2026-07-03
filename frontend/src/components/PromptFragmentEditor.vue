<script setup>
// Inline editor for a reusable prompt fragment, unfolded under its table row
// (or at the top of the table when creating). Mirrors PromptSkeletonEditor:
// purely presentational — it owns the draft, validates, and emits the payload;
// the parent panel owns the POST/PATCH.
import { ref } from 'vue'
import Button from './ui/Button.vue'

const props = defineProps({
  // The fragment being edited, or null when creating a new one.
  template: { type: Object, default: null },
  busy: { type: String, default: '' },
  saveError: { type: String, default: '' },
})
const emit = defineEmits(['save', 'cancel'])

const PROVIDER_OPTIONS = [
  { id: 'external-agent', label: 'External Agent' },
  { id: 'langchain', label: 'LangChain' },
]

const isNew = !props.template
const draft = ref({
  slug: props.template?.slug || '',
  label: props.template?.label || '',
  description: props.template?.description || '',
  body: props.template?.body || '',
  applies_to: [...(props.template?.applies_to || [])],
  default_for_providers: [...(props.template?.default_for_providers || [])],
})
const validationError = ref('')

function toggleAppliesTo(providerId) {
  const set = new Set(draft.value.applies_to)
  if (set.has(providerId)) {
    set.delete(providerId)
    // Defaults are constrained to applies_to.
    draft.value.default_for_providers = draft.value.default_for_providers.filter(p => p !== providerId)
  } else {
    set.add(providerId)
  }
  draft.value.applies_to = Array.from(set)
}

function toggleDefault(providerId) {
  const set = new Set(draft.value.default_for_providers)
  if (set.has(providerId)) {
    set.delete(providerId)
  } else {
    set.add(providerId)
    // Ensure applies_to covers any default.
    if (!draft.value.applies_to.includes(providerId)) {
      draft.value.applies_to = [...draft.value.applies_to, providerId]
    }
  }
  draft.value.default_for_providers = Array.from(set)
}

function onSave() {
  validationError.value = ''
  if (!draft.value.label.trim()) {
    validationError.value = 'Label is required.'
    return
  }
  if (!draft.value.body.trim()) {
    validationError.value = 'Body is required.'
    return
  }
  const payload = {
    label: draft.value.label.trim(),
    description: draft.value.description.trim(),
    body: draft.value.body,
    applies_to: draft.value.applies_to,
    default_for_providers: draft.value.default_for_providers,
  }
  if (isNew) payload.slug = draft.value.slug.trim()
  emit('save', payload)
}
</script>

<template>
  <div class="fragment-editor">
    <div class="editor-head">
      <span class="editor-title">{{ isNew ? 'New fragment' : 'Edit fragment' }}</span>
      <code v-if="!isNew" class="text-xs text-slate-500">{{ template.slug }}</code>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
      <label v-if="isNew" class="block">
        <span class="form-label">Slug (optional)</span>
        <input v-model="draft.slug" class="topics-input w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" placeholder="auto-generated from label">
      </label>
      <label class="block">
        <span class="form-label">Label *</span>
        <input v-model="draft.label" class="topics-input w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" required>
      </label>
      <label class="block md:col-span-2">
        <span class="form-label">Description</span>
        <input v-model="draft.description" class="topics-input w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" placeholder="One-line summary shown on the chip tooltip">
      </label>
      <div class="block md:col-span-2">
        <span class="form-label">Applies to providers</span>
        <div class="flex flex-wrap gap-2 mt-1">
          <button
            v-for="opt in PROVIDER_OPTIONS"
            :key="opt.id"
            type="button"
            class="topics-template-chip focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :class="{ 'topics-template-chip-active': draft.applies_to.includes(opt.id) }"
            @click="toggleAppliesTo(opt.id)"
          >
            {{ opt.label }}
          </button>
        </div>
      </div>
      <div class="block md:col-span-2">
        <span class="form-label">Default-on for providers</span>
        <div class="flex flex-wrap gap-2 mt-1">
          <button
            v-for="opt in PROVIDER_OPTIONS"
            :key="opt.id"
            type="button"
            class="topics-template-chip focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :class="{ 'topics-template-chip-active': draft.default_for_providers.includes(opt.id) }"
            @click="toggleDefault(opt.id)"
          >
            {{ opt.label }}
          </button>
        </div>
      </div>
      <label class="block md:col-span-2">
        <span class="form-label">Body *</span>
        <textarea v-model="draft.body" rows="10" class="topics-input w-full font-mono text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"></textarea>
      </label>
    </div>

    <div v-if="validationError || saveError" class="alert alert-info mt-2">
      {{ validationError || saveError }}
    </div>

    <div class="mt-4 flex gap-2 justify-end">
      <Button variant="secondary" :disabled="busy === 'save'" @click="emit('cancel')">Cancel</Button>
      <Button variant="primary" :disabled="busy === 'save'" @click="onSave">
        {{ busy === 'save' ? 'Saving…' : 'Save' }}
      </Button>
    </div>
  </div>
</template>

<style scoped>
.fragment-editor {
    padding: 0.5rem 0.25rem 0.25rem;
}
.editor-head {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.75rem;
}
.editor-title {
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--color-slate-800);
}
</style>
