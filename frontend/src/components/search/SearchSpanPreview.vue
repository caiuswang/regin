<script setup>
// A span hit, read through the SAME renderers the trace uses: rule lists stay
// lists, a Bash span keeps its command and its coloured output, an edit keeps
// its diff. `presentFor` is pure over the span, so the palette can drive it
// with nothing but the span object.
import { computed, ref, shallowRef, watch } from 'vue'
import PresentContent from '../present/PresentContent.vue'
import SearchLinePreview from './SearchLinePreview.vue'
import { presentFor } from '../../utils/presentModel.js'
import { countHits } from '../../utils/searchDocs.js'
import { resolveSearchSpan, useTraceSearchSource } from '../../composables/useTraceSearchSource.js'

const props = defineProps({
  doc: { type: Object, required: true },
  query: { type: String, default: '' },
})

const { source } = useTraceSearchSource()
const hydrating = ref(false)
const hydrated = shallowRef(null)

const span = computed(() => props.doc.span
  || (source.value?.spans?.() || []).find(s => s.span_id === props.doc.id)
  || (hydrated.value?.span_id === props.doc.id ? hydrated.value : null))

const vm = computed(() => (span.value ? presentFor(span.value) : null))

// `spanBody` indexes fields `presentFor` never renders — description, pattern,
// rule_file, file_path. A span matching only in those has a real, countable
// hit and an empty view model, so the rich renderer would answer "no body —
// metadata only" directly beneath a row badge reading "N matches in body".
// The indexed text is the honest answer for exactly that case.
const rich = computed(() =>
  vm.value && !(vm.value.empty && countHits(props.doc.body, props.query) > 0))

const traceId = computed(() => source.value?.traceId || '')

watch(() => props.doc.id, async (id) => {
  if (!id || span.value) { hydrating.value = false; return }
  hydrating.value = true
  const found = await resolveSearchSpan(id)
  // ↑↓ walks a row per keypress, so several hydrations can be in flight; a
  // straggler must not paint over the row the keyboard has since moved to.
  if (props.doc.id !== id) return
  hydrated.value = found
  hydrating.value = false
}, { immediate: true })
</script>

<template>
  <!-- `only-body` false because the palette has no "content only" toggle for a
       bodyless span's hint to point at — its ↑↓ walks every row. -->
  <PresentContent
    v-if="rich"
    :vm="vm"
    :trace-id="traceId"
    :only-body="false"
    :highlight="query"
  />
  <div v-else-if="hydrating" class="span-hydrating">
    <span class="sr-only">Loading span…</span>
    <span v-for="bar in 5" :key="bar" class="hydrate-bar" aria-hidden="true" />
  </div>
  <SearchLinePreview v-else :doc="doc" :query="query" />
</template>

<style scoped>
.span-hydrating { display: flex; flex-direction: column; gap: 0.625rem; padding-top: 0.25rem; }

.hydrate-bar {
  height: 0.6875rem;
  border-radius: 5px;
  background: var(--color-surface-2);
  animation: hydrate-pulse 1.1s ease-in-out infinite;
}

.hydrate-bar:nth-child(2) { width: 42%; }
.hydrate-bar:nth-child(3) { width: 92%; }
.hydrate-bar:nth-child(4) { width: 78%; }
.hydrate-bar:nth-child(5) { width: 88%; }
.hydrate-bar:nth-child(6) { width: 54%; }

@keyframes hydrate-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}

@media (prefers-reduced-motion: reduce) {
  .hydrate-bar { animation: none; }
}
</style>
