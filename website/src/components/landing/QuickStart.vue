<script setup>
import { onBeforeUnmount, ref } from 'vue'
import Button from '../ui/Button.vue'

const QUICK_START = 'git clone https://github.com/caiuswang/regin\ncd regin && ./scripts/setup.sh'
const LABELS = { idle: 'COPY', copied: 'COPIED ✓', failed: 'COPY FAILED' }
const RESET_MS = 2000

const state = ref('idle')
let resetTimer = null

function show(next) {
  state.value = next
  clearTimeout(resetTimer)
  resetTimer = setTimeout(() => { state.value = 'idle' }, RESET_MS)
}

async function copy() {
  try {
    if (!navigator.clipboard) throw new Error('Clipboard API unavailable')
    await navigator.clipboard.writeText(QUICK_START)
    show('copied')
  } catch {
    show('failed')
  }
}

onBeforeUnmount(() => clearTimeout(resetTimer))
</script>

<template>
  <div class="quick-start">
    <div class="quick-start-bar">
      <span>QUICK START · PYTHON + NODE</span>
      <Button variant="bare" class="copy-key" :class="{ 'is-failed': state === 'failed' }" aria-label="Copy quick-start commands" @click="copy">{{ LABELS[state] }}</Button>
      <span class="sr-only" aria-live="polite">{{ state === 'idle' ? '' : LABELS[state] }}</span>
    </div>
    <pre class="quick-start-pre"><span class="tone-dim">$ </span>git clone https://github.com/caiuswang/regin
<span class="tone-dim">$ </span>cd regin &amp;&amp; ./scripts/setup.sh</pre>
  </div>
</template>
