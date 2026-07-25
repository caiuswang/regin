<script setup>
import { toolRowDotClass } from '../../utils/traceFormatters.js'

// The node a conversation event hangs off the turn's spine. Positioning and
// the surface-colored gap live in the global `.spine-dot` rules; the per-kind
// fill comes straight off `toolRowDotClass` (denied → amber, failed/rejected
// → red, else `dotColor`), so the spine can never drift from the row colors.
defineProps({
  span: { type: Object, required: true },
})
</script>

<template>
  <span class="spine-dot" :class="toolRowDotClass(span)" aria-hidden="true">
    <span class="spine-dot-core"></span>
  </span>
</template>
