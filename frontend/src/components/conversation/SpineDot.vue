<script setup>
import { computed } from 'vue'
import { toolRowDotClass } from '../../utils/traceFormatters.js'
import { runStateOf } from '../../utils/liveRows.js'

// The node a conversation event hangs off the turn's spine. Positioning and
// the surface-colored gap live in the global `.spine-dot` rules; the per-kind
// fill comes straight off `toolRowDotClass` (denied → amber, failed/rejected
// → red, else `dotColor`), so the spine can never drift from the row colors.
//
// A span still in flight adds an expanding halo on top of that fill — the
// per-kind colour is what the dot MEANS and must survive; "unfinished" is a
// second axis, so it gets a second mark rather than overwriting the first.
// `state` is the ROW's reading (a subagent card is live when its agent is);
// the span's own state is the fallback for hosts that stamp none.
const props = defineProps({
  span: { type: Object, required: true },
  state: { type: String, default: '' },
})

const runState = computed(() => props.state || runStateOf(props.span))
</script>

<template>
  <span
    class="spine-dot"
    :class="[toolRowDotClass(span), runState ? 'spine-dot-run' : '']"
    :data-run-state="runState || undefined"
    aria-hidden="true"
  >
    <span class="spine-dot-core"></span>
  </span>
</template>
