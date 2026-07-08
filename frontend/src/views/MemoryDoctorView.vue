<script setup>
// Standalone page for the memory-store health panel: /memory hosts the
// browse/curate surface, this hosts the census + reflect-pipeline console.
import { ref } from 'vue'
import api from '../api'
import { useFetch } from '../composables/useFetch'
import { formatReflectSummary } from '../utils/reflectSummary'
import Button from '../components/ui/Button.vue'
import MemoryDoctor from '../components/memory/MemoryDoctor.vue'

const { data: stats, loading, error, refresh } =
  useFetch({ path: '/memory/stats' })

const reflectSummary = ref('')
const reflectError = ref('')
const lastResult = ref(null)
const reflecting = ref(false)

async function runReflect() {
  reflecting.value = true
  reflectError.value = ''
  try {
    const r = await api.post('/memory/reflect', {})
    lastResult.value = r
    reflectSummary.value = formatReflectSummary(r)
  } catch {
    reflectError.value = 'Reflect run failed — see the server log.'
  } finally {
    reflecting.value = false
  }
  await refresh()
}
</script>

<template>
  <div>
    <div class="flex flex-wrap items-center gap-3 mb-1">
      <h1 class="text-xl font-semibold text-slate-900">Memory doctor</h1>
      <span v-if="stats" class="text-xs text-slate-500 font-mono">{{ stats.total || 0 }} memories</span>
      <div class="ml-auto flex items-center gap-2">
        <Button variant="secondary" size="sm" :disabled="loading || reflecting" @click="runReflect">
          {{ reflecting ? 'Running…' : 'Run reflect' }}
        </Button>
      </div>
    </div>
    <p class="text-sm text-slate-500 mb-3">
      Store health, consolidation debt, and the reflect pipeline's stages —
      the operate side of <router-link to="/memory" class="text-blue-600 hover:underline">Memory</router-link>.
    </p>
    <p v-if="reflectSummary" class="text-xs text-slate-500 font-mono mb-3">{{ reflectSummary }}</p>
    <p v-if="reflectError" class="text-sm text-red-600 mb-3">{{ reflectError }}</p>

    <p v-if="error" class="text-sm text-red-600 mb-3">
      Could not load memory stats ({{ error }}) — is the server running?
    </p>
    <p v-else-if="loading && !stats" class="text-sm text-slate-500">Loading…</p>
    <MemoryDoctor
      v-else-if="stats"
      :stats="stats"
      :reflecting="reflecting"
      :last-result="lastResult"
      @reflect="runReflect"
    />
  </div>
</template>
