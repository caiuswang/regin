<script setup>
// A real terminal on the session's tmux pane: xterm.js ↔ WebSocket ↔ a PTY
// running a grouped tmux client. Every key, control sequence and copy-mode
// gesture goes through untranslated — there is no key allowlist here because
// there is nothing in the path that could apply one.
//
// Frame type IS the protocol: binary frames carry raw bytes in both
// directions, text frames carry JSON control messages (resize). So no
// keystroke can ever be mistaken for a command.
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import api from '../../api'
import { useTerminalFocus } from '../../composables/useTerminalFocus.js'
import Button from '../ui/Button.vue'

const props = defineProps({
  traceId: { type: String, required: true },
  // Offer the chromeless full-page view. Off there, so it cannot link to
  // itself.
  popout: { type: Boolean, default: false },
  // Start in control. True on the dedicated page, which you reached by
  // deliberately choosing to operate this pane; false inside the trace view,
  // where you are reading a transcript and swallowing ⌘K would be a surprise.
  startInControl: { type: Boolean, default: false },
})

const host = ref(null)
const status = ref('connecting')
const detail = ref('')
const terminalFocus = useTerminalFocus()

// Read-only until the operator asks for control. Watching a pane and driving
// it are different intents, and only the second one justifies swallowing the
// page's own keyboard: in control, every key belongs to the pane, so ⌘K stops
// opening search and Escape stops closing things. Making that a deliberate,
// visible switch is what keeps it from being a surprise — and gives an
// obvious way back out.
const interactive = ref(props.startInControl)

// Chords the OS or browser reserves; the page cannot see them, let alone
// forward them. Listed so the UI can say so rather than leaving the operator
// to wonder why one key in twenty vanishes.
const RESERVED = ['⌘W', '⌘T', '⌘N', '⌘Q', '⌘Tab']

const term = shallowRef(null)
const fit = shallowRef(null)
const socket = shallowRef(null)
let observer = null
// A close we initiated (unmount, tab switch) must not read as a server
// refusal — the close handler cannot tell the two apart on its own.
let closing = false
// Bumped by every disconnect. `connect()` awaits a ticket, and a boolean
// alone cannot tell "we tore down while I was awaiting" from "we tore down
// and started again": switching session mid-await would clear the flag, let
// the stale attempt open a socket, then overwrite the reference to it — two
// live sockets, two PTYs, and both writing into one xterm.
let generation = 0

const encoder = new TextEncoder()

function sendResize() {
  if (!fit.value || !term.value) return
  fit.value.fit()
  if (socket.value?.readyState !== WebSocket.OPEN) return
  socket.value.send(JSON.stringify({
    type: 'resize', cols: term.value.cols, rows: term.value.rows,
  }))
}

async function connect() {
  const claimed = ++generation
  status.value = 'connecting'
  detail.value = ''
  let ticket
  try {
    ticket = (await api.post('/auth/stream-ticket', null)).ticket
  } catch {
    if (claimed !== generation) return
    status.value = 'error'
    detail.value = 'Could not authenticate.'
    return
  }
  if (closing || claimed !== generation) return
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${scheme}://${window.location.host}`
    + `/api/sessions/${encodeURIComponent(props.traceId)}/terminal`
    + `?ticket=${encodeURIComponent(ticket)}`
    + `&cols=${term.value.cols}&rows=${term.value.rows}`
  const ws = new WebSocket(url)
  ws.binaryType = 'arraybuffer'
  socket.value = ws
  // Every handler is generation-guarded for the same reason `connect` is: a
  // socket we have moved on from must not write into the live terminal, and
  // its `onclose` must not null out the reference to its successor.
  ws.onopen = () => {
    if (claimed !== generation) { ws.close(); return }
    status.value = 'open'
    sendResize()
    if (interactive.value) term.value?.focus()
  }
  ws.onmessage = (ev) => {
    if (typeof ev.data === 'string' || claimed !== generation) return
    term.value?.write(new Uint8Array(ev.data))
  }
  ws.onclose = (ev) => {
    if (claimed !== generation) return
    socket.value = null
    if (closing) return
    status.value = 'closed'
    // 1008 is the server's single refusal code; its reason is the only
    // explanation the operator gets, so surface it verbatim.
    detail.value = ev.code === 1008
      ? (ev.reason || 'Refused.')
      : 'Terminal disconnected.'
  }
}

