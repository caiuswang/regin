<script setup>
// The /live card's raw-terminal peek, reachable from the desktop trace page.
//
// The transcript only ever shows what produced a span. Everything that stays
// inside the Claude Code TUI — the status line, slash commands, the composer's
// own hints — is invisible to it, and that is exactly the material you want
// when the question is "what is this session actually doing right now". The
// /live card has answered that since the bridge landed; the conversation view
// had no route to it at all.
//
// Body is `LiveTerminalSheet` unchanged — it owns the fetch, the refresh, the
// scroll-to-bottom and the failure copy, so the two surfaces cannot drift.
// Only the container differs: a modal here, a bottom sheet there.
import { computed, ref } from 'vue'
import Dialog from '../ui/Dialog.vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import LiveTerminalSheet from '../live/LiveTerminalSheet.vue'

// Takes the session row rather than two unpacked strings, and owns its own
// reachability gate: whether there is a pane to look at is this component's
// question, and answering it here keeps the host's template out of it.
const props = defineProps({
  session: { type: Object, default: null },
})

const open = ref(false)
// The pane id, not `bridge_reachable`: the server reports an SDK-owned run as
// reachable (it can be typed into over the SDK channel) while giving it a null
// pane, and there is no tmux screen behind such a run to capture.
const reachable = computed(() => !!props.session?.bridge_pane)
</script>

<template>
  <Dialog
    v-if="reachable"
    v-model:open="open"
    wide
    title="Raw terminal"
    description="One-shot capture of the session's tmux pane. Read-only — no keys are sent; open the live terminal to type into it."
  >
    <template #trigger>
      <!-- A host that has a better target than a button — a whole status row —
           supplies it here; the default is the header's labelled button. -->
      <slot name="trigger">
      <!-- The label IS the affordance: no tooltip, because a control whose
           purpose only appears on hover is one most readers never discover. -->
      <Button
        variant="ghost"
        size="sm"
        class="term-cta"
        data-testid="trace-terminal-btn"
      >
        <Icon name="terminal" :size="13" />
        open terminal
      </Button>
      </slot>
    </template>
    <!-- Mounted only while open so each open re-captures: the sheet fetches
         once on mount, and a pane held in the DOM would show a frozen frame
         from whenever the page happened to load. -->
    <LiveTerminalSheet
      v-if="open"
      :session-id="session?.trace_id || ''"
      :bridge-pane="session?.bridge_pane || ''"
    />
  </Dialog>
</template>
