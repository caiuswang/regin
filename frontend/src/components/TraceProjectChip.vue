<script setup>
// Which project a session ran in. A terminal keeps this on screen at all
// times; the trace header carried a trace id, a model and a clock but never
// the working directory, so two checkouts of one repo read identically.
//
// Rendered statusline-style: the parent path muted, the directory you are
// "in" emphasized. `compact` is the same fact reduced to the folder name for
// the collapsed header row, which has no room for a path.
import { computed } from 'vue'

// Takes the whole session rather than the two fields it reads, matching its
// sibling strips (TraceVitalsStrip, TraceOverviewStrip) — the header threads
// one object to all of them.
const props = defineProps({
  session: { type: Object, required: true },
  compact: { type: Boolean, default: false },
})

// `repo` is the REGISTERED repo name, which can differ from the folder on disk.
const path = computed(() => (props.session?.cwd || '').replace(/\/+$/, ''))
const name = computed(() => {
  const p = path.value
  return p ? p.slice(p.lastIndexOf('/') + 1) : (props.session?.repo || '')
})
const parent = computed(() => {
  const p = path.value
  const i = p.lastIndexOf('/')
  return i > 0 ? p.slice(0, i) : ''
})
// The repo name only when it disagrees with the folder: that is the name the
// rest of regin keys on, and a silent disagreement is exactly what a reader
// needs told. Identical names would just be said twice.
const repoAlias = computed(() => {
  const r = props.session?.repo || ''
  return r && r !== name.value ? r : ''
})
const title = computed(() => {
  if (!name.value) return ''
  const where = path.value || name.value
  return repoAlias.value
    ? `working directory: ${where} — registered repo "${repoAlias.value}"`
    : `working directory: ${where}`
})
</script>

<template>
  <span
    v-if="name && compact"
    class="shrink-0 font-mono text-[11px] text-slate-500"
    data-testid="trace-header-project-compact"
    :title="title"
  >{{ name }}</span>
  <span
    v-else-if="name"
    class="inline-flex max-w-full items-baseline gap-1 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-mono text-[11px] text-slate-600"
    data-testid="trace-header-project"
    :title="title"
  >
    <span aria-hidden="true">📁</span>
    <span v-if="parent" class="hidden truncate text-slate-400 sm:inline">{{ parent }}/</span>
    <span class="font-semibold text-slate-700">{{ name }}</span>
    <span v-if="repoAlias" class="text-slate-400">· {{ repoAlias }}</span>
  </span>
</template>
