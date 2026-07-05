<script setup>
// Micro context-usage meter for the /live card: "ctx N%" + a 24×3px bar,
// amber past 80%. Fed the SEGMENT-AWARE live-peak ctx% (context_pct: the
// current post-compaction segment's peak) — never the whole-session peak,
// which would read a scary stale value on a compacted session. Rendered in
// two places (header meta line, composer bridge row); `verbose` adds the
// "— nearing compaction" tail once past the amber threshold.
import { computed } from 'vue'

const props = defineProps({
  pct: { type: Number, default: null },
  verbose: { type: Boolean, default: false },
})

const shown = computed(() => Number.isFinite(props.pct))
const rounded = computed(() => Math.round(props.pct))
const warn = computed(() => shown.value && props.pct > 80)
const fillWidth = computed(() => `${Math.min(100, Math.max(0, props.pct))}%`)
const label = computed(() => (props.verbose && warn.value
  ? `ctx ${rounded.value}% — nearing compaction`
  : `ctx ${rounded.value}%`))
</script>

<template>
  <span
    v-if="shown"
    class="live-ctx-meter"
    :class="{ 'live-ctx-warn': warn }"
    data-testid="live-ctx-meter"
  >
    <span class="live-ctx-txt">{{ label }}</span>
    <span class="live-ctx-bar" aria-hidden="true">
      <span class="live-ctx-fill" :style="{ width: fillWidth }"></span>
    </span>
  </span>
</template>
