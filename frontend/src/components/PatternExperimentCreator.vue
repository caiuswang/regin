<script setup>
import { ref } from 'vue'
import api from '../api'
import { useFlash } from '../composables/useFlash'

// Extracted from PatternDetailView (PR 2.4c). Owns the create-experiment
// form: name + sections checkbox group + inline validation. The
// activate/deactivate buttons on the experiment LIST stay in the parent
// because they share `expProcessing` with other per-experiment actions.
const props = defineProps({
  patternSlug: { type: String, required: true },
  availableSections: { type: Array, default: () => [] },
})
const emit = defineEmits(['saved'])

const { flash } = useFlash()

const open = ref(false)
const name = ref('')
const sections = ref([])
const errors = ref({})

async function create() {
  errors.value = {}
  if (!name.value.trim()) errors.value.name = 'Name is required'
  if (!sections.value.length) errors.value.sections = 'Select at least one section'
  if (Object.keys(errors.value).length) return
  const result = await api.post('/experiments', {
    pattern_slug: props.patternSlug,
    name: name.value,
    sections: sections.value,
  })
  if (!result.ok) {
    flash(result.msg || 'Failed to create experiment', 'error')
    return
  }
  flash(result.msg)
  name.value = ''
  sections.value = []
  open.value = false
  emit('saved')
}
</script>

<template>
  <details :open="open">
    <summary
      class="btn btn-secondary text-xs focus-visible:outline-2 focus-visible:outline-blue-500"
      @click.prevent="open = !open">Create experiment</summary>
    <div v-if="open" class="mt-3">
      <div class="mb-3">
        <label class="text-sm text-gray-500">Name</label>
        <input
          type="text"
          v-model="name"
          aria-label="Experiment name"
          placeholder="hide-disciplines"
          :class="[
            'text-sm border rounded-md px-2.5 py-1.5 w-full max-w-sm focus:outline-none focus:ring-2',
            errors.name ? 'border-red-400 focus:ring-red-400' : 'border-gray-300 focus:ring-blue-500',
          ]">
        <p v-if="errors.name" class="text-xs text-red-600 mt-1">{{ errors.name }}</p>
      </div>
      <div class="mb-3">
        <label class="text-sm text-gray-500">Sections to conceal</label>
        <div class="flex flex-wrap gap-x-4 gap-y-1 mt-1">
          <label
            v-for="s in availableSections"
            :key="s"
            class="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              :value="s"
              v-model="sections"
              :aria-label="s"
              class="rounded border-gray-300">
            <code class="text-xs">{{ s }}</code>
          </label>
        </div>
        <p v-if="errors.sections" class="text-xs text-red-600 mt-1">{{ errors.sections }}</p>
      </div>
      <button
        type="button"
        class="btn btn-primary text-xs focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="create">Create</button>
    </div>
  </details>
</template>
