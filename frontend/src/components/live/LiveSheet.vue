<script setup>
// Bottom sheet for the /live card: scrim + slide-up panel with a grabber,
// capped at 80dvh, internally scrollable. Positioned absolutely INSIDE the
// card (not a portal) so the tail underneath never reflows and its scroll
// position survives open/close. The shared ui/Dialog primitive is a
// centered modal — wrong shape for the phone sheet interaction, hence this
// sibling (spec v7 "sheets float highest").
//
// Copy action (spec v7.1): every sheet header carries a Copy button when a
// payload is provided — flips to a green "✓ Copied" for 1.5s;
// navigator.clipboard with an execCommand fallback.
import { ref, watch, onUnmounted } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'

const open = defineModel('open', { type: Boolean, default: false })
defineProps({
  title: { type: String, default: '' },
  copyPayload: { type: String, default: null },
})

const copied = ref(false)
let copyTimer = null

function close() {
  open.value = false
}

function onKey(e) {
  if (e.key === 'Escape') close()
}

watch(open, (v) => {
  if (v) document.addEventListener('keydown', onKey)
  else document.removeEventListener('keydown', onKey)
  copied.value = false
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKey)
  if (copyTimer) clearTimeout(copyTimer)
})

async function writeClipboard(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      return document.execCommand('copy')
    } finally {
      ta.remove()
    }
  }
}

async function onCopy(payload) {
  const ok = await writeClipboard(payload)
  if (!ok) return
  copied.value = true
  if (copyTimer) clearTimeout(copyTimer)
  copyTimer = setTimeout(() => { copied.value = false }, 1500)
}
</script>

<template>
  <div v-if="open" class="live-sheet-layer">
    <Button
      variant="ghost"
      class="live-scrim"
      aria-label="Dismiss"
      @click="close"
    />
    <div class="live-sheet" role="dialog" :aria-label="title" data-testid="live-sheet">
      <span class="live-grab" aria-hidden="true"></span>
      <div class="live-sheet-hd">
        <span class="live-sheet-title">{{ title }}</span>
        <Button
          v-if="copyPayload != null"
          variant="ghost"
          size="sm"
          class="live-sheet-copy"
          :class="{ 'live-sheet-copy-done': copied }"
          data-testid="live-sheet-copy"
          aria-label="Copy to clipboard"
          @click="onCopy(copyPayload)"
        >
          <Icon :name="copied ? 'check' : 'copy'" :size="12" />
          {{ copied ? 'Copied' : 'Copy' }}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          class="live-sheet-x"
          aria-label="Close"
          @click="close"
        >
          <Icon name="x" :size="14" />
        </Button>
      </div>
      <div class="live-sheet-body"><slot /></div>
    </div>
  </div>
</template>
