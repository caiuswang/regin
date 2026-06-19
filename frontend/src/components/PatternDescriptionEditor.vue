<script setup>
import { ref } from 'vue'
import api from '../api'
import { useFlash } from '../composables/useFlash'
import Button from './ui/Button.vue'

// Extracted from PatternDetailView (PR 2.4e). Owns the description
// editor's 3 refs (editing/edit/saving) and its 2 helpers. The parent
// passes the current description as a prop and reloads on `saved`.
const props = defineProps({
  slug: { type: String, required: true },
  description: { type: String, default: '' },
})
const emit = defineEmits(['saved'])

const { flash } = useFlash()

const editing = ref(false)
const editText = ref('')
const saving = ref(false)

function startEditing() {
  editText.value = props.description || ''
  editing.value = true
}

async function save() {
  saving.value = true
  const result = await api.post(
    `/patterns/${props.slug}/description`,
    { description: editText.value },
  )
  saving.value = false
  if (!result.ok) {
    flash(result.msg || 'Failed to save description', 'error')
    return
  }
  flash(result.msg)
  editing.value = false
  emit('saved')
}

function cancel() {
  editing.value = false
}
</script>

<template>
  <template v-if="!editing">
    <span v-if="description" class="pdv-description-text">{{ description }}</span>
    <span v-else class="pdv-meta-empty">No description</span><button
      type="button"
      class="pdv-inline-edit focus-visible:outline-2 focus-visible:outline-blue-500"
      @click="startEditing">Edit</button>
  </template>
  <div v-else class="pdv-edit-form">
    <textarea
      v-model="editText"
      rows="3"
      class="w-full text-sm"
      aria-label="Pattern description"
      placeholder="One- or two-sentence summary shown in the deployed skill listing." />
    <div class="btn-row mt-3">
      <Button
        variant="primary"
        size="sm"
        :disabled="saving"
        @click="save">{{ saving ? 'Saving…' : 'Save description' }}</Button>
      <Button
        variant="secondary"
        size="sm"
        @click="cancel">Cancel</Button>
    </div>
  </div>
</template>
