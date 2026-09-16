<script setup>
// The composer's two run controls: cancel the turn in flight, and end the
// session outright. Both routes are tier-agnostic by design, so this component
// never has to know whether it is looking at a session regin launched over the
// Agent SDK or a terminal it merely traces through tmux:
//
//   bridge-interrupt → a typed interrupt for an SDK run, the Escape a human
//                      would press for a pane; the session stays open.
//   shutdown         → agent_sdk.stop_run for an SDK run, Escape + `/exit` +
//                      a watch for the pane to actually go quiet for tmux.
//
// Cancel is one tap; Stop arms first. The asymmetry is the point — a cancel is
// what you reach for when the agent is already off down the wrong path, and a
// confirm there is friction in exactly the wrong place, whereas Stop ends the
// very session this composer exists to write into.
import { ref, computed, onUnmounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
})

// The two routes do NOT share a success contract, and the difference is not
// cosmetic: `shutdown` answers {tier, live, closed, interrupted, detail} and
// carries no `delivered` key at all, so a component that tests for one reads
// every successful stop as a red failure. Each action therefore states its own
// reading of "it took" — the same split `useLiveShutdown.js` makes for the
// trace list's Close.
const CANCEL = {
  path: 'bridge-interrupt',
  pending: 'cancelling',
  done: 'cancelled',
  noun: 'cancel',
  took: (res) => res.delivered === true,
  say: (res) => res.detail,
}
const STOP = {
  path: 'shutdown',
  pending: 'stopping',
  done: 'stopped',
  noun: 'stop',
  // A null tier means nothing was holding the session by the time the server
  // looked — it ended between this bar opening and the click. Nothing failed,
  // so reporting it as a refusal would be a false alarm on a clean close. The
  // strictness is load-bearing: `api.post` answers {ok:false, msg} on a non-2xx,
  // where `tier` is undefined, and a loose `== null` would paint a 500 green.
  //
  // `undetermined` is the other way a null tier arises: the registry named a
  // pane regin then failed to read, so the agent may still be running and was
  // certainly never touched. That is the one null tier that is NOT a close.
  took: (res) => res.closed === true
    || (res.tier === null && res.undetermined !== true),
  // …but the server's sentence for a clean null tier ("no manageable tier holds
  // this session") is worded as a refusal, and a tick on it reads as a tick on
  // nothing having happened. Say what is true for the operator instead.
  say: (res) => (res.closed ? res.detail : 'already stopped'),
}

// ready | cancelling | arming | stopping | cancelled | stopped | failed
const phase = ref('ready')
const detail = ref('')
let resetTimer = null
let alive = true

const busy = computed(() => phase.value === 'cancelling' || phase.value === 'stopping')

function clearResetTimer() {
  if (resetTimer) { clearTimeout(resetTimer); resetTimer = null }
}

// A successful Stop unmounts this component on the next poll (the session is
// no longer followable), so the awaited request routinely outlives it. Teardown
// alone cannot cover that: `onUnmounted` has already run by the time the
// continuation sets its timer, so the flag is what stops it arming one.
onUnmounted(() => { alive = false; clearResetTimer() })

function reset() {
  clearResetTimer()
  detail.value = ''
  phase.value = 'ready'
}

// An armed confirm that is walked away from must not stay armed: a stray later
// click would then end the agent with no intent behind it.
function arm() {
  clearResetTimer()
  detail.value = ''
  phase.value = 'arming'
  resetTimer = setTimeout(reset, 6000)
}

async function run(act) {
  if (busy.value) return
  clearResetTimer()
  phase.value = act.pending
  let res = null
  try {
    res = await api.post(`/sessions/${props.sessionId}/${act.path}`, {})
  } catch { res = null }
  if (!alive) return
  if (res && act.took(res)) {
    phase.value = act.done
    detail.value = act.say(res) || act.done
    // Back to buttons only for a cancel: nothing about it is terminal and a
    // second one is a legitimate thing to want, whereas a stopped session has
    // no next state this bar can offer.
    if (act === CANCEL) resetTimer = setTimeout(reset, 3000)
  } else {
    // A refusal is the informative case — "no reachable session", "bridge
    // disabled", or a tmux quit that did not land after the turn was already
    // cancelled — is what the operator needs to read, not a silent no-op.
    phase.value = 'failed'
    detail.value = res?.detail || res?.msg || `${act.noun} failed`
  }
}

const cancel = () => run(CANCEL)
const stop = () => run(STOP)
</script>

