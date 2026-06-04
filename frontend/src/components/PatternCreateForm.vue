<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'
import Card from './Card.vue'
import { useFlash } from '../composables/useFlash'

// Extracted from PatternsView. Owns the "Create new pattern" form. All of its
// fields are local to the form, so they live here rather than on the parent —
// keeping the markup and its directives off PatternsView's budget. The parent
// gates this with v-if="showCreate" (so the form mounts fresh, matching the
// old behavior where Cancel/Create always left the fields cleared) and listens
// for `close` to drop showCreate.
defineProps({
  tags: { type: Array, default: () => [] },
})
const emit = defineEmits(['close'])

const { flash } = useFlash()
const router = useRouter()

const newTitle = ref('')
const newSlug = ref('')
const newDescription = ref('')
const newTags = ref([])
const creating = ref(false)

function autoSlug() {
  if (!newSlug.value) {
    newSlug.value = newTitle.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  }
}

async function createPattern() {
  if (!newTitle.value.trim()) { flash('Title is required', 'error'); return }
  creating.value = true
  const result = await api.post('/patterns/create', {
    title: newTitle.value.trim(),
    slug: newSlug.value.trim(),
    description: newDescription.value.trim(),
    tags: newTags.value,
  })
  creating.value = false
  if (result.ok) {
    flash(result.msg)
    router.push(`/patterns/${result.slug}`)
  } else {
    flash(result.msg || 'Failed to create', 'error')
  }
}
</script>

<template>
  <Card>
    <h2 class="card-header">Create new pattern</h2>
    <div class="grid grid-cols-2 gap-4 max-w-2xl">
      <div>
        <label class="block text-sm font-medium text-slate-700 mb-1">Title</label>
        <input v-model="newTitle" type="text" aria-label="Pattern title" @blur="autoSlug" placeholder="e.g. Distributed Lock with RLockUtil"
          class="input focus-visible:outline-2 focus-visible:outline-blue-500">
      </div>
      <div>
        <label class="block text-sm font-medium text-slate-700 mb-1">Slug</label>
        <input v-model="newSlug" type="text" aria-label="Pattern slug" placeholder="auto-generated from title"
          class="input font-mono focus-visible:outline-2 focus-visible:outline-blue-500">
      </div>
      <div class="col-span-2">
        <label class="block text-sm font-medium text-slate-700 mb-1">Description</label>
        <textarea v-model="newDescription" rows="2" aria-label="Pattern description" placeholder="What this pattern covers…"
          class="input focus-visible:outline-2 focus-visible:outline-blue-500"></textarea>
      </div>
      <div class="col-span-2" v-if="tags?.length">
        <label class="block text-sm font-medium text-slate-700 mb-1">Tags</label>
        <div class="flex flex-wrap gap-1.5">
          <label v-for="t in tags" :key="t.name" class="tag-pick"
            :class="{ 'is-active': newTags.includes(t.name) }">
            <input type="checkbox" :value="t.name" v-model="newTags" :aria-label="t.name" class="hidden">
            {{ t.name }}
          </label>
        </div>
      </div>
    </div>
    <div class="mt-4 flex gap-2">
      <button type="button" @click="createPattern" :disabled="creating"
              class="btn btn-primary focus-visible:outline-2 focus-visible:outline-blue-500">
        {{ creating ? 'Creating…' : 'Create' }}
      </button>
      <button type="button" @click="emit('close')"
              class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500">
        Cancel
      </button>
    </div>
  </Card>
</template>

<style scoped>
/* Tag pick chips (create form) */
.tag-pick {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    font-size: 0.75rem;
    background: #F1F5F9;
    color: #475569;
    border-radius: 0.375rem;
    padding: 0.25rem 0.625rem;
    cursor: pointer;
    transition: background-color 150ms, color 150ms;
}

.tag-pick:hover { background: #E2E8F0; }

.tag-pick.is-active { background: #DBEAFE; color: #1E40AF; font-weight: 500; }
</style>
