<script setup>
import { ref, watch } from 'vue'
import api from '../api'
import { useFlash } from '../composables/useFlash'

// Extracted from PatternDetailView (PR 2.4a). Owns its three pieces of
// local state (editingTags, selectedTags, newTagName) plus the
// saveTags() POST. The parent provides the current tag list + the
// catalog of all tags (read-only); after a successful save the
// component emits `saved` so the parent can refresh its loaded data.
const props = defineProps({
  slug: { type: String, required: true },
  tags: { type: Array, default: () => [] },
  allTags: { type: Array, default: () => [] },
})
const emit = defineEmits(['saved'])

const { flash } = useFlash()

const editing = ref(false)
const selectedTags = ref([])
const newTagName = ref('')

// Re-seed the checkbox selection whenever the parent loads/reloads the
// pattern. The parent used to do this inline inside its `load()` block;
// owning the local mirror here keeps the parent dumb.
watch(
  () => props.tags,
  (next) => {
    selectedTags.value = (next || []).map((t) => t.name)
  },
  { immediate: true },
)

async function save() {
  const result = await api.post(`/patterns/${props.slug}/tags`, {
    tags: selectedTags.value,
    new_tag: newTagName.value,
  })
  if (!result.ok) {
    flash(result.msg || 'Failed to save tags', 'error')
    return
  }
  flash(result.msg)
  newTagName.value = ''
  editing.value = false
  emit('saved')
}

function cancel() {
  editing.value = false
}
</script>

<template>
  <template v-if="!editing">
    <span v-if="!tags?.length" class="pdv-meta-empty">No tags</span>
    <router-link
      v-for="t in tags"
      :key="t.name"
      :to="{ path: '/patterns', query: { tag: t.name } }"
      class="badge badge-gray hover:bg-gray-200 no-underline"
      >{{ t.name }}</router-link>
    <button
      type="button"
      class="pdv-inline-edit focus-visible:outline-2 focus-visible:outline-blue-500"
      @click="editing = true">Edit</button>
  </template>
  <div v-else class="pdv-edit-form">
    <div class="flex flex-wrap gap-x-4 gap-y-1">
      <label
        v-for="t in allTags"
        :key="t.name"
        class="flex items-center gap-1.5 text-sm">
        <input
          type="checkbox"
          :value="t.name"
          v-model="selectedTags"
          :aria-label="t.name"
          class="rounded border-gray-300">
        {{ t.name }}
      </label>
    </div>
    <div class="flex gap-2 items-end mt-3">
      <label class="pdv-field flex-1 max-w-xs">
        <span>New tag</span>
        <input
          type="text"
          v-model="newTagName"
          aria-label="New tag name"
          placeholder="tag-name">
      </label>
      <button
        type="button"
        class="btn btn-primary text-xs focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="save">Save tags</button>
      <button
        type="button"
        class="btn btn-secondary text-xs focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="cancel">Cancel</button>
    </div>
  </div>
</template>
