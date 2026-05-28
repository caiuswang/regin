<script setup>
import { ref } from 'vue'
import api from '../api'
import { useFlash } from '../composables/useFlash'
import MarkdownContent from './MarkdownContent.vue'

// Extracted from PatternDetailView (PR 2.4d). Owns editing/editBody/
// saving refs + startEditing/saveContent. Renders the body markdown
// view or the textarea editor depending on `editing`. The wrapping
// <section v-show="activeTab === 'content'"> stays in the parent because
// it gates on the parent-owned activeTab.
const props = defineProps({
  slug: { type: String, required: true },
  bodyMd: { type: String, default: '' },
  concealedTexts: { type: Array, default: () => [] },
})
const emit = defineEmits(['saved'])

const { flash } = useFlash()

const editing = ref(false)
const editBody = ref('')
const saving = ref(false)

function startEditing() {
  editBody.value = props.bodyMd || ''
  editing.value = true
}

async function save() {
  saving.value = true
  const result = await api.post(`/patterns/${props.slug}/content`, {
    body: editBody.value,
  })
  saving.value = false
  if (!result.ok) {
    flash(result.msg || 'Failed to save', 'error')
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
  <div class="pdv-section-head">
    <h2 class="pdv-section-title">Content</h2>
    <div v-if="!editing">
      <button
        type="button"
        class="btn btn-secondary text-xs focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="startEditing">Edit</button>
    </div>
    <div v-else class="btn-row">
      <button
        type="button"
        :disabled="saving"
        class="btn btn-primary text-xs focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="save">
        {{ saving ? 'Saving...' : 'Save' }}
      </button>
      <button
        type="button"
        class="btn btn-secondary text-xs focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="cancel">Cancel</button>
    </div>
  </div>
  <div v-if="editing">
    <textarea
      v-model="editBody"
      rows="30"
      aria-label="Pattern content markdown"
      class="w-full font-mono text-sm border border-gray-300 rounded-lg px-4 py-3 focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none bg-gray-50"
      spellcheck="false"></textarea>
    <p class="text-xs text-gray-400 mt-1">
      Markdown format. Frontmatter (title, procedure, etc.) is preserved automatically.
    </p>
  </div>
  <MarkdownContent v-else :markdown="bodyMd" :concealed-texts="concealedTexts" />
</template>