function disconnect() {
  closing = true
  generation++
  socket.value?.close()
  socket.value = null
}

onMounted(() => {
  const t = new Terminal({
    convertEol: false,
    cursorBlink: true,
    fontSize: 12,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    theme: { background: '#0f172a', foreground: '#e2e8f0' },
  })
  const f = new FitAddon()
  t.loadAddon(f)
  // Claim every key xterm is offered. The default handler already returns
  // true for most, but it lets the browser keep a few (⌘K, function keys)
  // that a real terminal would send on; the SPA's own chords are handled by
  // `terminalFocus`, which suppresses them while this element has focus.
  t.attachCustomKeyEventHandler(() => interactive.value)
  t.options.disableStdin = !interactive.value
  t.open(host.value)
  // Only while actually holding focus AND in control: clicking anything else
  // on the page gives the app its chords back without a second switch.
  t.textarea?.addEventListener('focus', () => {
    if (interactive.value) terminalFocus.claim()
  })
  t.textarea?.addEventListener('blur', terminalFocus.release)
  f.fit()
  term.value = t
  fit.value = f
  t.onData((data) => {
    if (!interactive.value) return
    if (socket.value?.readyState === WebSocket.OPEN) {
      socket.value.send(encoder.encode(data))
    }
  })
  observer = new ResizeObserver(sendResize)
  observer.observe(host.value)
  if (interactive.value) {
    terminalFocus.claim()
    t.focus()
  }
  connect()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  disconnect()
  terminalFocus.release()
  term.value?.dispose()
  term.value = null
})

watch(() => props.traceId, () => {
  disconnect()
  closing = false
  term.value?.reset()
  connect()
})

function retry() {
  closing = false
  connect()
}

function setInteractive(on) {
  interactive.value = on
  const t = term.value
  if (!t) return
  t.options.disableStdin = !on
  if (on) {
    t.focus()
  } else {
    t.blur()
    terminalFocus.release()
  }
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div
      v-if="status !== 'open'"
      data-testid="terminal-status"
      class="flex items-center gap-2 border-b border-slate-200 px-3 py-1.5
             text-[11px] text-slate-600"
    >
      <span v-if="status === 'connecting'">Connecting to the pane…</span>
      <template v-else>
        <span class="text-red-700">{{ detail }}</span>
        <Button
          variant="secondary"
          size="sm"
          class="h-auto px-1.5 py-0.5 text-[11px]"
          @click="retry"
        >Reconnect</Button>
      </template>
    </div>
    <div ref="host" class="min-h-0 flex-1 bg-slate-900 p-1" data-terminal-pane />
    <div
      class="flex items-center justify-between gap-3 border-t border-slate-200
             px-3 py-1.5 text-[10px] text-slate-400"
    >
      <div class="flex min-w-0 items-center gap-2">
        <Button
          :variant="interactive ? 'primary' : 'secondary'"
          size="sm"
          class="h-auto shrink-0 px-2 py-0.5 text-[10px]"
          :aria-pressed="interactive"
          @click="setInteractive(!interactive)"
        >{{ interactive ? 'Release control' : 'Take control' }}</Button>
        <span v-if="interactive" class="truncate text-slate-500">
          Every key goes to the pane — <kbd>⌘K</kbd>, <kbd>Ctrl-C</kbd>,
          <kbd>Esc</kbd>. Not {{ RESERVED.join(', ') }}; the browser keeps those.
        </span>
        <span v-else class="truncate">
          Read-only: live output, no input. Take control to type.
        </span>
      </div>
      <RouterLink
        v-if="popout"
        :to="`/terminal/${props.traceId}`"
        class="shrink-0 underline decoration-dotted hover:text-slate-600
               focus-visible:outline-none focus-visible:ring-2
               focus-visible:ring-blue-500 rounded"
      >Open full page ↗</RouterLink>
    </div>
  </div>
</template>
