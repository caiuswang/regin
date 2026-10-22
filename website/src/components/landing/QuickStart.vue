<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Button from '../ui/Button.vue'

// In PowerShell `./scripts/setup.sh` does not run: Windows opens it in the app
// associated with `.sh`. Windows visitors get setup.cmd preselected.
const SETUPS = {
  unix: { label: 'MACOS / LINUX', run: 'cd regin && ./scripts/setup.sh' },
  windows: { label: 'WINDOWS', run: 'cd regin; .\\scripts\\setup.cmd' },
}
const CLONE = 'git clone https://github.com/caiuswang/regin'
const LABELS = { idle: 'COPY', copied: 'COPIED ✓', failed: 'COPY FAILED' }
const RESET_MS = 2000

const state = ref('idle')
const os = ref('unix')
const command = computed(() => `${CLONE}\n${SETUPS[os.value].run}`)
let resetTimer = null

onMounted(() => {
  const platform = navigator.userAgentData?.platform || navigator.platform || ''
  if (/^win/i.test(platform)) os.value = 'windows'
})

function show(next) {
  state.value = next
  clearTimeout(resetTimer)
  resetTimer = setTimeout(() => { state.value = 'idle' }, RESET_MS)
}

async function copy() {
  try {
    if (!navigator.clipboard) throw new Error('Clipboard API unavailable')
    await navigator.clipboard.writeText(command.value)
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
      <span class="quick-start-os" role="group" aria-label="Operating system">
        <Button
          v-for="(setup, key) in SETUPS" :key="key" variant="bare" class="os-key"
          :class="{ 'is-on': os === key }" :aria-pressed="os === key" @click="os = key"
        >{{ setup.label }}</Button>
      </span>
      <Button variant="bare" class="copy-key" :class="{ 'is-failed': state === 'failed' }" aria-label="Copy quick-start commands" @click="copy">{{ LABELS[state] }}</Button>
      <span class="sr-only" aria-live="polite">{{ state === 'idle' ? '' : LABELS[state] }}</span>
    </div>
    <pre class="quick-start-pre"><span class="tone-dim">$ </span>git clone https://github.com/caiuswang/regin
<span class="tone-dim">$ </span>{{ SETUPS[os].run }}</pre>
  </div>
</template>
