<script setup>
import { ref, onMounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'

// Named clusters synthesised by reflect. Each card expands to its members;
// clicking a member surfaces it in the parent's detail pane.
const emit = defineEmits(['select'])

const topics = ref([])
const expandedId = ref(null)
const members = ref({})
const loading = ref(false)

async function reload() {
  loading.value = true
  try {
    topics.value = (await api.get('/memory/topics')).topics || []
  } finally {
    loading.value = false
  }
}

async function toggle(topic) {
  if (expandedId.value === topic.id) {
    expandedId.value = null
    return
  }
  expandedId.value = topic.id
  if (!members.value[topic.id]) {
    const data = await api.get(`/memory/topics/${topic.id}`)
    members.value = { ...members.value, [topic.id]: data.members || [] }
  }
}

function snippet(text) {
  const t = (text || '').replace(/\s+/g, ' ').trim()
  return t.length > 120 ? `${t.slice(0, 120)}…` : t
}

onMounted(reload)
defineExpose({ reload })
</script>

<template>
  <section v-if="topics.length" class="mb-4 mt-4">
    <div class="flex items-center gap-2 mb-2">
      <h2 class="text-sm font-semibold text-slate-800">Topics</h2>
      <span class="text-[11px] text-slate-400 font-mono">{{ topics.length }} clusters</span>
    </div>
    <ul class="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      <li v-for="t in topics" :key="t.id" class="relative overflow-hidden rounded-lg border border-slate-200 bg-white transition-shadow hover:shadow-sm">
        <span class="absolute inset-y-0 left-0 w-1 bg-violet-400" aria-hidden="true"></span>
        <button
          type="button"
          class="w-full text-left pl-4 pr-3 py-2 rounded-lg focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="expandedId === t.id ? 'bg-slate-50' : 'hover:bg-slate-50'"
          @click="toggle(t)"
        >
          <div class="flex items-center gap-2 mb-0.5">
            <span class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-violet-100 text-violet-700">topic</span>
            <span class="text-[10px] font-mono text-slate-400">{{ t.member_count }}×</span>
          </div>
          <p class="text-sm font-medium text-slate-800 leading-snug">{{ t.name }}</p>
          <p v-if="t.summary" class="text-xs text-slate-500 leading-snug mt-0.5">{{ snippet(t.summary) }}</p>
        </button>
        <ul v-if="expandedId === t.id" class="border-t border-slate-100 pl-4 pr-3 py-2 space-y-1">
          <li v-for="m in (members[t.id] || [])" :key="m.id">
            <Button
              variant="link"
              size="sm"
              class="text-left text-slate-600 hover:text-blue-600 hover:no-underline w-full truncate"
              @click="emit('select', m.id)"
            >· {{ m.title || m.kind }}</Button>
          </li>
          <li v-if="!(members[t.id] || []).length" class="text-xs text-slate-400">No live members.</li>
        </ul>
      </li>
    </ul>
  </section>
</template>
