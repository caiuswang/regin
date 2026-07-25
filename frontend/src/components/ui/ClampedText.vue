<script setup>
// Collapses long free-form text (review notes, comments, prompts) to a fixed
// number of lines with a Show more / Show less toggle. The toggle renders only
// when the content actually overflows the clamp, measured live so filter
// changes and window resizes keep it honest.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  lines: { type: Number, default: 6 },
  // Lets an outer "show the whole thing" control open this clamp too. Without
  // it a caller that already reveals the full text stacks a second clamp on
  // top of it, and the reader has to press two different buttons to read one
  // body — which is exactly what the trace view's "Show full prompt" did.
  forceExpanded: { type: Boolean, default: false },
})

const bodyEl = ref(null)
const selfExpanded = ref(false)
const overflowing = ref(false)
const expanded = computed(() => props.forceExpanded || selfExpanded.value)

function measure() {
  const el = bodyEl.value
  if (!el) return
  overflowing.value = expanded.value || el.scrollHeight > el.clientHeight + 1
}

let observer = null
onMounted(() => {
  measure()
  observer = new ResizeObserver(measure)
  if (bodyEl.value) observer.observe(bodyEl.value)
})
onBeforeUnmount(() => observer?.disconnect())

watch(expanded, () => nextTick(measure))
</script>

<template>
  <div>
    <div
      ref="bodyEl"
      :style="expanded ? null : { display: '-webkit-box', WebkitBoxOrient: 'vertical', WebkitLineClamp: String(lines), overflow: 'hidden' }"
    >
      <slot />
    </div>
    <button
      v-if="overflowing && !forceExpanded"
      type="button"
      class="mt-1 inline-flex min-h-7 items-center text-xs font-medium text-primary hover:underline focus-visible:outline-2 focus-visible:outline-ring rounded"
      :aria-expanded="expanded"
      @click="selfExpanded = !selfExpanded"
    >
      {{ expanded ? 'Show less' : 'Show more' }}
    </button>
  </div>
</template>