<template>
  <div class="composer-run" data-testid="composer-run-controls">
    <template v-if="phase === 'ready'">
      <Button
        variant="ghost"
        size="sm"
        class="composer-run-btn"
        aria-label="Cancel the turn the agent is running"
        title="Cancel this turn — the session stays open for the next prompt"
        data-testid="composer-cancel"
        @click="cancel"
      ><Icon name="square" :size="12" /><span class="composer-run-label">Cancel</span></Button>
      <Button
        variant="ghost"
        size="sm"
        class="composer-run-btn is-stop"
        aria-label="Stop this agent session"
        title="End this session — the agent process is shut down"
        data-testid="composer-stop-arm"
        @click="arm"
      ><Icon name="power" :size="13" /><span class="composer-run-label">Stop</span></Button>
    </template>

    <template v-else-if="phase === 'arming'">
      <span class="composer-run-ask">Stop the agent?</span>
      <Button
        variant="ghost"
        size="sm"
        class="composer-run-btn is-stop"
        data-testid="composer-stop-confirm"
        @click="stop"
      >End it</Button>
      <Button
        variant="ghost"
        size="sm"
        class="composer-run-btn"
        data-testid="composer-stop-keep"
        @click="reset"
      >Keep</Button>
    </template>

    <template v-else-if="busy">
      <span class="composer-run-spinner" aria-hidden="true"></span>
      <span class="composer-run-meta">{{ phase === 'cancelling' ? 'cancelling…' : 'stopping…' }}</span>
    </template>

    <template v-else>
      <span
        class="composer-run-meta"
        :class="{ 'is-failed': phase === 'failed' }"
        data-testid="composer-run-detail"
      >{{ phase === 'failed' ? `✗ ${detail}` : `✓ ${detail}` }}</span>
      <!-- A refusal must not be terminal: a dropped request, or a session this
           process briefly couldn't reach, would otherwise leave the operator
           with no way back to the buttons short of reloading the view. -->
      <Button
        v-if="phase === 'failed'"
        variant="ghost"
        size="sm"
        class="composer-run-btn"
        data-testid="composer-run-retry"
        @click="reset"
      >Try again</Button>
    </template>
  </div>
</template>

<style scoped>
.composer-run {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  min-width: 0;
  /* Never shrink below content. As a shrinkable flex child this box was
     squeezed to 40px against 137px of buttons on a 375px phone, and the
     overflow painted straight over the Send button beside it — a collision
     no pane-level overflow check can see, because the PANE never overflows. */
  flex: 0 0 auto;
}

/* …but only while it holds BUTTONS. The text-bearing states hold a server
   sentence, and there the message is what should give: unshrinkable, a refusal
   ("no reachable bridge pane for this session; tmux target …") pushed the whole
   composer panel 404px past its own right edge instead of ellipsizing, clipping
   the one line the operator needed to read. */
.composer-run:has(.composer-run-meta),
.composer-run:has(.composer-run-ask) {
  flex: 0 1 auto;
  min-width: 0;
}

.composer-run-btn {
  /* Whole, in either state: it is the prose beside them that absorbs the
     deficit when the row runs short. */
  flex: 0 0 auto;
  height: 1.75rem;
  gap: 0.25rem;
  padding: 0 0.5rem;
  border-radius: 0.5rem;
  color: var(--color-fg-muted);
  font-size: 11.5px;
  font-weight: 600;
}

.composer-run-btn.is-stop:hover {
  color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 10%, transparent);
}

.composer-run-ask {
  padding-left: 0.25rem;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--color-fg);
  white-space: nowrap;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.composer-run-meta {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 10.5px;
  color: var(--color-fg-muted);
  white-space: nowrap;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.composer-run-meta.is-failed { color: var(--color-danger); }

.composer-run-spinner {
  width: 10px;
  height: 10px;
  flex-shrink: 0;
  border-radius: 9999px;
  border: 1.5px solid color-mix(in srgb, var(--color-fg-muted) 35%, transparent);
  border-top-color: var(--color-fg-muted);
  animation: composer-run-spin 700ms linear infinite;
}

@keyframes composer-run-spin { to { transform: rotate(360deg); } }

@media (prefers-reduced-motion: reduce) {
  .composer-run-spinner { animation-duration: 2.4s; }
}

/* A narrow composer panel has room for the two glyphs but not their words. The
   accessible name rides `aria-label`, which is what a screen reader was reading
   anyway. Keyed to the panel's width (the container `FollowUpComposer` names),
   not the viewport's: at 768px the panel is ~400px and needs this just as much
   as a phone does. */
@container composer (max-width: 520px) {
  .composer-run-label { display: none; }
  .composer-run-btn { padding: 0 0.4375rem; }
  .composer-run-ask { font-size: 11px; }
}
</style>
