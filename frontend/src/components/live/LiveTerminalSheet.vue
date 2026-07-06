<script setup>
// Raw-terminal-peek sheet body for the /live card. One-shot GET on open
// (+ manual refresh) — deliberately NOT a live stream. Fills the one gap the
// structured tail above can't: harness-only interactions (slash commands,
// the composer's insert-mode hint, the box-drawn status line) that never
// produce a span because they never leave the Claude Code TUI. `html` is
// already-converted (server-side ansi_html.convert()) markup — safe to drop
// straight into the DOM since every literal screen character was escaped
// before the wrapping <span>/<a> tags were added.
//
// The pane's BOTTOM is the live status — the prompt, the spinner, the
// composer's insert-mode hint — so `.live-term-body` is its own scroll
// region (not left to the outer sheet) and always opens scrolled to its
// bottom, the same follow-tail idiom LiveSessionView uses for the main tail.
import { ref, nextTick, onMounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  sessionId: { type: String, default: '' },
  bridgePane: { type: String, default: '' },
})

const html = ref('')
const phase = ref('loading') // loading | ready | failed
const detail = ref('')
const termBodyEl = ref(null)

async function fetchScreen() {
  phase.value = 'loading'
  let res = null
  try {
    res = await api.get(`/sessions/${props.sessionId}/bridge-screen`)
  } catch { res = null }
  if (res && res.ok) {
    html.value = res.html
    phase.value = 'ready'
  } else {
    phase.value = 'failed'
    detail.value = res?.detail || 'capture failed'
  }
  await nextTick()
  const el = termBodyEl.value
  if (el) el.scrollTop = el.scrollHeight
}

onMounted(fetchScreen)
</script>

<template>
  <div class="live-term-sheet" data-testid="live-terminal-sheet">
    <div class="live-term-toolbar">
      <span class="live-term-hint">one-shot snapshot · not live</span>
      <Button
        variant="ghost"
        size="sm"
        class="live-term-refresh"
        :loading="phase === 'loading'"
        data-testid="live-terminal-refresh"
        @click="fetchScreen"
      >
        <Icon name="refresh-cw" :size="12" />
        refresh
      </Button>
    </div>
    <pre
      v-if="phase !== 'failed'"
      ref="termBodyEl"
      class="live-term-body"
      data-testid="live-terminal-body"
      v-html="html"
    ></pre>
    <p v-else class="live-empty" data-testid="live-terminal-error">{{ detail }}</p>
    <p v-if="bridgePane" class="live-term-foot">
      pane {{ bridgePane }} · read-only, no keys sent
    </p>
  </div>
</template>
