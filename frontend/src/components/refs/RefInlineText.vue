<script setup>
// Plain text whose references are drawn as chips: every other character is
// emitted as a text node, exactly as stored. No markdown, on purpose — the
// texts this draws (a defect's symptom) are quoted input, and a renderer
// would eat the reporter's asterisks and fold their line breaks
// (defect-symptom-verbatim). The caller's element owns the whitespace rule.
//
// A fragment, not an element: it lands inside the caller's `<p>`, so a text
// without references leaves that element holding text and nothing else.
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import RefChip from './RefChip.vue'
import { useRefResolve } from '../../composables/useRefResolve.js'
import { parseRefs } from '../../utils/refs.js'
import { followChip } from '../../utils/refRender.js'

const props = defineProps({
  text: { type: String, default: '' },
  // The project whose repo a `file:` reference is looked up in.
  project: { type: Number, default: null },
})

const found = computed(() => parseRefs(props.text || ''))

const parts = computed(() => {
  const text = props.text || ''
  const out = []
  let at = 0
  for (const r of found.value) {
    if (r.start > at) out.push({ text: text.slice(at, r.start) })
    out.push({ ref: r })
    at = r.end
  }
  if (at < text.length) out.push({ text: text.slice(at) })
  return out
})

const router = useRouter()
const { items } = useRefResolve(
  () => found.value.map((r) => r.ref),
  () => (found.value.some((r) => r.kind === 'file') ? props.project : null))

// Only a click that navigated stops here: one on a chip that is no link yet
// still belongs to the surface, which may take focus on it (the triage card).
function onChip(event) {
  followChip(event, (to) => router.push(to))
  if (event.defaultPrevented) event.stopPropagation()
}
</script>

<template>
  <template v-for="(part, i) in parts" :key="i">
    <RefChip v-if="part.ref" :target="part.ref.ref" :label="part.ref.label"
             :item="items[part.ref.ref]" @click="onChip" />
    <template v-else>{{ part.text }}</template>
  </template>
</template>
