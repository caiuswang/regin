<script setup>
// The pane terminal on its own page: no sidebar, no trace chrome, no span
// rail — a full-viewport terminal, which is what it is for. Rendered outside
// AppLayout (route meta `chromeless`), so nothing competes for the width or
// the keyboard.
import { onMounted, ref } from 'vue'
import { useRoute, RouterLink } from 'vue-router'
import api from '../api'
import SessionTerminalPane from '../components/trace/SessionTerminalPane.vue'

const route = useRoute()
const traceId = route.params.id
const title = ref('')

onMounted(async () => {
  try {
    const s = await api.get(`/sessions/${traceId}`)
    title.value = s?.title || s?.session?.title || ''
  } catch {
    // A missing title costs the header a line; the terminal itself does not
    // depend on it, and the socket reports its own refusals.
  }
})
</script>

<template>
  <div class="flex h-screen min-h-0 flex-col bg-white">
    <header
      class="flex shrink-0 items-center gap-3 border-b border-slate-200 px-3 py-2"
    >
      <RouterLink
        :to="`/trace/sessions/${traceId}`"
        class="shrink-0 rounded px-1.5 py-0.5 text-xs text-slate-500
               hover:bg-slate-50 hover:text-slate-700 focus-visible:outline-none
               focus-visible:ring-2 focus-visible:ring-blue-500"
      >← Trace</RouterLink>
      <div class="min-w-0 flex-1">
        <div class="truncate text-xs font-medium text-slate-700">
          {{ title || 'Terminal' }}
        </div>
        <div class="truncate font-mono text-[10px] text-slate-400">{{ traceId }}</div>
      </div>
    </header>
    <div class="min-h-0 flex-1">
      <SessionTerminalPane :trace-id="String(traceId)" :start-in-control="true" />
    </div>
  </div>
</template>
