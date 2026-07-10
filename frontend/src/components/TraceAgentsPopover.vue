<script setup>
// "Agents" roster button for the desktop trace header — the reduced desktop
// analogue of live/LiveAgentSheet.vue (no detail pane; the primary click
// scopes). Below xl — and in the ≥xl 'full' takeover presentation — it drops
// a pick-to-scope popover (running first, then finished). In the ≥xl SPLIT
// presentation (`paneMode`) the button instead opens TraceAgentPane in roster
// mode — the roster fills the pane rather than a corner menu — so the popover
// is suppressed and the click just emits `open-roster`. Button absent when
// the roster is empty.
import { ref, watch, onUnmounted } from 'vue'
import Button from './ui/Button.vue'
import TraceAgentRoster from './TraceAgentRoster.vue'

const props = defineProps({
  runningAgents: { type: Array, default: () => [] },
  finishedAgents: { type: Array, default: () => [] },
  // running + waiting count for the badge (useLiveAgents.runningCount).
  runningCount: { type: Number, default: 0 },
  serverNow: { type: String, default: '' },
  serverNowAt: { type: Number, default: 0 },
  // ≥xl: the button opens the companion pane in roster mode instead of the
  // popover (the parent owns the pane state).
  paneMode: { type: Boolean, default: false },
})
const emit = defineEmits(['scope', 'open-roster'])

const open = ref(false)
const rootEl = ref(null)

function onDocClick(e) {
  if (rootEl.value && !rootEl.value.contains(e.target)) open.value = false
}
function onDocKeydown(e) {
  if (e.key === 'Escape') open.value = false
}
// The menu is fixed-position, measured from the button only at open — a page
// scroll or window resize while it's up would leave it visually detached from
// the button. Close instead of re-tracking (cheapest correct behavior). A
// scroll inside the menu's own overflow-y-auto is exempt: scrolling the
// roster must not self-dismiss it.
function onAnyScroll(e) {
  if (rootEl.value && rootEl.value.contains(e.target)) return
  open.value = false
}
function onWinResize() { open.value = false }
function unbindDoc() {
  document.removeEventListener('mousedown', onDocClick)
  document.removeEventListener('keydown', onDocKeydown)
  document.removeEventListener('scroll', onAnyScroll, { capture: true })
  window.removeEventListener('resize', onWinResize)
}
watch(open, (on) => {
  if (on) {
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onDocKeydown)
    document.addEventListener('scroll', onAnyScroll, { capture: true, passive: true })
    window.addEventListener('resize', onWinResize)
  } else {
    unbindDoc()
  }
})
onUnmounted(unbindDoc)

// Fixed positioning clamped to the viewport: right-anchored `absolute` put the
// 320px menu off-screen when the button sits left of x=320 (narrow layouts —
// at 390px the rows lost ~142px). Measured from the button on every open, so
// it also tracks resizes across opens.
const popStyle = ref({})
function placePopover() {
  const rect = rootEl.value?.getBoundingClientRect()
  const vw = window.innerWidth
  const width = Math.min(320, vw - 16)
  const left = Math.min(Math.max(8, (rect?.right ?? vw) - width), vw - width - 8)
  popStyle.value = {
    position: 'fixed',
    top: `${(rect?.bottom ?? 0) + 6}px`,
    left: `${left}px`,
    width: `${width}px`,
  }
}

function onButton() {
  if (props.paneMode) { emit('open-roster'); return }
  if (open.value) { open.value = false; return }
  placePopover()
  open.value = true
}

function pick(agent) {
  open.value = false
  emit('scope', agent.agentId)
}
</script>

<template>
  <div
    v-if="runningAgents.length || finishedAgents.length"
    ref="rootEl"
  >
    <Button
      variant="ghost"
      class="gap-1.5 px-3 py-1 h-auto rounded-full border border-slate-200 bg-white text-xs text-slate-600 hover:bg-violet-50 hover:border-violet-300 hover:text-violet-700"
      data-testid="trace-agents-btn"
      :aria-haspopup="paneMode ? undefined : 'true'"
      :aria-expanded="paneMode ? undefined : open"
      @click="onButton"
    >
      <span class="text-violet-500" aria-hidden="true">◈</span>
      Agents
      <span
        v-if="runningCount"
        class="inline-flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 rounded-full bg-violet-500 text-white text-[10px] tabular-nums"
        data-testid="trace-agents-badge"
      >{{ runningCount }}</span>
    </Button>
    <div
      v-if="open && !paneMode"
      class="z-popover max-h-96 overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-lg p-1.5 text-left"
      :style="popStyle"
      data-testid="trace-agents-popover"
      role="menu"
    >
      <TraceAgentRoster
        :running-agents="runningAgents"
        :finished-agents="finishedAgents"
        :server-now="serverNow"
        :server-now-at="serverNowAt"
        :active="open"
        @pick="pick"
      />
    </div>
  </div>
</template>
