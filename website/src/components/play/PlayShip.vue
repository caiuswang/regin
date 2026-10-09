<script setup>
import { computed } from 'vue'

import { P_LEDGER } from '../../content/play.js'

const props = defineProps({ st: { type: Object, required: true } })
const lines = computed(() => P_LEDGER.slice(0, props.st.lines))
</script>

<template>
  <pre class="play-pre play-ledger"><span v-for="([t, ink], i) in lines" :key="i" :class="['play-ledger-line', `is-${ink}`]">{{ t || ' ' }}</span></pre>
  <div v-if="st.lines >= P_LEDGER.length" class="play-clear">
    <span class="play-clear-title">★ LEVEL CLEAR</span>
    <span class="play-clear-stats">ROUNDS 1 · ESCAPES 0 · NEVER WALKED BACK</span>
  </div>
</template>
